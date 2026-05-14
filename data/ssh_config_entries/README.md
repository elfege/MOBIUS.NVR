# SSH-config mirror — operator-vetted .ssh/config staging area

Phase 2c v2 (2026-05-13).

This directory is the bind-mount source for `/data/ssh_config_entries`
inside the `unified-nvr` container. The
`/api/host-agent/install-via-ssh` endpoint writes one file per successful
install, named `<host_label>.conf`, each containing a minimal:

```
Host <label>
  HostName <ip-or-dns>
  User <unix-user>
```

stanza. The host-side script `scripts/sync_ssh_config_entries.sh` reads
this directory on the next `start.sh` and (currently) **dry-runs** the
merge into the operator's `~/.ssh/config` — it prints what would be
appended but does not touch the file.

The "real" merge is deliberately deferred until the operator has vetted
the dry-run output for at least one full kiosk-install cycle. After
vetting, flip the `// FUTURE: enable real merge after vetting` block in
both `start.sh` and `scripts/sync_ssh_config_entries.sh`.

## Safety properties

- One file per host_label — no shared-monolith write contention.
- Atomic-rename writes from the container (write to `*.conf.tmp`, then
  `os.replace`), so a partial write never corrupts an entry.
- The merge script must NEVER overwrite an existing `Host <alias>` block
  in `~/.ssh/config` — it skips by detecting `Host <label>` already
  present.
- The container only writes files matching the label/host/user validation
  regexes from `routes/host_agent_install_ssh.py`; no path traversal
  reachable from the route.
