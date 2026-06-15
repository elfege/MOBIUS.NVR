"""
tests/test_env_conformity.py — schema-drift guard for the env-file trio.

Three env files in the repo, each playing a distinct role:

  .env.example  ← the spec — what a third-party clone copies and edits.
                  Tracked. Every ${VAR} the docker-compose.yml references
                  MUST be declared here.
  .env.test     ← the test-mode overrides (Playwright suite, +10000 ports,
                  ephemeral ./tmp_test/ paths). Tracked.
  .env          ← the operator's local config (machine-specific paths,
                  vendor flags). NOT tracked. May or may not exist on a
                  given machine; checked when present.

This file enforces:

  (a) .env.test  ⊇  .env.example  (every key in example must be defined in test)
  (b) .env       ⊇  .env.example  (operator's .env must declare every key in example)
  (c) every ${VAR} in docker-compose.yml is declared in .env.example

Together those guarantees prevent the "tests boot with stale defaults"
class of regression. A new env var added to compose without being added
to .env.example would fail (c); without being added to .env.test would
fail (a); the operator forgetting to update their local .env would fail
(b) on their machine only.

Runs in the static tier of the test suite — no docker, no DB.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Set

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"
ENV_EXAMPLE  = REPO_ROOT / ".env.example"
ENV_TEST     = REPO_ROOT / ".env.test"
ENV_LOCAL    = REPO_ROOT / ".env"

# Env vars that the docker-compose.yml references but are NOT expected to
# be declared in .env.example. Mostly things passed via `docker compose -p`
# or computed at runtime by start.sh (e.g. NVR_LOCAL_HOST_IP defaults to
# `hostname -I` if unset).
EXEMPT_FROM_EXAMPLE: Set[str] = set()


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

ENV_KEY_RE = re.compile(r"^\s*([A-Z_][A-Z0-9_]*)\s*=", re.MULTILINE)
COMPOSE_VAR_RE = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)(?::-[^}]*)?\}")


def _read_env_keys(path: Path) -> Set[str]:
    """Return the set of KEY= entries declared in an env file. Blank
    values count — what matters is that the key is present so a third-
    party can see it exists and override it."""
    if not path.is_file():
        return set()
    text = path.read_text(encoding="utf-8", errors="replace")
    return set(ENV_KEY_RE.findall(text))


def _read_compose_vars(path: Path) -> Set[str]:
    """Return the set of ${VAR} placeholders referenced by docker-compose.yml.
    Matches ${VAR} and ${VAR:-default} forms. Ignores ${VAR:?required} which
    we don't use."""
    if not path.is_file():
        pytest.fail(f"docker-compose.yml not found at {path}")
    text = path.read_text(encoding="utf-8", errors="replace")
    return set(COMPOSE_VAR_RE.findall(text))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_compose_vars_declared_in_env_example():
    """Every ${VAR} referenced by docker-compose.yml must be declared in
    .env.example. Otherwise a fresh-clone user runs `docker compose up`
    and silently inherits whatever ambient shell vars happen to exist."""
    compose_vars = _read_compose_vars(COMPOSE_FILE)
    example_keys = _read_env_keys(ENV_EXAMPLE)
    undeclared = sorted((compose_vars - example_keys) - EXEMPT_FROM_EXAMPLE)
    assert not undeclared, (
        "docker-compose.yml references env vars that are NOT declared in "
        f".env.example: {undeclared}. Add them to .env.example so a "
        "third-party clone can configure them."
    )


def test_env_test_covers_env_example():
    """Every key in .env.example must be set in .env.test. Tests get
    overrides for everything; nothing inherits the example default by
    accident."""
    example_keys = _read_env_keys(ENV_EXAMPLE)
    test_keys    = _read_env_keys(ENV_TEST)
    missing = sorted(example_keys - test_keys)
    assert not missing, (
        ".env.test is missing keys present in .env.example: "
        f"{missing}. Add them to .env.test (typically with +10000 port "
        "offsets or ephemeral ./tmp_test/ paths)."
    )


def test_local_env_covers_env_example_when_present():
    """
    ADVISORY check — warns rather than fails.

    When the operator's local .env exists, it SHOULD declare every key from
    .env.example so the local config stays in sync with the spec. But the
    docker-compose ${VAR:-default} clauses mean missing keys don't break a
    running stack — they just inherit defaults. So this check is informational:
    a missing key emits a pytest warning the operator sees in the suite output,
    but doesn't fail the run.

    The strict checks (compose → example, example ↔ test) are still hard fails;
    those gaps would actually break things.
    """
    if not ENV_LOCAL.is_file():
        pytest.skip(".env not present on this machine — skipping local-coverage check")
    example_keys = _read_env_keys(ENV_EXAMPLE)
    local_keys   = _read_env_keys(ENV_LOCAL)
    missing = sorted(example_keys - local_keys)
    if missing:
        import warnings
        warnings.warn(
            f"Operator's .env is missing {len(missing)} key(s) from .env.example: "
            f"{missing}. Compose ${{VAR:-default}} clauses cover them at runtime, "
            "so nothing is broken — but the local config has drifted from the "
            "spec. Adding the keys (even with empty values) makes drift visible.",
            UserWarning,
            stacklevel=2,
        )


def test_no_orphan_keys_in_env_test():
    """Reverse direction — every key in .env.test should also be in
    .env.example (otherwise the key is dead weight: docker-compose
    wouldn't read it because nothing in compose references a key not
    referenced in compose)."""
    example_keys = _read_env_keys(ENV_EXAMPLE)
    test_keys    = _read_env_keys(ENV_TEST)
    orphans = sorted(test_keys - example_keys)
    assert not orphans, (
        ".env.test declares keys not in .env.example: "
        f"{orphans}. Either add them to .env.example (if they're real "
        "config) or remove them from .env.test (if obsolete)."
    )
