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

*Last updated: February 7, 2026 23:10 EST*

Branch: `user_auth_and_settings_FEB_07_2026_c`

**Previous Session Completed (Feb 2-7):**

- Camera selector modal feature (show/hide cameras, HD/SD toggle)
- Fixed Hubitat environment variables (Hub 4 suffix)
- Fixed pointer-events blocking PTZ and mobile touch
- Mobile UX improvements (gestures, header control)

See last ~100 lines of `docs/README_project_history.md` for full details.

Always read `CLAUDE.md` in case I updated it in between sessions.

---

## Current Session: February 7, 2026 (23:10+ EST)

**New Feature:** User Authentication & Per-User Settings

**Requirements:**

1. **User Authentication:**
   - Simple bcrypt-based auth (Auth0/Google/Facebook later)
   - Session: indefinite until logout
   - Two roles: `admin` and `user`
   - Admin can create users, user cannot
   - All users can change name and password
   - Default: admin/admin with forced password change on first login

2. **Per-User Stream Type Preferences:**
   - Allow users to swap stream types (MJPEG, WebRTC, HLS, LL_HLS)
   - Save as defaults in user's related table
   - M2M schema needed (users ↔ cameras ↔ stream_types)

**Planning Phase:**

- Need to assess current database state (PostgreSQL)
- Determine: Are we using JSON or database as source of truth?
- Design schema for users, sessions, and per-user camera preferences

**Next Steps:**

1. Explore current database schema and configuration storage
2. Design user authentication system
3. Design per-user settings schema (M2M relationships)
4. Plan implementation approach

---

## TODO List

**New Feature (User Auth):**

- [ ] Explore current database schema and usage
- [ ] Design user authentication tables
- [ ] Design per-user settings tables (M2M)
- [ ] Implement bcrypt auth endpoints
- [ ] Implement login/logout UI
- [ ] Implement user management UI (admin only)
- [ ] Implement per-user stream type preferences
- [ ] Add session management

**Previous Testing Needed:**

- [ ] Test camera selector modal on mobile (iPhone/iPad)
- [ ] Test PTZ controls after pointer-events fix
- [ ] Test stream tap-to-expand on mobile
- [ ] Test HD toggle switches stream quality

---
