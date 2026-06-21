"""
tests/regression/test_test_stack_isolation_go2rtc_config_dir.py
=================================================================

Regression ledger entry — the test stack's go2rtc container must NEVER
share its `/config` bind mount with the production stack's go2rtc
container. Without per-stack isolation the test stack inherits prod's
`eufy://` + `onvif://` stream definitions and silently opens real
P2P / ONVIF sessions on the operator's live cameras — breaking Rule 11
(1 camera = 1 input) every time the test stack is up.

Bug (2026-06-21):
    Operator caught it after ~4 days of Hot Tub + Terrace Shed
    (both Eufy, both single-consumer P2P) failing. Bringing the test
    stack down (`docker compose -p nvr_test down`) immediately
    restored those cameras' P2P sessions. Root cause: docker-compose.yml
    hardcoded `/dev/shm/nvr-go2rtc:/config` for both stacks, and
    scripts/generate_streaming_configs.py wrote to the same path. The
    test stack's go2rtc happily read production's config and connected
    to the real cameras.

Fix:
    Three coordinated changes:
      - docker-compose.yml: go2rtc volume parameterized via
        `${NVR_GO2RTC_CONFIG_DIR:-/dev/shm/nvr-go2rtc}:/config`
      - scripts/generate_streaming_configs.py: reads
        `os.environ.get('NVR_GO2RTC_CONFIG_DIR', ...)`, no hardcoded path
      - .env.test: sets `NVR_GO2RTC_CONFIG_DIR=/dev/shm/nvr_test-go2rtc`
        so the test stack uses its own dir

    All three must stay in sync. This regression test locks each one
    individually so a future refactor that breaks ONE of them fires
    immediately (not weeks later when an operator notices cameras dying).

Guard (static — no live stack):
    Parse the three files. Assert each contains the parameterized form,
    NOT the hardcoded literal.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.regression._ledger import entry_for, format_failure_context


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
COMPOSE = REPO_ROOT / "docker-compose.yml"
GEN_SCRIPT = REPO_ROOT / "scripts" / "generate_streaming_configs.py"
ENV_TEST = REPO_ROOT / ".env.test"
_LEDGER_CONTEXT = format_failure_context(entry_for(__file__))

PROD_DEFAULT = "/dev/shm/nvr-go2rtc"


def test_compose_go2rtc_volume_uses_env_var():
    """The go2rtc service's `volumes:` block must reference
    `${NVR_GO2RTC_CONFIG_DIR...}`, not a hardcoded path. If reverted,
    test + prod share the same dir and the parasitic-session bug
    returns."""
    assert COMPOSE.is_file(), f"{COMPOSE} not found"
    src = COMPOSE.read_text(encoding="utf-8")
    # Find the go2rtc service block.
    m = re.search(r"^  go2rtc:\s*\n((?:    .*\n)+)", src, re.MULTILINE)
    assert m, "Could not locate `go2rtc:` service in docker-compose.yml"
    block = m.group(1)
    # Inside the block, the /config mount must reference the env var.
    # A hardcoded `/dev/shm/nvr-go2rtc:/config` line is the regression.
    assert "${NVR_GO2RTC_CONFIG_DIR" in block, (
        f"docker-compose.yml go2rtc service no longer references "
        f"${{NVR_GO2RTC_CONFIG_DIR...}} — the volume mount has reverted "
        f"to a hardcoded path. Test stack will now read production's "
        f"go2rtc.yaml and open real P2P sessions.\n\n{_LEDGER_CONTEXT}"
    )
    assert f"- {PROD_DEFAULT}:/config" not in block, (
        f"docker-compose.yml go2rtc service mounts `{PROD_DEFAULT}:/config` "
        f"as a HARDCODED literal — the test stack will share this with prod "
        f"and re-open the parasitic-session bug.\n\n{_LEDGER_CONTEXT}"
    )


def test_generate_streaming_configs_reads_env_var():
    """scripts/generate_streaming_configs.py must read the go2rtc
    config dir from the env var, not a hardcoded literal. If reverted,
    the test stack's startup overwrites the prod config (and vice versa)
    even with per-stack volume mounts."""
    assert GEN_SCRIPT.is_file(), f"{GEN_SCRIPT} not found"
    src = GEN_SCRIPT.read_text(encoding="utf-8")
    # Look inside the generate_go2rtc_config function body.
    m = re.search(
        r"def generate_go2rtc_config\([^)]*\):(.+?)(?=\ndef |\Z)",
        src,
        re.DOTALL,
    )
    assert m, "Could not locate generate_go2rtc_config function"
    body = m.group(1)
    assert "NVR_GO2RTC_CONFIG_DIR" in body, (
        f"generate_go2rtc_config no longer references NVR_GO2RTC_CONFIG_DIR — "
        f"the writer has reverted to a hardcoded path. Even if compose mounts "
        f"separately, this script will keep overwriting the prod dir for both "
        f"stacks.\n\n{_LEDGER_CONTEXT}"
    )
    # And the hardcoded literal must NOT be the value of an assignment.
    # A re-introduction would look like: shm_dir = '/dev/shm/nvr-go2rtc'
    hardcode_re = re.compile(
        r"^\s*shm_dir\s*=\s*['\"]" + re.escape(PROD_DEFAULT) + r"['\"]",
        re.MULTILINE,
    )
    assert not hardcode_re.search(body), (
        f"generate_go2rtc_config contains `shm_dir = '{PROD_DEFAULT}'` "
        f"as a hardcoded assignment. The env-var read has been bypassed "
        f"by a literal.\n\n{_LEDGER_CONTEXT}"
    )


def test_env_test_overrides_go2rtc_config_dir():
    """.env.test must set NVR_GO2RTC_CONFIG_DIR to a value DIFFERENT
    from production's default. If it falls back to the default, both
    stacks land in `/dev/shm/nvr-go2rtc` and the bug returns."""
    assert ENV_TEST.is_file(), f"{ENV_TEST} not found"
    src = ENV_TEST.read_text(encoding="utf-8")
    m = re.search(r"^NVR_GO2RTC_CONFIG_DIR\s*=\s*(.+?)\s*$", src, re.MULTILINE)
    assert m, (
        f".env.test does not set NVR_GO2RTC_CONFIG_DIR. The test stack will "
        f"fall back to the production default `{PROD_DEFAULT}` — sharing the "
        f"config dir with prod and re-opening the parasitic-session bug.\n\n"
        f"{_LEDGER_CONTEXT}"
    )
    value = m.group(1).strip().strip("'\"")
    assert value and value != PROD_DEFAULT, (
        f".env.test sets NVR_GO2RTC_CONFIG_DIR={value!r} which equals "
        f"production's default. Must be DIFFERENT (e.g. "
        f"`/dev/shm/nvr_test-go2rtc`) for stack isolation.\n\n{_LEDGER_CONTEXT}"
    )
