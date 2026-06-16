# MOBIUS.NVR — Test Suite

This directory is the project's executable spec. The structure mirrors what we test:

```
tests/
├── README.md                ← you are here
├── conftest.py              ← shared pytest config (no DB required)
├── test_audit_coverage.py   ← static SQL check: every audit-tracked table has a trigger
└── e2e/                     ← browser-driven end-to-end suite
    ├── conftest.py          ← stack-readiness check, DB helpers, fixtures
    └── test_auth_login.py   ← AUTH.LOGIN.OK + AUTH.LOGIN.WRONG_PASSWORD
```

## Two tiers

**Static / unit tier** (root of `tests/`) — runs without a running stack. Parses migration SQL as text, asserts schema invariants. Fast (~1 s).

**End-to-end tier** (`tests/e2e/`) — Playwright drives a real browser against a real docker-compose stack. Catches the kind of bugs static checks miss (CSRF interaction, UI rendering, full request/response cycles).

The two specs are written against [`docs/functionality_reference.md`](../docs/functionality_reference.md), which is the human-readable map of every user-visible feature. Each row in that doc carries a stable ID (e.g. `AUTH.LOGIN.OK`); each test references its ID in the docstring. Doc ↔ test stays in sync via the hard rule in `CLAUDE.md`.

## Running the static tier

```bash
pytest tests/test_audit_coverage.py
```

No setup required. Runs in CI-equivalent conditions every time.

## Running the E2E tier

The E2E suite uses the **same `docker-compose.yml` as production**, with `.env.test` providing test-mode overrides (port offsets, ephemeral paths, optional features off). Container names get a `nvr_test_` prefix from `-p nvr_test` so the test stack runs alongside a live prod NVR on the same host with zero collision.

```bash
# 1. Install test-only deps + the Chromium browser binary
pip install -r requirements-test.txt
playwright install --with-deps chromium

# 2. Bring the test stack up (postgres + postgrest + flask, all isolated)
docker compose -p nvr_test --env-file .env.test up -d --wait

# 3. Run the suite
pytest tests/e2e

# 4. When done
docker compose -p nvr_test --env-file .env.test down -v
```

The first run pulls the Postgres / PostgREST images and builds the app image (~2-3 min on a fresh machine). Subsequent runs reuse cached layers and start in ~30 s. Migrations apply automatically when Postgres initializes a fresh data directory (see [`psql/02-apply-migrations.sh`](../psql/02-apply-migrations.sh)).

A test fails fast with a helpful message if the stack isn't running — no opaque connection-refused traces.

### Why one compose file instead of a separate test compose

Earlier drafts of the suite shipped a parallel `docker-compose.test.yml`. The operator pushed back 2026-06-15: a separate test compose **drifts from prod** over time, so tests end up validating a snapshot rather than the current app. With one compose and two env-files, any change to the prod stack automatically applies to the test stack on the next bring-up. No drift.

The env-file conformity test ([`tests/test_env_conformity.py`](test_env_conformity.py)) is the static guard: it fails if any `${VAR}` in `docker-compose.yml` is missing from `.env.example` (third-party can't configure) or `.env.test` (tests run with stale defaults), and warns if the operator's local `.env` is missing keys when it exists.

## Phases (where this suite is heading)

The build-out follows the methodology in [`docs/plans/cross_platform_deployment_assessment_and_storage_selection_and_e2e_test_methodology_2026_06_15.md`](../docs/plans/cross_platform_deployment_assessment_and_storage_selection_and_e2e_test_methodology_2026_06_15.md) (operator-local; not in the public mirror).

- **Phase A (done, v6.4.0)** — Functionality reference skeleton at `docs/functionality_reference.md`. 121 rows, 21 surfaces.
- **Phase B (done, v6.5.x)** — Scaffold one runnable case. ← this file + `tests/e2e/test_auth_login.py`.
- **Phase C (done, v6.6.x)** — Regression test ledger. One case per documented past bug. Narrative-source-of-truth lives in [`tests/regression/ledger.yaml`](regression/ledger.yaml); browse the index with `pytest --regression-ledger`.
- **Phase D** — Backfill the rest of the reference doc, prioritized by user impact.
- **Phase E (done, v6.7.x)** — Local pre-commit hook at [`scripts/hooks/pre-commit`](../scripts/hooks/pre-commit). Runs `ruff check .` (F821 broad pyflakes net via [`ruff.toml`](../ruff.toml)) + the static pytest tier in ~1.5 s. Full e2e suite stays a manual-or-scheduled run. Install on fresh clone via `./scripts/hooks/install-hooks.sh`.

CI runners on GitHub Actions were considered and explicitly skipped (the operator prefers on-premises execution to avoid runner costs + secret exposure). The suite is built to be runnable by anyone who clones the repo and has Docker.

## How to add a new case

1. Find or add the matching row in `docs/functionality_reference.md`. The row's `ID` (e.g., `STORAGE.MIGRATE.MANUAL`) is the case's anchor.
2. Add a test under `tests/e2e/test_<surface>.py`. Reference the ID in the test docstring.
3. Reuse existing fixtures (`seed_test_admin`, `base_url`, `fresh_context`, `db_conn`). Add new fixtures to `tests/e2e/conftest.py` if you need shared setup across multiple cases.
4. If your case requires a camera stream, depend on the (future) `mock_rtsp_camera` fixture — coming in Phase C with `docker-compose.test.yml`'s mediamtx + test-pattern feeder.
5. Update the row's `Verified` column to `e2e:PASS` after the test lands.

## Why on-premises only

GitHub Actions on the dev repo would work (Linux runners can host docker-compose) but the operator runs this NVR as a real production system, so:

- The hardware to run the suite already exists.
- Pulling vendor images / Chromium repeatedly on free-tier runner minutes is wasteful when a local run is one command.
- Secrets (AWS credentials for camera credential bootstrap) don't need to leave the on-prem network.

A scheduled or pre-commit local run achieves the same coverage with zero CI-runner cost.
