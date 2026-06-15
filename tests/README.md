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

The E2E suite uses a parallel docker-compose stack on offset ports (`+10000` from prod) so it never collides with a running production NVR on the same host.

```bash
# 1. Install test-only deps + the Chromium browser binary
pip install -r requirements-test.txt
playwright install --with-deps chromium

# 2. Bring the test stack up (postgres + postgrest + flask, all isolated)
docker compose -f docker-compose.test.yml up -d --wait

# 3. Run the suite
pytest tests/e2e

# 4. When done
docker compose -f docker-compose.test.yml down -v
```

The first run pulls the Postgres / PostgREST images and builds the app image (~2-3 min on a fresh machine). Subsequent runs reuse cached layers and start in ~30 s.

A test fails fast with a helpful message if the stack isn't running — no opaque connection-refused traces.

## Phases (where this suite is heading)

The build-out follows the methodology in [`docs/plans/cross_platform_deployment_assessment_and_storage_selection_and_e2e_test_methodology_2026_06_15.md`](../docs/plans/cross_platform_deployment_assessment_and_storage_selection_and_e2e_test_methodology_2026_06_15.md) (operator-local; not in the public mirror).

- **Phase A (done, v6.4.0)** — Functionality reference skeleton at `docs/functionality_reference.md`. 121 rows, 21 surfaces.
- **Phase B (in progress)** — Scaffold one runnable case. ← this file + `tests/e2e/test_auth_login.py`.
- **Phase C** — Regression test ledger. One case per documented past bug (Eufy P2P expiry, MediaMTX API auth, snap-gate signal-lost regression, v6.2.x CSRF + render bugs, etc.).
- **Phase D** — Backfill the rest of the reference doc, prioritized by user impact.
- **Phase E** — Local pre-commit hook for the smoke subset; full suite stays a manual-or-scheduled run.

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
