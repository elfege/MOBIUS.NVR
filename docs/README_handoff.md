---
title: "Session Handoff Buffer"
layout: default
---

<!-- markdownlint-disable MD025 -->
<!-- markdownlint-disable MD024 -->
<!-- markdownlint-disable MD036 -->
<!-- markdownlint-disable MD060 -->

# Session Handoff Buffer

This file is updated after each file modification during a Claude Code session.
It serves as a buffer before content is transferred to `README_project_history.md`.

---

*Last updated: February 8, 2026 19:45 EST*

Branch: `stream_type_preferences_FEB_08_2026_a`

**Previous Session Completed (Feb 7-8):**

- Full user authentication system (bcrypt, Flask-Login, RLS)
- User management UI (admin CRUD)
- Per-user camera access control
- PostgREST resilience (psycopg2 fallback, RLock deadlock fix, JSON 401 handler)

See last ~100 lines of `docs/README_project_history.md` for full details (Feb 7-8 section).

Always read `CLAUDE.md` in case it was updated between sessions.

---

## TODO List

**Pending:**

- [ ] Implement per-user stream type preferences API
- [ ] Frontend: load user stream type preferences, allow live switching
- [ ] file_operations_log cleanup: 98M rows (34GB) needs retention/pruning policy
- [ ] VACUUM ANALYZE on recordings table (never been vacuumed)
- [ ] Security: Implement stricter RLS policies (currently permissive)
- [ ] WebRTC HD/SD fallback - falls back too fast, doesn't retry HD
- [ ] Add snapshot feature in fullscreen mode

---
