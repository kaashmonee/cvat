# Project Guidelines

## Code Style
- Python formatting uses Black + isort with line length 100; follow `pyproject.toml` (`[tool.black]`, `[tool.isort]`).
- `isort` intentionally skips `serverless/`; do not mass-reorder imports there unless required for the specific change.
- Frontend code is linted with ESLint per workspace package (`cvat-ui`, `cvat-core`, `cvat-canvas`, `cvat-canvas3d`, `cvat-data`).
- Pre-commit lint routing is path-based in `lint-staged.config.js`; keep edits scoped so only relevant package checks run.

## Architecture
- This is a monorepo: Django backend in `cvat/`, frontend packages in `cvat-ui/`, `cvat-core/`, `cvat-canvas/`, `cvat-canvas3d/`, `cvat-data/`.
- Backend domain apps live in `cvat/apps/` (e.g., `engine`, `iam`, `organizations`, `quality_control`, `webhooks`).
- Runtime is multi-service via Docker Compose: API/UI + Postgres + Redis/KV + workers + OPA + analytics services.
- Worker roles are split (`import`, `export`, `annotation`, `webhooks`, `quality_reports`, etc. in `docker-compose.yml`); async flows may span multiple containers.

## Build and Test
- Start core services from repo root: `docker compose up -d`.
- Install JS dependencies with Yarn Berry: `yarn --immutable` (see root `package.json` and `cvat-ui/README.md`).
- Run UI dev server: `yarn workspace cvat-ui run start` (or `yarn run start:cvat-ui`).
- Build packages from root as needed: `yarn run build:cvat-ui`, `yarn run build:cvat-core`, `yarn run build:cvat-canvas`, `yarn run build:cvat-canvas3d`, `yarn run build:cvat-data`.
- Run backend REST tests: `pytest ./tests/python` (see `tests/python/README.md`).
- Do not use root `yarn test` (placeholder script exits with error in root `package.json`).

## Project Conventions
- Use Yarn workspaces (not npm) for frontend/package tasks (`packageManager: yarn@4.9.2`).
- `cvat-ui` depends on local linked packages (`link:./../cvat-core`, `link:./../cvat-canvas`, etc.); validate cross-package type/API changes.
- Prefer compose overlays over ad-hoc edits for environment changes (`docker-compose.dev.yml`, `docker-compose.https.yml`, `docker-compose.external_db.yml`).
- Keep fixes surgical: avoid broad formatting or unrelated refactors across monorepo packages.

## Integration Points
- Serverless auto-annotation is enabled via compose overlay: `docker compose -f docker-compose.yml -f components/serverless/docker-compose.serverless.yml up -d`.
- Nuclio deployment scripts are in `serverless/deploy_cpu.sh` and `serverless/deploy_gpu.sh`; they expect local `nuctl` + Docker networking.
- Authorization policy service (`cvat_opa`) and analytics stack are wired through Compose; backend behavior can depend on these services.
- API URL for local UI development is `http://localhost:7000` (`cvat-ui/package.json`).

## Fusion Plugin (Side-by-Side 2D ↔ 3D Editor)
- Source lives in `cvat-ui/plugins/fusion/src/ts/`.
- Entry point: `index.tsx` registers `/fusion` and `/fusion/:projectId` routes as a CVAT plugin.
- `fusion-page.tsx` embeds two CVAT annotation editors side-by-side via `<iframe>` (`/tasks/{id}/jobs/{id}`).
- Supporting panels in `panels/`: `link-controls.tsx` (Link/Unlink/Save), `annotation-list.tsx` (linked pair table).
- Legacy view-only panels (`canvas2d-panel.tsx`, `canvas3d-panel.tsx`) remain in `panels/` but are no longer imported.
- Linking logic uses a `link_id` text attribute on labels (see `consts.ts`); link/unlink/save operate via cvat-core API.
- Demo overlay: `docker-compose.fusion-demo.yml` + `utils/fusion_demo_seed.py` seeds 2D task #1 and 3D task #2.

### Running the Fusion Demo
1. Build images (suppress proxy for UI build if on corporate network):
   ```
   http_proxy= https_proxy= docker compose -f docker-compose.yml -f docker-compose.dev.yml build cvat_server cvat_ui
   ```
2. Deploy with demo seed:
   ```
   docker compose -f docker-compose.yml -f docker-compose.dev.yml -f docker-compose.fusion-demo.yml up -d
   ```
3. Open `http://localhost:8080/fusion?task2d=1&task3d=2` (login: admin / admin).
4. Each panel is a full CVAT editor — draw, edit, delete annotations normally.
5. Click **Refresh Annotations** to sync the link panel after editing in the iframes.

### Build Notes (Corporate Proxy)
- Docker build args `http_proxy`/`https_proxy` are passed via `docker-compose.dev.yml`; they inherit from shell env.
- If ffmpeg download fails during server build, pre-download `ffmpeg-8.0.tar.gz` to repo root — the Dockerfile COPYs it instead of curling.
- `docker-compose.fusion-demo.yml` clears proxy env vars so the init container can reach `cvat_server` on the Docker network.
- `~/.docker/config.json` may need a `proxies` section for BuildKit stages to access the internet.
- Use `gitp` (not `git`) for pushing to remote.

### X-Frame-Options
- `cvat-ui/react_nginx.conf` and `cvat/nginx.conf` set `X-Frame-Options: SAMEORIGIN` to allow same-origin iframe embedding.
- Django's `XFrameOptionsMiddleware` still sends `DENY` on API responses; this doesn't affect iframes since they load HTML pages served by the UI nginx.

## Security
- Follow `SECURITY.md`: only latest release is supported for vulnerability reports.
- Report vulnerabilities to `secure@cvat.ai` with affected versions and repro details.
- For external DB deployments, use secret-based DB password flow (`CVAT_POSTGRES_PASSWORD_FILE` in `docker-compose.external_db.yml`).
- HTTPS setup requires `ACME_EMAIL` and Traefik TLS settings (`docker-compose.https.yml`).
- Treat permissive local defaults (e.g., `ALLOWED_HOSTS: '*'` in `docker-compose.yml`) as development-only.