#!/usr/bin/env python3
# Copyright (C) CVAT.ai Corporation
#
# SPDX-License-Identifier: MIT
"""
Fusion Demo Seed — creates sample 2D + 3D tasks with linked annotations.

Designed to run inside the cvat_fusion_demo_init container (mounted by
docker-compose.fusion-demo.yml), but can also be invoked from the host
for testing or CI.

Usage (container — automatic via compose):
    python3 /opt/fusion-demo/seed.py

Usage (host — manual):
    python3 utils/fusion_demo_seed.py --host http://localhost:8080

Environment variables:
    CVAT_DEMO_USER  (default: admin)
    CVAT_DEMO_PASS  (default: admin)
    CVAT_HOST       (default: localhost, controls printed URLs only)
"""
from __future__ import annotations

import argparse
import io
import logging
import os
import random
import struct
import sys
import tempfile
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests
from PIL import Image, ImageDraw

logger = logging.getLogger("fusion-demo")

# ── Configuration ────────────────────────────────────────────────────────

DEFAULT_HOST = "http://cvat-server:8080"
DEFAULT_USER = "admin"
DEFAULT_PASS = "admin"

LABEL_SCHEMA: list[dict] = [
    {
        "name": "car",
        "color": "#ff6037",
        "attributes": [
            {
                "name": "link_id",
                "input_type": "text",
                "mutable": True,
                "default_value": "",
                "values": [],
            }
        ],
    },
    {
        "name": "pedestrian",
        "color": "#00bfff",
        "attributes": [
            {
                "name": "link_id",
                "input_type": "text",
                "mutable": True,
                "default_value": "",
                "values": [],
            }
        ],
    },
]

ANNOTATIONS_2D: list[dict] = [
    {
        "type": "rectangle",
        "frame": 0,
        "points": [120, 80, 320, 240],
        "occluded": False,
        "z_order": 0,
        "label": "car",
        "link_id": "link-car-001",
    },
    {
        "type": "rectangle",
        "frame": 0,
        "points": [450, 150, 550, 380],
        "occluded": False,
        "z_order": 0,
        "label": "pedestrian",
        "link_id": "link-ped-001",
    },
    {
        "type": "rectangle",
        "frame": 0,
        "points": [600, 200, 750, 350],
        "occluded": False,
        "z_order": 0,
        "label": "car",
        "link_id": "",
    },
]

ANNOTATIONS_3D: list[dict] = [
    {
        "type": "cuboid",
        "frame": 0,
        "points": [2.0, 3.0, 0.5, 0.0, 0.0, 0.1, 3.0, 2.0, 1.5, 0, 0, 0, 0, 0, 0, 0],
        "occluded": False,
        "z_order": 0,
        "label": "car",
        "link_id": "link-car-001",
    },
    {
        "type": "cuboid",
        "frame": 0,
        "points": [-4.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.8, 0.8, 1.8, 0, 0, 0, 0, 0, 0, 0],
        "occluded": False,
        "z_order": 0,
        "label": "pedestrian",
        "link_id": "link-ped-001",
    },
    {
        "type": "cuboid",
        "frame": 0,
        "points": [6.0, -2.0, 0.3, 0.0, 0.0, 0.5, 4.0, 2.0, 1.6, 0, 0, 0, 0, 0, 0, 0],
        "occluded": False,
        "z_order": 0,
        "label": "car",
        "link_id": "",
    },
]


# ── Data classes ─────────────────────────────────────────────────────────


@dataclass
class LabelInfo:
    label_id: int
    link_id_attr_id: int


@dataclass
class SeedResult:
    """Returned by seed() for verification and display."""

    task_2d_id: int = 0
    task_3d_id: int = 0
    job_2d_id: int = 0
    job_3d_id: int = 0
    annotations_2d_count: int = 0
    annotations_3d_count: int = 0
    skipped: bool = False


# ── CVAT API client ─────────────────────────────────────────────────────


class CVATClient:
    """Minimal CVAT REST API wrapper using requests.Session."""

    def __init__(self, host: str, username: str, password: str):
        self.base = host.rstrip("/")
        self.api = f"{self.base}/api"
        self.session = requests.Session()
        self.session.auth = (username, password)

    def wait_for_server(self, timeout: int = 240, interval: int = 2) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                r = self.session.get(f"{self.api}/server/about", timeout=5)
                if r.ok:
                    logger.info("Server is up.")
                    return
            except requests.ConnectionError:
                pass
            time.sleep(interval)
        raise RuntimeError(f"CVAT server not reachable after {timeout}s")

    def ensure_superuser(self, username: str, password: str) -> None:
        r = self.session.get(f"{self.api}/users/self")
        if r.status_code == 200:
            logger.info("Authenticated as %s.", username)
            return
        # If running inside the server container, create via Django ORM
        logger.info("Creating superuser '%s' via Django ORM …", username)
        try:
            os.environ.setdefault("DJANGO_SETTINGS_MODULE", "cvat.settings.production")
            import django

            django.setup()
            from django.contrib.auth.models import User

            if not User.objects.filter(username=username).exists():
                User.objects.create_superuser(username, "admin@localhost", password)
                logger.info("Superuser created.")
            else:
                u = User.objects.get(username=username)
                u.set_password(password)
                u.save()
                logger.info("Password reset for existing user.")
        except ImportError:
            raise RuntimeError(
                f"Cannot authenticate as {username} and Django ORM is not available. "
                "Make sure the user exists or run this inside the server container."
            )
        time.sleep(3)
        r = self.session.get(f"{self.api}/users/self")
        if r.status_code != 200:
            raise RuntimeError(f"Still cannot authenticate (HTTP {r.status_code})")
        logger.info("Authenticated as %s.", username)

    def find_task_by_name(self, name: str) -> int | None:
        r = self.session.get(f"{self.api}/tasks", params={"search": name})
        r.raise_for_status()
        results = r.json().get("results", [])
        return results[0]["id"] if results else None

    def create_task(self, name: str, labels: list[dict]) -> int:
        r = self.session.post(
            f"{self.api}/tasks",
            json={"name": name, "labels": labels},
        )
        r.raise_for_status()
        task_id = r.json()["id"]
        logger.info("Created task '%s' → id=%d", name, task_id)
        return task_id

    def upload_task_data(self, task_id: int, files: list[Path], image_quality: int = 70) -> None:
        multipart = []
        for i, fpath in enumerate(files):
            multipart.append(
                (f"client_files[{i}]", (fpath.name, open(fpath, "rb")))
            )
        multipart.append(("image_quality", (None, str(image_quality))))
        r = self.session.post(f"{self.api}/tasks/{task_id}/data", files=multipart)
        r.raise_for_status()

    def wait_for_task(self, task_id: int, timeout: int = 60) -> int:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            r = self.session.get(f"{self.api}/tasks/{task_id}")
            r.raise_for_status()
            size = r.json().get("size", 0)
            if size > 0:
                return size
            time.sleep(1)
        raise RuntimeError(f"Task {task_id} not ready after {timeout}s")

    def get_labels(self, task_id: int) -> dict[str, LabelInfo]:
        r = self.session.get(f"{self.api}/labels", params={"task_id": task_id})
        r.raise_for_status()
        result: dict[str, LabelInfo] = {}
        for label in r.json()["results"]:
            for attr in label.get("attributes", []):
                if attr["name"] == "link_id":
                    result[label["name"]] = LabelInfo(
                        label_id=label["id"],
                        link_id_attr_id=attr["id"],
                    )
        return result

    def get_job_id(self, task_id: int) -> int:
        r = self.session.get(f"{self.api}/jobs", params={"task_id": task_id})
        r.raise_for_status()
        return r.json()["results"][0]["id"]

    def put_annotations(self, job_id: int, shapes: list[dict]) -> None:
        r = self.session.put(
            f"{self.api}/jobs/{job_id}/annotations",
            json={"shapes": shapes, "tags": [], "tracks": []},
        )
        r.raise_for_status()

    def get_annotations(self, job_id: int) -> dict:
        r = self.session.get(f"{self.api}/jobs/{job_id}/annotations")
        r.raise_for_status()
        return r.json()


# ── Sample data generation ───────────────────────────────────────────────


def generate_2d_images(directory: Path, count: int = 5) -> list[Path]:
    """Generate simple synthetic JPEG images."""
    rng = random.Random(42)
    paths = []
    for i in range(count):
        img = Image.new("RGB", (800, 600), (30 + i * 20, 50 + i * 10, 80 + i * 15))
        draw = ImageDraw.Draw(img)
        for _ in range(3):
            x, y = rng.randint(50, 600), rng.randint(50, 400)
            draw.rectangle([x, y, x + 80, y + 60], outline="white", width=2)
        path = directory / f"image_{i:03d}.jpg"
        img.save(path)
        paths.append(path)
    logger.info("Generated %d sample images.", count)
    return paths


def generate_point_cloud_zip(directory: Path, num_points: int = 5000) -> Path:
    """Generate a binary PCD file and wrap it in a ZIP."""
    rng = random.Random(42)
    header = (
        f"# .PCD v0.7 - Point Cloud Data file format\n"
        f"VERSION 0.7\n"
        f"FIELDS x y z rgb\n"
        f"SIZE 4 4 4 4\n"
        f"TYPE F F F U\n"
        f"COUNT 1 1 1 1\n"
        f"WIDTH {num_points}\n"
        f"HEIGHT 1\n"
        f"VIEWPOINT 0 0 0 1 0 0 0\n"
        f"POINTS {num_points}\n"
        f"DATA binary\n"
    )
    pcd_buf = io.BytesIO()
    pcd_buf.write(header.encode())
    for _ in range(num_points):
        x = rng.uniform(-10, 10)
        y = rng.uniform(-10, 10)
        z = rng.uniform(-2, 2)
        r, g, b = rng.randint(50, 255), rng.randint(50, 255), rng.randint(50, 255)
        rgb_packed = struct.pack("I", (r << 16) | (g << 8) | b)
        pcd_buf.write(struct.pack("fff", x, y, z) + rgb_packed)

    zip_path = directory / "pointcloud.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("pointcloud.pcd", pcd_buf.getvalue())
    logger.info("Generated PCD with %d points.", num_points)
    return zip_path


# ── Annotation helpers ───────────────────────────────────────────────────


def build_shapes(
    templates: list[dict],
    labels: dict[str, LabelInfo],
) -> list[dict]:
    """Convert annotation templates to API-ready shape dicts with resolved IDs."""
    shapes = []
    for t in templates:
        info = labels[t["label"]]
        shapes.append(
            {
                "type": t["type"],
                "frame": t["frame"],
                "points": t["points"],
                "occluded": t["occluded"],
                "z_order": t["z_order"],
                "label_id": info.label_id,
                "attributes": [
                    {"spec_id": info.link_id_attr_id, "value": t["link_id"]},
                ],
            }
        )
    return shapes


# ── Main seed logic ──────────────────────────────────────────────────────


def seed(
    host: str = DEFAULT_HOST,
    username: str = DEFAULT_USER,
    password: str = DEFAULT_PASS,
) -> SeedResult:
    """
    Create demo 2D + 3D tasks with linked annotations.

    Returns a SeedResult with task/job IDs and counts.
    Idempotent: skips if the demo tasks already exist.
    """
    client = CVATClient(host, username, password)
    result = SeedResult()

    # Wait for server
    client.wait_for_server()

    # Ensure auth
    client.ensure_superuser(username, password)

    # Idempotency check
    existing_2d = client.find_task_by_name("Fusion Demo - 2D Camera")
    if existing_2d is not None:
        existing_3d = client.find_task_by_name("Fusion Demo - 3D LiDAR")
        logger.info(
            "Demo tasks already exist (2D=%s, 3D=%s). Skipping.",
            existing_2d,
            existing_3d,
        )
        result.task_2d_id = existing_2d
        result.task_3d_id = existing_3d or 0
        result.skipped = True
        return result

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        # Generate data
        images = generate_2d_images(tmp)
        pcd_zip = generate_point_cloud_zip(tmp)

        # Create 2D task
        result.task_2d_id = client.create_task("Fusion Demo - 2D Camera", LABEL_SCHEMA)
        client.upload_task_data(result.task_2d_id, images)
        size_2d = client.wait_for_task(result.task_2d_id)
        logger.info("2D task ready: id=%d (%d frames).", result.task_2d_id, size_2d)

        # Create 3D task
        result.task_3d_id = client.create_task("Fusion Demo - 3D LiDAR", LABEL_SCHEMA)
        client.upload_task_data(result.task_3d_id, [pcd_zip])
        size_3d = client.wait_for_task(result.task_3d_id)
        logger.info("3D task ready: id=%d (%d frames).", result.task_3d_id, size_3d)

    # Resolve labels
    labels_2d = client.get_labels(result.task_2d_id)
    labels_3d = client.get_labels(result.task_3d_id)

    # Get job IDs
    result.job_2d_id = client.get_job_id(result.task_2d_id)
    result.job_3d_id = client.get_job_id(result.task_3d_id)

    # Create annotations
    shapes_2d = build_shapes(ANNOTATIONS_2D, labels_2d)
    client.put_annotations(result.job_2d_id, shapes_2d)
    result.annotations_2d_count = len(shapes_2d)
    logger.info("Created %d 2D annotations.", len(shapes_2d))

    shapes_3d = build_shapes(ANNOTATIONS_3D, labels_3d)
    client.put_annotations(result.job_3d_id, shapes_3d)
    result.annotations_3d_count = len(shapes_3d)
    logger.info("Created %d 3D annotations.", len(shapes_3d))

    return result


# ── Verification ─────────────────────────────────────────────────────────


def verify(host: str, username: str, password: str, result: SeedResult) -> bool:
    """Verify the seeded data is correct. Returns True if all checks pass."""
    client = CVATClient(host, username, password)
    ok = True

    # Check 2D task has frames
    r = client.session.get(f"{client.api}/tasks/{result.task_2d_id}")
    if r.ok and r.json().get("size", 0) > 0:
        logger.info("✓ 2D task has %d frames.", r.json()["size"])
    else:
        logger.warning("✗ 2D task has no frames.")
        ok = False

    # Check 3D task has frames
    r = client.session.get(f"{client.api}/tasks/{result.task_3d_id}")
    if r.ok and r.json().get("size", 0) > 0:
        logger.info("✓ 3D task has %d frames.", r.json()["size"])
    else:
        logger.warning("✗ 3D task has no frames.")
        ok = False

    # Check 2D annotations
    ann_2d = client.get_annotations(result.job_2d_id)
    count_2d = len(ann_2d.get("shapes", []))
    if count_2d >= 3:
        logger.info("✓ 2D job has %d annotations.", count_2d)
    else:
        logger.warning("✗ Expected ≥3 2D annotations, got %d.", count_2d)
        ok = False

    # Check 3D annotations
    ann_3d = client.get_annotations(result.job_3d_id)
    count_3d = len(ann_3d.get("shapes", []))
    if count_3d >= 3:
        logger.info("✓ 3D job has %d annotations.", count_3d)
    else:
        logger.warning("✗ Expected ≥3 3D annotations, got %d.", count_3d)
        ok = False

    # Check link_ids exist
    link_ids = set()
    for s in ann_2d.get("shapes", []):
        for a in s.get("attributes", []):
            if a.get("value"):
                link_ids.add(a["value"])
    if "link-car-001" in link_ids and "link-ped-001" in link_ids:
        logger.info("✓ link_ids found: %s", link_ids)
    else:
        logger.warning("✗ Expected link-car-001 and link-ped-001, got %s.", link_ids)
        ok = False

    return ok


# ── CLI entry point ──────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed the CVAT Fusion demo.")
    parser.add_argument(
        "--host",
        default=os.environ.get("CVAT_HOST_URL", DEFAULT_HOST),
        help="CVAT base URL (default: %(default)s)",
    )
    parser.add_argument("--username", default=os.environ.get("CVAT_DEMO_USER", DEFAULT_USER))
    parser.add_argument("--password", default=os.environ.get("CVAT_DEMO_PASS", DEFAULT_PASS))
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Skip seeding, only verify existing data.",
    )
    parser.add_argument(
        "--task2d-id",
        type=int,
        default=0,
        help="For --verify-only: 2D task ID.",
    )
    parser.add_argument(
        "--task3d-id",
        type=int,
        default=0,
        help="For --verify-only: 3D task ID.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="[fusion-demo] %(message)s",
    )

    public_host = os.environ.get("CVAT_HOST", "localhost")

    try:
        if args.verify_only:
            if not args.task2d_id or not args.task3d_id:
                parser.error("--verify-only requires --task2d-id and --task3d-id")
            result = SeedResult(task_2d_id=args.task2d_id, task_3d_id=args.task3d_id)
            client = CVATClient(args.host, args.username, args.password)
            result.job_2d_id = client.get_job_id(result.task_2d_id)
            result.job_3d_id = client.get_job_id(result.task_3d_id)
            ok = verify(args.host, args.username, args.password, result)
            return 0 if ok else 1

        result = seed(args.host, args.username, args.password)

        if result.skipped:
            print(f"\n  Editor: http://{public_host}:8080/fusion"
                  f"?task2d={result.task_2d_id}&task3d={result.task_3d_id}\n")
            return 0

        ok = verify(args.host, args.username, args.password, result)

        print()
        print("=" * 60)
        print("  Fusion Demo Ready!")
        print("=" * 60)
        print(f"  2D Task    : {result.task_2d_id}  (job {result.job_2d_id})")
        print(f"  3D Task    : {result.task_3d_id}  (job {result.job_3d_id})")
        print(f"  Annotations: {result.annotations_2d_count} 2D, "
              f"{result.annotations_3d_count} 3D")
        print()
        print(f"  Credentials: {args.username} / {args.password}")
        print()
        print(f"  Fusion Editor:")
        print(f"  http://{public_host}:8080/fusion"
              f"?task2d={result.task_2d_id}&task3d={result.task_3d_id}")
        print("=" * 60)

        return 0 if ok else 1

    except Exception:
        logger.exception("Seed failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
