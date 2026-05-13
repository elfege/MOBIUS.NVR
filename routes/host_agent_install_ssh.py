"""
routes/host_agent_install_ssh.py — Phase 2c v2.

Adds a second install path alongside the existing "paste this curl into
the kiosk terminal" flow (host_agent_install.py): the SERVER SSHes into
the target machine and runs the install one-liner directly, streaming
the SSH output back to the operator's browser via Server-Sent Events.

Why a separate blueprint from host_agent_install.py:
    The print-only `/api/host-agent/install-command` is a small JSON
    endpoint. The SSH path is a long-lived streaming endpoint that
    needs to fork an `ssh` subprocess, handle password retries, scrub
    secrets, and yield text/event-stream chunks. Keeping it in its own
    file preserves the small-and-readable shape of the original
    blueprint and isolates the password-handling code path.

Endpoint:

  POST /api/host-agent/install-via-ssh
      body: { label, target_host, ssh_user, ssh_password? }
      auth: Bearer NVR_API_TOKEN (or LAN fallback when token is unset)
      returns: text/event-stream
         data: <line of ssh output>\\n\\n   (repeated)
         event: end
         data: {"exit_code": N, "ok": true|false[, "auth_error": true]}\\n\\n

Auth-error handling:
    If ssh fails with a credential-related error (banner contains
    'Permission denied', exit 255, no password supplied), the stream
    emits an `event: auth_required` line so the frontend can prompt
    for a password and retry without ever logging the password.

Secret hygiene:
    Passwords are NEVER written to logs, journalctl, or persisted to
    disk. They're passed to sshpass via -d <fd> (file-descriptor mode)
    so they don't appear in /proc/<pid>/cmdline. The local Python
    variable holding the password is overwritten with a zero-length
    string immediately after the subprocess is launched. (Python's GC
    eventually frees the original string; we cannot reliably mlock or
    bzero from CPython, so this is best-effort.)
"""
from __future__ import annotations

import json
import logging
import os
import re
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Optional

from flask import Blueprint, Response, request
from flask_login import current_user

logger = logging.getLogger(__name__)

host_agent_install_ssh_bp = Blueprint("host_agent_install_ssh", __name__)

# --------------------------------------------------------------------------
# SSH-config mirror directory (operator request 2026-05-13).
#
# When the server successfully installs the host-agent on a target via
# SSH, we record a minimal ssh stanza into this directory keyed by
# host_label. Each file is a self-contained `Host <label>` block — one
# stanza per file so future writes are atomic (replace the file, never
# rewrite a multi-stanza monolith).
#
# A companion host-side script (`scripts/sync_ssh_config_entries.sh`)
# reads this directory on the next `start.sh` run and DRY-RUNS the merge
# into the operator's ~/.ssh/config. The merge does NOT happen
# automatically yet — operator wants to vet behavior before letting it
# touch their .ssh/config (see start.sh "FUTURE: enable real merge").
#
# Bind-mounted in docker-compose.yml as:
#     ${NVR_SSH_CONFIG_ENTRIES_PATH:-./data/ssh_config_entries}
#         : /data/ssh_config_entries
# so the host can see what the container wrote.
# --------------------------------------------------------------------------
_SSH_CONFIG_ENTRIES_DIR = Path(
    os.environ.get("NVR_SSH_CONFIG_ENTRIES_DIR", "/data/ssh_config_entries")
)

# Reuse the same auth posture as routes/host_agent_install.py.
_API_TOKEN = os.environ.get("NVR_API_TOKEN", "").strip()

# Mirror the label regex enforced server-side elsewhere so we fail fast
# before we shell out to ssh.
_LABEL_RE = re.compile(r"^[a-z][a-z0-9_-]*$")
_LABEL_MAX_LEN = 64

# Restrict target_host / ssh_user to safe shell characters. We never
# `bash -c` the values, but we pass them as separate argv elements to
# ssh, so the only practical risk is `target_host` shaped like an ssh
# option (`-oProxyCommand=...`). Force a leading alnum to prevent that.
_HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_USER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9._-]*$")
_HOST_MAX = 255
_USER_MAX = 64


def _check_bearer() -> bool:
    """Same posture as host_agent_install._check_bearer."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer ") and _API_TOKEN:
        return auth[7:] == _API_TOKEN
    if not _API_TOKEN:
        ip = (
            request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
            or request.remote_addr
            or ""
        )
        return ip.startswith(("10.", "172.", "192.168.", "127.")) or ip == "::1"
    return False


def _is_authenticated() -> bool:
    """True if session is logged in OR a valid bearer token was supplied."""
    try:
        if current_user and current_user.is_authenticated:
            return True
    except Exception:
        pass
    return _check_bearer()


def _nvr_base_url() -> str:
    """Same logic as host_agent_install._nvr_base_url."""
    override = os.environ.get("NVR_PUBLIC_URL", "").strip()
    if override:
        return override.rstrip("/")
    return (request.host_url or "").rstrip("/")


# --------------------------------------------------------------------------
# SSE framing helpers — keep encoding centralized so the auth_required /
# end-of-stream contract stays consistent and easy to evolve.
# --------------------------------------------------------------------------
def _sse_data(payload: str) -> str:
    """Encode a single SSE `data:` frame. Newlines are split into multiple
    data: lines per the EventSource spec."""
    safe = payload.replace("\r\n", "\n").replace("\r", "\n")
    return "".join(f"data: {line}\n" for line in safe.split("\n")) + "\n"


def _sse_event(name: str, payload: Any) -> str:
    """Encode a named SSE event with a JSON-serialized payload."""
    return f"event: {name}\n{_sse_data(json.dumps(payload))}"


def _build_ssh_argv(
    target_host: str,
    ssh_user: str,
    install_url: str,
    use_sshpass: bool,
) -> list[str]:
    """
    Compose the ssh argv vector.

    - StrictHostKeyChecking=accept-new keeps fresh hosts non-interactive
      while still rejecting fingerprint mismatches on revisit.
    - BatchMode=yes when we're NOT using sshpass forces ssh to fail fast
      on password prompts instead of hanging. With sshpass we WANT the
      password prompt, so BatchMode is omitted.
    - ConnectTimeout=10 keeps the operator from staring at a hung tab
      when the target machine is off the LAN.
    - We DO NOT use `bash -c` on the remote: the install URL is piped
      directly into bash, and the install URL has already been built
      from validated label/token on the server side.
    """
    # We always invoke curl on the remote and pipe to bash. The single
    # remote command is one argv element to ssh — ssh will reassemble it
    # with the user's login shell on the far end.
    remote_cmd = f"curl -sSLk {shlex.quote(install_url)} | bash"

    argv: list[str] = []
    if use_sshpass:
        # -d <fd>: read password from file descriptor 3 (we'll wire that
        # in via pass_fds). Avoids cmdline exposure and avoids a tempfile.
        argv.extend(["sshpass", "-d3"])
    argv.extend([
        "ssh",
        "-o", "ConnectTimeout=10",
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "ServerAliveInterval=15",
        "-o", "ServerAliveCountMax=4",
    ])
    if not use_sshpass:
        # Without a password we want fast-fail on auth prompts. With a
        # password sshpass is feeding the prompt, so BatchMode would
        # actively prevent that.
        argv.extend(["-o", "BatchMode=yes"])
    # PreferredAuthentications: when sshpass is in play, pubkey alone is
    # useless — we WANT password to be tried. When no password, prefer
    # pubkey-only so a misconfigured server doesn't drop us into an
    # interactive prompt.
    if use_sshpass:
        argv.extend(["-o", "PreferredAuthentications=password,keyboard-interactive"])
    else:
        argv.extend(["-o", "PreferredAuthentications=publickey"])

    argv.append(f"{ssh_user}@{target_host}")
    argv.append(remote_cmd)
    return argv


def _write_ssh_config_mirror_entry(
    label: str, target_host: str, ssh_user: str
) -> Optional[str]:
    """
    Upsert a minimal `Host <label>` stanza into the mirror directory.

    Returns the absolute path of the written file on success, or None on
    failure (failure is non-fatal — the SSH install already succeeded by
    the time we reach this point, and a failed mirror write must never
    abort the operator's session). We log failures at WARNING level.

    The on-disk format is intentionally a minimal three-line stanza that
    `scripts/sync_ssh_config_entries.sh` can re-emit verbatim. We never
    include `IdentityFile` or other host-specific keys here; the merge
    script is supposed to be a side-effect-free DRY-RUN in v2 and the
    operator vets each entry before enabling real merge.

    Atomic write: we write to <label>.conf.tmp and rename, so a partial
    write from a crashed process never leaves a corrupt stanza behind.
    """
    # Defensive validation duplicated from the route — never trust the
    # caller frame even when it's our own code.
    if not _LABEL_RE.match(label) or len(label) > _LABEL_MAX_LEN:
        logger.warning("ssh_config_mirror: refusing invalid label %r", label)
        return None
    if not _HOST_RE.match(target_host) or len(target_host) > _HOST_MAX:
        logger.warning(
            "ssh_config_mirror: refusing invalid target_host %r", target_host
        )
        return None
    if not _USER_RE.match(ssh_user) or len(ssh_user) > _USER_MAX:
        logger.warning("ssh_config_mirror: refusing invalid ssh_user %r", ssh_user)
        return None

    try:
        _SSH_CONFIG_ENTRIES_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.warning(
            "ssh_config_mirror: cannot create %s: %s",
            _SSH_CONFIG_ENTRIES_DIR, e,
        )
        return None

    stanza = (
        f"# Generated by MOBIUS.NVR install-via-ssh on first successful "
        f"connect.\n"
        f"# Source of truth: routes/host_agent_install_ssh.py — do not edit "
        f"by hand,\n"
        f"# changes will be overwritten on the next successful install.\n"
        f"Host {label}\n"
        f"  HostName {target_host}\n"
        f"  User {ssh_user}\n"
    )

    target = _SSH_CONFIG_ENTRIES_DIR / f"{label}.conf"
    tmp = target.with_suffix(".conf.tmp")
    try:
        tmp.write_text(stanza, encoding="utf-8")
        # POSIX rename is atomic within the same filesystem — the mirror
        # dir is a bind-mount of a single host directory, so this holds.
        os.replace(tmp, target)
    except OSError as e:
        logger.warning(
            "ssh_config_mirror: write %s failed: %s", target, e,
        )
        # Best-effort cleanup of the tmp file so we don't leave litter.
        try:
            tmp.unlink(missing_ok=True)  # type: ignore[arg-type]
        except Exception:
            pass
        return None

    logger.info(
        "ssh_config_mirror: wrote %s (Host %s -> %s@%s)",
        target, label, ssh_user, target_host,
    )
    return str(target)


def _looks_like_auth_error(combined_output: str, exit_code: int) -> bool:
    """
    Heuristic detection of credential failures.

    ssh's exit code 255 is the canonical 'connection or auth failure'
    code. We further refine by looking for the standard 'Permission
    denied' banner and the sshpass-specific 'permission denied' wording.
    """
    if exit_code != 255:
        return False
    needles = (
        "Permission denied",
        "permission denied",
        "Authentication failed",
        "authentication failed",
        "no supported authentication methods",
    )
    return any(n in combined_output for n in needles)


@host_agent_install_ssh_bp.route(
    "/api/host-agent/install-via-ssh", methods=["POST"]
)
def install_via_ssh():
    """
    SSH into target_host as ssh_user and run the install one-liner.
    Streams ssh's combined stdout/stderr back as Server-Sent Events.

    Body (JSON):
        label         — host_label the agent should report under
        target_host   — DNS or IP of the target machine
        ssh_user      — Unix username on the target
        ssh_password? — optional; if absent we attempt pubkey-only auth.
                        On auth failure the SSE stream emits an
                        `auth_required` event and the client should
                        retry with the password filled in.

    Returns:
        text/event-stream of `data:` lines, terminated by an `end`
        event carrying {"exit_code": N, "ok": bool[, "auth_error": bool]}.
        4xx HTTP status when the request body is malformed.
    """
    if not _is_authenticated():
        return Response(
            "data: unauthorized\n\n",
            status=401,
            mimetype="text/event-stream",
        )

    # ── Validate body ───────────────────────────────────────────────────
    if not request.is_json:
        return Response(
            "data: bad request — expected application/json\n\n",
            status=400,
            mimetype="text/event-stream",
        )
    body = request.get_json(silent=True) or {}
    label = str(body.get("label") or "").strip()
    target_host = str(body.get("target_host") or "").strip()
    ssh_user = str(body.get("ssh_user") or "").strip()
    # IMPORTANT: keep the password reference local to this function so we
    # can overwrite it after spawning sshpass.
    ssh_password: Optional[str] = body.get("ssh_password")
    if ssh_password is not None:
        ssh_password = str(ssh_password)

    if not _LABEL_RE.match(label) or len(label) > _LABEL_MAX_LEN:
        return Response(
            "data: invalid label\n\n", status=400, mimetype="text/event-stream",
        )
    if not _HOST_RE.match(target_host) or len(target_host) > _HOST_MAX:
        return Response(
            "data: invalid target_host\n\n",
            status=400, mimetype="text/event-stream",
        )
    if not _USER_RE.match(ssh_user) or len(ssh_user) > _USER_MAX:
        return Response(
            "data: invalid ssh_user\n\n",
            status=400, mimetype="text/event-stream",
        )

    # The install URL we'll feed to the remote `curl | bash`. We sign it
    # with NVR_API_TOKEN so the remote curl is authenticated against our
    # /host-agent/install.sh route. Empty token = LAN-only dev path.
    server_url = _nvr_base_url()
    if _API_TOKEN:
        install_url = (
            f"{server_url}/host-agent/install.sh"
            f"?label={label}&token={_API_TOKEN}"
        )
    else:
        install_url = f"{server_url}/host-agent/install.sh?label={label}"

    use_sshpass = bool(ssh_password)

    # If we'll use sshpass, we need to feed the password via a private fd
    # to keep it out of /proc cmdline + environment listings. We create
    # an OS pipe here; the write end is closed in the parent after the
    # child reads, the read end is dup'd to fd 3 in the child via
    # pass_fds.
    pw_read_fd: Optional[int] = None
    pw_write_fd: Optional[int] = None
    if use_sshpass:
        pw_read_fd, pw_write_fd = os.pipe()
        try:
            # sshpass expects the password followed by a newline. Ignore
            # type complaints — ssh_password is non-None when use_sshpass.
            os.write(pw_write_fd, (ssh_password or "").encode("utf-8") + b"\n")
        finally:
            os.close(pw_write_fd)
            pw_write_fd = None

    argv = _build_ssh_argv(target_host, ssh_user, install_url, use_sshpass)

    # OVERWRITE the password reference before the subprocess fork — even
    # if something goes wrong below the variable name no longer holds the
    # cleartext. (CPython does not zero the underlying buffer; this is
    # mitigation, not erasure.)
    ssh_password = ""
    del ssh_password
    body.pop("ssh_password", None)

    # Log the action WITHOUT the password (and without the install_url's
    # token query-string — strip it for the journal).
    redacted_url = re.sub(r"token=[^&]+", "token=REDACTED", install_url)
    logger.info(
        "install-via-ssh: user=%s host=%s label=%s use_sshpass=%s url=%s",
        ssh_user, target_host, label, use_sshpass, redacted_url,
    )

    def generate():
        """SSE generator — yields data: frames as ssh produces output."""
        proc = None
        try:
            yield _sse_data(
                f">>> connecting to {ssh_user}@{target_host} ..."
            )
            popen_kwargs: Dict[str, Any] = dict(
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                bufsize=1,
                text=True,
                # Inherit the password fd into the child as fd 3.
                pass_fds=(pw_read_fd,) if pw_read_fd is not None else (),
            )
            try:
                proc = subprocess.Popen(argv, **popen_kwargs)
            finally:
                # The child has now duped its fd 3 from our pw_read_fd
                # (or we never opened one); close ours so EOF propagates
                # to sshpass and the secret leaves our address space.
                if pw_read_fd is not None:
                    try:
                        os.close(pw_read_fd)
                    except OSError:
                        pass

            collected = []
            assert proc.stdout is not None
            # Read line-by-line; popen with bufsize=1 + text=True gives us
            # newline-buffered iteration.
            for line in proc.stdout:
                stripped = line.rstrip("\n")
                collected.append(stripped)
                yield _sse_data(stripped)
                # Heartbeat-ish: a tiny sleep gives nginx a chance to
                # flush the chunk. Without it, very short installs can
                # finish before the proxy realizes the stream started.
                # We don't sleep on every line — just yield control.
                # (The proxy is configured with X-Accel-Buffering: no.)
            exit_code = proc.wait()
            combined = "\n".join(collected)
            auth_error = _looks_like_auth_error(combined, exit_code)

            if auth_error:
                # Hand the client a structured signal so the modal can
                # surface the password field. The stream still terminates
                # with an `end` event so the client's EventSource gets a
                # consistent close.
                yield _sse_event("auth_required", {
                    "message": "ssh authentication failed",
                    "use_sshpass_was": use_sshpass,
                })

            # ── SSH-config mirror (operator request 2026-05-13) ──────
            # On a successful install, record a minimal Host stanza in
            # the mirror directory so a future start.sh sync pass can
            # offer to add it to the operator's ~/.ssh/config. Failures
            # here are non-fatal — the install itself already succeeded.
            mirror_path: Optional[str] = None
            if exit_code == 0:
                try:
                    mirror_path = _write_ssh_config_mirror_entry(
                        label, target_host, ssh_user,
                    )
                    if mirror_path:
                        yield _sse_data(
                            f">>> recorded ssh stanza: {mirror_path} "
                            f"(will be offered by start.sh on next launch)"
                        )
                except Exception:
                    # Never let a mirror-write bug poison the success path.
                    logger.exception(
                        "ssh_config_mirror: unexpected error writing entry"
                    )

            yield _sse_event("end", {
                "exit_code": exit_code,
                "ok": exit_code == 0,
                "auth_error": auth_error,
                "ssh_config_mirror_path": mirror_path,
            })
        except FileNotFoundError as e:
            # ssh / sshpass not on PATH inside the container — surface a
            # clear message instead of the bare OSError text.
            yield _sse_data(f"!!! {e}")
            yield _sse_event("end", {"exit_code": 127, "ok": False, "auth_error": False})
        except Exception as e:
            logger.exception("install-via-ssh subprocess error")
            yield _sse_data(f"!!! unexpected error: {e}")
            yield _sse_event("end", {"exit_code": -1, "ok": False, "auth_error": False})
        finally:
            # Best-effort cleanup. If the client disconnects mid-stream
            # (GeneratorExit), we don't want a stranded ssh holding the
            # target's session open. SIGTERM gives ssh a chance to log
            # 'Connection closed' on the remote; SIGKILL as backstop.
            if proc is not None and proc.poll() is None:
                try:
                    proc.terminate()
                    for _ in range(20):
                        if proc.poll() is not None:
                            break
                        time.sleep(0.05)
                    if proc.poll() is None:
                        proc.kill()
                except Exception:
                    pass

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # nginx must NOT buffer SSE chunks
            "Connection": "keep-alive",
        },
    )
