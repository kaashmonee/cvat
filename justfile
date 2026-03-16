# ─────────────────────────────────────────────────────────────────────────
# CVAT Development Commands
#
# Run `just` with no arguments to see this list.
# Run `just <recipe>` to execute a command.
# ─────────────────────────────────────────────────────────────────────────

set dotenv-load := false

# Default: show available commands
default:
    @just --list --unsorted

# ── Docker Compose ───────────────────────────────────────────────────────

# Start core CVAT services (server, db, redis, workers, OPA, etc.)
up:
    docker compose up -d

# Stop all CVAT services
down:
    docker compose down

# Stop all services and delete volumes (full reset)
down-v:
    docker compose down -v

# Show running container status
ps:
    docker compose ps

# Tail logs for all services (Ctrl-C to stop)
logs *ARGS:
    docker compose logs -f {{ ARGS }}

# ── Build ────────────────────────────────────────────────────────────────

# Build server + UI images (dev overlay, suppresses proxy for clean builds)
build:
    http_proxy= https_proxy= docker compose -f docker-compose.yml -f docker-compose.dev.yml build cvat_server cvat_ui

# Build only the server image
build-server:
    http_proxy= https_proxy= docker compose -f docker-compose.yml -f docker-compose.dev.yml build cvat_server

# Build only the UI image
build-ui:
    http_proxy= https_proxy= docker compose -f docker-compose.yml -f docker-compose.dev.yml build cvat_ui

# Install JS dependencies (Yarn Berry, immutable lockfile)
yarn-install:
    yarn --immutable

# ── Frontend Dev ─────────────────────────────────────────────────────────

# Start UI dev server (hot-reload, points at localhost:7000)
ui-dev:
    yarn workspace cvat-ui run start

# Build the UI package (production bundle)
ui-build:
    yarn run build:cvat-ui

# Build cvat-core package
core-build:
    yarn run build:cvat-core

# Build cvat-canvas package
canvas-build:
    yarn run build:cvat-canvas

# Build cvat-canvas3d package
canvas3d-build:
    yarn run build:cvat-canvas3d

# Build cvat-data package
data-build:
    yarn run build:cvat-data

# Build all frontend packages
build-all-js: core-build data-build canvas-build canvas3d-build ui-build

# Lint UI source
ui-lint:
    cd cvat-ui && eslint './src/**/*.{ts,tsx}'

# Lint + auto-fix UI source
ui-lint-fix:
    cd cvat-ui && eslint './src/**/*.{ts,tsx}' --fix

# TypeScript type-check (no emit)
ui-typecheck:
    yarn workspace cvat-ui run type-check

# ── Backend / Python ─────────────────────────────────────────────────────

# Run backend REST API tests
test-backend:
    pytest ./tests/python

# Format Python code (black + isort)
fmt-python:
    bash dev/format_python_code.sh

# ── Fusion Demo ──────────────────────────────────────────────────────────

# Build + start CVAT with Fusion demo (2D + 3D sample tasks, auto-seeded)
fusion-demo: build
    docker compose -f docker-compose.yml -f docker-compose.dev.yml -f docker-compose.fusion-demo.yml up -d
    @echo ""
    @echo "Waiting for fusion demo init container …"
    @for i in $(seq 1 300); do \
        STATUS=$$(docker inspect -f '{{"{{"}}.State.Status{{"}}"}}' cvat_fusion_demo_init 2>/dev/null || echo "unknown"); \
        if [ "$$STATUS" = "exited" ]; then \
            EXIT=$$(docker inspect -f '{{"{{"}}.State.ExitCode{{"}}"}}' cvat_fusion_demo_init 2>/dev/null || echo "1"); \
            if [ "$$EXIT" = "0" ]; then \
                echo ""; \
                echo "✓ Fusion demo ready → http://localhost:8080/fusion?task2d=1&task3d=2"; \
                echo "  Login: admin / admin"; \
                exit 0; \
            else \
                echo "✗ Init container failed (exit $$EXIT). Check: just logs cvat_fusion_demo_init"; \
                exit 1; \
            fi; \
        fi; \
        sleep 1; \
    done; \
    echo "✗ Timed out waiting for init container"; \
    exit 1

# Tear down fusion demo (including volumes)
fusion-demo-down:
    docker compose -f docker-compose.yml -f docker-compose.dev.yml -f docker-compose.fusion-demo.yml down -v

# Run fusion demo seed tests (Python, via uv)
fusion-demo-test:
    cd utils && uv run pytest test_fusion_demo_seed.py -v

# ── Serverless / AI ──────────────────────────────────────────────────────

# Start CVAT with serverless auto-annotation support
serverless-up:
    docker compose -f docker-compose.yml -f components/serverless/docker-compose.serverless.yml up -d

# Deploy serverless functions (CPU)
serverless-deploy-cpu:
    bash serverless/deploy_cpu.sh

# Deploy serverless functions (GPU)
serverless-deploy-gpu:
    bash serverless/deploy_gpu.sh

# ── Cypress E2E Tests ────────────────────────────────────────────────────

# Run standard Cypress e2e tests (headless)
test-e2e:
    cd tests && npx cypress run --config-file cypress.config.js

# Run Cypress fusion tests (headless)
test-e2e-fusion:
    cd tests && npx cypress run --config-file cypress.fusion.config.js

# Run Canvas3D Cypress tests (headless)
test-e2e-canvas3d:
    cd tests && npx cypress run --config-file cypress_canvas3d.config.js

# Open Cypress interactive runner
test-e2e-open:
    cd tests && npx cypress open

# ── Utilities ────────────────────────────────────────────────────────────

# Check for changelog fragments (CI check)
check-changelog:
    python3 dev/check_changelog_fragments.py

# Open a shell inside the running server container
server-shell:
    docker compose exec cvat_server bash

# Open a Django management shell inside the server container
server-django-shell:
    docker compose exec cvat_server python3 manage.py shell

# Hit the API health check endpoint
api-health:
    @curl -sf http://localhost:8080/api/server/about | python3 -m json.tool

# Show git branch / status
status:
    @git --no-pager log --oneline -5
    @echo ""
    @git status -sb
