"""
tests/e2e/conftest.py — fixtures for the docker-compose-backed E2E suite.

The suite expects a running test stack on the unified prod compose file
with the test env overrides:

    docker compose -p nvr_test --env-file .env.test up -d --wait
    pytest tests/e2e

Operator directive 2026-06-15: tests run against the SAME docker-compose.yml
that prod uses, with a different env-file. No parallel docker-compose.test.yml
that would drift. Container names get a `nvr_test_` prefix from `-p nvr_test`;
all published ports are offset by +10000 so the test stack runs alongside
prod on the same host without collision.

Spinning the stack up + down is NOT a pytest fixture — the stack takes
~30s to come healthy and we don't want every `pytest` invocation to pay
that cost. Devs bring it up once, run the suite many times, tear it down.

What the fixtures DO provide:
  * `base_url` — the host:port the Flask app is reachable at
  * `db_conn` — a fresh psycopg2 connection to the test DB
  * `seed_test_admin` — INSERT an admin user the tests log in as
  * `fresh_context` — fresh Playwright context per test (no cookie leak)

Migrations run automatically when the test Postgres starts on a fresh
data dir (psql/02-apply-migrations.sh in /docker-entrypoint-initdb.d/).
There's no per-session apply_migrations fixture anymore — Postgres took
that job over.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import psycopg2
import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Defaults match .env.test (the test env-file). Override via E2E_* shell
# vars if a particular run needs to hit a different stack (e.g. a remote
# test box). Values track .env.test verbatim — keep in sync if you change
# either.
TEST_BASE_URL = os.getenv("E2E_BASE_URL", "http://127.0.0.1:15000")

TEST_DB = {
    "host":     os.getenv("E2E_DB_HOST",     "127.0.0.1"),
    "port":     int(os.getenv("E2E_DB_PORT", "15432")),
    "dbname":   os.getenv("E2E_DB_NAME",     "nvr"),
    "user":     os.getenv("E2E_DB_USER",     "nvr_api"),
    "password": os.getenv("E2E_DB_PASSWORD", "nvr_internal_db_key"),
}

# A known test admin — seeded once per session. NOT the real admin/admin
# baked into init-db.sql; this one has must_change_password=false so the
# test can land directly on /streams.
TEST_ADMIN_USERNAME = "e2e_admin"
TEST_ADMIN_PASSWORD = "e2e_admin_password"
# bcrypt hash of the above password (cost 12). Re-generate via:
#   python -c "import bcrypt; print(bcrypt.hashpw(b'e2e_admin_password', bcrypt.gensalt(12)).decode())"
TEST_ADMIN_BCRYPT = "$2b$12$Yp.UQI8Ny9R/Tr2lqJZ.0eNwKVi0g6Q7c7v1ZqQR1f4tT2gK1aOyW"


# ---------------------------------------------------------------------------
# Stack readiness check
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session", autouse=True)
def _wait_for_stack():
    """
    Fail fast with a helpful message if the test stack isn't running.

    Devs hitting `pytest tests/e2e` with a cold stack get a clear
    bring-up instruction instead of an opaque connection-refused trace.
    """
    deadline = time.monotonic() + 60
    last_err = None
    while time.monotonic() < deadline:
        try:
            with psycopg2.connect(connect_timeout=2, **TEST_DB) as c, c.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
            return
        except Exception as e:
            last_err = e
            time.sleep(1)
    pytest.fail(
        "E2E test stack is not reachable.\n\n"
        "Start it with:\n"
        "  docker compose -p nvr_test --env-file .env.test up -d --wait\n\n"
        f"Last connection error: {last_err}"
    )


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def db_conn():
    """One persistent connection for the whole session — session-scoped
    so the per-test cost is zero. Each test calls cursor() / commit() on
    it independently."""
    conn = psycopg2.connect(**TEST_DB)
    conn.autocommit = True
    yield conn
    conn.close()


@pytest.fixture(scope="session")
def seed_test_admin(db_conn):
    """
    Insert (or update) the e2e_admin user so login tests have a known
    identity. Returns (username, password) for the test to use.
    """
    with db_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO users (username, password_hash, role, must_change_password)
            VALUES (%s, %s, 'admin', false)
            ON CONFLICT (username)
            DO UPDATE SET password_hash = EXCLUDED.password_hash,
                          role = 'admin',
                          must_change_password = false
            """,
            (TEST_ADMIN_USERNAME, TEST_ADMIN_BCRYPT),
        )
    return TEST_ADMIN_USERNAME, TEST_ADMIN_PASSWORD


# ---------------------------------------------------------------------------
# Network / browser helpers
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def base_url() -> str:
    """The HTTP base the Flask app is reachable at from the test runner."""
    return TEST_BASE_URL


@pytest.fixture
def fresh_context(browser):
    """
    Per-test Playwright BrowserContext — fresh cookies, fresh localStorage.
    Replaces pytest-playwright's default `context` fixture for cases that
    want explicit isolation guarantees.
    """
    context = browser.new_context()
    yield context
    context.close()
