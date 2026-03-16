#!/usr/bin/env python3
# Copyright (C) CVAT.ai Corporation
#
# SPDX-License-Identifier: MIT
"""
Tests for the Fusion Demo seed script.

Runs without a live CVAT server — all HTTP calls are mocked.

Usage:
    python3 -m pytest utils/test_fusion_demo_seed.py -v
"""
from __future__ import annotations

import json
import struct
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from fusion_demo_seed import (
    ANNOTATIONS_2D,
    ANNOTATIONS_3D,
    LABEL_SCHEMA,
    CVATClient,
    LabelInfo,
    SeedResult,
    build_shapes,
    generate_2d_images,
    generate_point_cloud_zip,
    main,
    seed,
    verify,
)


# ── Data generation tests ────────────────────────────────────────────────


class TestGenerate2DImages:
    def test_creates_correct_number_of_images(self, tmp_path):
        paths = generate_2d_images(tmp_path, count=3)
        assert len(paths) == 3
        for p in paths:
            assert p.exists()
            assert p.suffix == ".jpg"

    def test_images_are_valid_jpeg(self, tmp_path):
        from PIL import Image

        paths = generate_2d_images(tmp_path, count=1)
        img = Image.open(paths[0])
        assert img.size == (800, 600)
        assert img.mode == "RGB"

    def test_deterministic_with_same_seed(self, tmp_path):
        (tmp_path / "a").mkdir()
        (tmp_path / "b").mkdir()
        p1 = generate_2d_images(tmp_path / "a", count=2)
        p2 = generate_2d_images(tmp_path / "b", count=2)
        for a, b in zip(p1, p2):
            assert a.read_bytes() == b.read_bytes()

    def test_default_count_is_five(self, tmp_path):
        paths = generate_2d_images(tmp_path)
        assert len(paths) == 5


class TestGeneratePointCloud:
    def test_creates_valid_zip_with_pcd(self, tmp_path):
        zip_path = generate_point_cloud_zip(tmp_path, num_points=100)
        assert zip_path.exists()
        with zipfile.ZipFile(zip_path) as zf:
            assert "pointcloud.pcd" in zf.namelist()

    def test_pcd_has_correct_header(self, tmp_path):
        zip_path = generate_point_cloud_zip(tmp_path, num_points=100)
        with zipfile.ZipFile(zip_path) as zf:
            data = zf.read("pointcloud.pcd")
        header_end = data.index(b"DATA binary\n") + len(b"DATA binary\n")
        header = data[:header_end].decode()
        assert "WIDTH 100" in header
        assert "POINTS 100" in header

    def test_pcd_binary_data_has_correct_size(self, tmp_path):
        n = 200
        zip_path = generate_point_cloud_zip(tmp_path, num_points=n)
        with zipfile.ZipFile(zip_path) as zf:
            data = zf.read("pointcloud.pcd")
        header_end = data.index(b"DATA binary\n") + len(b"DATA binary\n")
        binary_data = data[header_end:]
        # Each point: 3 floats (12 bytes) + 1 uint (4 bytes) = 16 bytes
        assert len(binary_data) == n * 16


# ── build_shapes tests ───────────────────────────────────────────────────


class TestBuildShapes:
    def test_resolves_label_and_attr_ids(self):
        labels = {
            "car": LabelInfo(label_id=10, link_id_attr_id=100),
            "pedestrian": LabelInfo(label_id=20, link_id_attr_id=200),
        }
        shapes = build_shapes(ANNOTATIONS_2D, labels)
        assert len(shapes) == 3

        assert shapes[0]["label_id"] == 10
        assert shapes[0]["attributes"][0]["spec_id"] == 100
        assert shapes[0]["attributes"][0]["value"] == "link-car-001"

        assert shapes[1]["label_id"] == 20
        assert shapes[1]["attributes"][0]["spec_id"] == 200
        assert shapes[1]["attributes"][0]["value"] == "link-ped-001"

        # Unlinked annotation
        assert shapes[2]["attributes"][0]["value"] == ""

    def test_preserves_shape_type_and_points(self):
        labels = {
            "car": LabelInfo(label_id=1, link_id_attr_id=2),
            "pedestrian": LabelInfo(label_id=3, link_id_attr_id=4),
        }
        shapes = build_shapes(ANNOTATIONS_3D, labels)
        assert shapes[0]["type"] == "cuboid"
        assert shapes[0]["points"][0] == 2.0


# ── CVATClient tests ─────────────────────────────────────────────────────


class TestCVATClient:
    def test_init_sets_base_and_auth(self):
        c = CVATClient("http://example.com:8080", "user", "pass")
        assert c.base == "http://example.com:8080"
        assert c.api == "http://example.com:8080/api"
        assert c.session.auth == ("user", "pass")

    def test_init_strips_trailing_slash(self):
        c = CVATClient("http://example.com:8080/", "u", "p")
        assert c.base == "http://example.com:8080"


class TestCVATClientWaitForServer:
    @patch("fusion_demo_seed.time.sleep")
    def test_returns_when_server_responds(self, mock_sleep):
        c = CVATClient("http://host", "u", "p")
        mock_resp = MagicMock()
        mock_resp.ok = True
        c.session.get = MagicMock(return_value=mock_resp)
        c.wait_for_server(timeout=5)
        c.session.get.assert_called()

    @patch("fusion_demo_seed.time.monotonic")
    @patch("fusion_demo_seed.time.sleep")
    def test_raises_on_timeout(self, mock_sleep, mock_monotonic):
        # First call: deadline = 0 + 5 = 5; second call: 0 < 5 (enter loop);
        # third call inside except: sleep; fourth call: 100 >= 5 (exit loop)
        mock_monotonic.side_effect = [0, 0, 100]
        c = CVATClient("http://host", "u", "p")
        c.session.get = MagicMock(side_effect=requests.ConnectionError)
        with pytest.raises(RuntimeError, match="not reachable"):
            c.wait_for_server(timeout=5)


class TestCVATClientFindTask:
    def test_returns_id_when_found(self):
        c = CVATClient("http://host", "u", "p")
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"results": [{"id": 42}]}
        c.session.get = MagicMock(return_value=mock_resp)
        assert c.find_task_by_name("test") == 42

    def test_returns_none_when_not_found(self):
        c = CVATClient("http://host", "u", "p")
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"results": []}
        c.session.get = MagicMock(return_value=mock_resp)
        assert c.find_task_by_name("test") is None


# ── SeedResult tests ─────────────────────────────────────────────────────


class TestSeedResult:
    def test_defaults(self):
        r = SeedResult()
        assert r.task_2d_id == 0
        assert r.task_3d_id == 0
        assert r.skipped is False

    def test_set_values(self):
        r = SeedResult(task_2d_id=1, task_3d_id=2, skipped=True)
        assert r.task_2d_id == 1
        assert r.skipped is True


# ── Label schema validation ──────────────────────────────────────────────


class TestLabelSchema:
    def test_has_two_labels(self):
        assert len(LABEL_SCHEMA) == 2

    def test_labels_have_link_id_attribute(self):
        for label in LABEL_SCHEMA:
            attrs = label["attributes"]
            link_attrs = [a for a in attrs if a["name"] == "link_id"]
            assert len(link_attrs) == 1, f"Label '{label['name']}' missing link_id attribute"
            assert link_attrs[0]["input_type"] == "text"

    def test_label_names(self):
        names = {l["name"] for l in LABEL_SCHEMA}
        assert names == {"car", "pedestrian"}


# ── Annotation template validation ──────────────────────────────────────


class TestAnnotationTemplates:
    def test_2d_annotations_are_rectangles(self):
        for a in ANNOTATIONS_2D:
            assert a["type"] == "rectangle"
            assert len(a["points"]) == 4

    def test_3d_annotations_are_cuboids(self):
        for a in ANNOTATIONS_3D:
            assert a["type"] == "cuboid"
            assert len(a["points"]) == 16

    def test_linked_annotations_share_ids(self):
        ids_2d = {a["link_id"] for a in ANNOTATIONS_2D if a["link_id"]}
        ids_3d = {a["link_id"] for a in ANNOTATIONS_3D if a["link_id"]}
        assert ids_2d == ids_3d
        assert "link-car-001" in ids_2d
        assert "link-ped-001" in ids_2d

    def test_each_set_has_one_unlinked(self):
        unlinked_2d = [a for a in ANNOTATIONS_2D if not a["link_id"]]
        unlinked_3d = [a for a in ANNOTATIONS_3D if not a["link_id"]]
        assert len(unlinked_2d) == 1
        assert len(unlinked_3d) == 1


# ── CLI tests ────────────────────────────────────────────────────────────


class TestCLI:
    @patch("fusion_demo_seed.seed")
    @patch("fusion_demo_seed.verify", return_value=True)
    def test_main_returns_zero_on_success(self, mock_verify, mock_seed):
        mock_seed.return_value = SeedResult(
            task_2d_id=1, task_3d_id=2, job_2d_id=1, job_3d_id=2,
            annotations_2d_count=3, annotations_3d_count=3,
        )
        assert main(["--host", "http://test:8080"]) == 0

    @patch("fusion_demo_seed.seed")
    def test_main_returns_one_on_exception(self, mock_seed):
        mock_seed.side_effect = RuntimeError("boom")
        assert main(["--host", "http://test:8080"]) == 1

    @patch("fusion_demo_seed.seed")
    @patch("fusion_demo_seed.verify", return_value=True)
    def test_main_skipped_prints_url(self, mock_verify, mock_seed, capsys):
        mock_seed.return_value = SeedResult(
            task_2d_id=5, task_3d_id=6, skipped=True,
        )
        ret = main(["--host", "http://test:8080"])
        assert ret == 0
        captured = capsys.readouterr()
        assert "task2d=5" in captured.out
        assert "task3d=6" in captured.out
