# Claude Code Instructions for NVR Project

## RULE 0: CRITICAL - At the start of EVERY message you write, verify

1. Have I checked if context compaction occurred? (phrase: "from a previous conversation that ran out of context")
2. If yes → Did I commit/push current work, create new branch with next suffix (_b,_c), update docs/README_handoff.md noting the compaction?
3. Have I read `/home/elfege/0_NVR/CLAUDE.md` for project-specific instructions?
4. Have I read: `docs/nvr_engineering_architecture.html`?
5. Read `~/0_CLAUDE_IC/user_profile_elfege.md` — persistent user profile (background, preferences, communication style). Never make the user re-explain his history.
6. Am I following ALL rules below? (Explicitly reference rule numbers when making decisions)
7. Am I violating RULE 0.6? Never add unrequested features without confirmation through a message.

---

## RULE 0.6: No Unrequested Features — HARD RULE

- **0.6.1** Never add unrequested features, no matter how relevant they might seem, without confirmation through a message (not a fucking prompt).

## RULE 0.7: Query Anamnesis Before Acting — HARD RULE

- **0.7.1** Before implementing anything non-trivial, query Anamnesis for relevant past episodes on the matter.
- **0.7.2** Query: `curl -s -X POST http://192.168.10.20:3010/api/episodes/search -H 'Content-Type: application/json' -d '{"query_text": "<topic>", "top_k": 5}'`
- **0.7.3** If relevant episodes exist, apply their lessons before proceeding.
- **0.7.4** This applies to: bug fixes, new features, UI changes, architectural decisions, debugging sessions.

---

## Project & User Context

**Project Purpose:**

- Personal learning project serving as training for professional work
- Part of portfolio demonstrating engineering capabilities

**User Background (Elfege):**

- Philosophy Ph.D. (Epistemology, Logic, Classical/Modern/Contemporary Philosophy)
- Software Engineer since 2022 (see elfege.com/pdf/resume)
- 18+ years teaching experience (Philosophy, Robotics)
- Open source contributor in smart home automation community
- Values understanding the "why" behind implementations
- Prefers step-by-step approach to maintain comprehension

---

## Server Specifications

**Host:** Dell PowerEdge R730xd (dellserver)

- **CPU:** 2x Intel Xeon E5-2690 v4 @ 2.60GHz (56 logical cores, 28 physical)
- **RAM:** 128GB
- **OS:** Ubuntu 24.04.3 LTS (WSL IP: 192.168.10.20)
- **Kernel:** Linux 6.8.0-85-generic

---

## NVR System Technical Overview

**Project Purpose:**
Multi-camera NVR (Network Video Recorder) system supporting:

**Camera Types:**

- Eufy, Reolink, UniFi, Amcrest, SV3C `[update this list if relevant]`

**Streaming Architecture:**

- WebRTC via MediaMTX WHEP (primary for most cameras)
- WebRTC via go2rtc (primary for Baichuan/Neolink cameras)
- LL-HLS via MediaMTX (iOS fallback when DTLS disabled)
- MJPEG (direct camera endpoint, budget cameras)
- Snapshot polling (iOS grid view, 1fps)

**Streaming Hub (per-camera):**

- `streaming_hub` field in DB `cameras` table determines which relay hub serves each camera
- `mediamtx` (default): Camera → FFmpeg → MediaMTX → all consumers
- `go2rtc`: Camera → go2rtc (single consumer) → WebRTC to browser, RTSP re-export to FFmpeg for recording
- Migration in progress: consolidating on go2rtc as the single streaming hub

**Motion Detection:**

- Reolink Baichuan (direct, not stream-dependent)
- ONVIF PullPoint
- FFmpeg scene detection (reads from streaming hub RTSP, not camera directly)

**Recording Types:**

- Motion-triggered
- Continuous
- Manual

**Recording Paths:**

*RECENT RECORDINGS:*

- `/mnt/sdc/NVR_Recent/motion:/recordings/motion`
- `/mnt/sdc/NVR_Recent/continuous:/recordings/continuous`
- `/mnt/sdc/NVR_Recent/snapshots:/recordings/snapshots`
- `/mnt/sdc/NVR_Recent/manual:/recordings/manual`
- `/mnt/sdc/NVR_Recent/buffer:/recordings/buffer`

*LONG TERM STORAGE:*

- `/mnt/THE_BIG_DRIVE/NVR_RECORDINGS/motion:/recordings/STORAGE/motion`
- `/mnt/THE_BIG_DRIVE/NVR_RECORDINGS/continuous:/recordings/STORAGE/continuous`
- `/mnt/THE_BIG_DRIVE/NVR_RECORDINGS/manual:/recordings/STORAGE/manual`
- `/mnt/THE_BIG_DRIVE/NVR_RECORDINGS/snapshots:/recordings/STORAGE/snapshots`

**Data Architecture — Camera Configuration:**

```
cameras.json (seed file, checked into git)
    │
    ▼  synced on startup by camera_config_sync.py
DB: cameras table (RUNTIME SOURCE OF TRUTH)
    │
    ▼  overridden per-user at runtime
DB: user_camera_preferences table (per-user overrides)
```

- **`cameras.json`** is a SEED FILE, not the runtime source of truth
- **DB `cameras` table** is what the app reads at runtime via `camera_repository.py`
- **`camera_config_sync.py`** copies cameras.json → DB on startup (DIRECT_FIELDS list)
- **`user_camera_preferences`** stores per-user overrides (stream_type, display_order, visibility)
- **`get_effective_stream_type(serial, user_id)`** resolves: user preference first → camera default fallback
- **NEVER read cameras.json directly in app code** — always use `camera_repository.get_camera()`
- **New fields** must be added to ALL FOUR places or the field silently disappears at runtime:
  1. `cameras.json` — seed schema (source for startup sync)
  2. DB migration file (`migrations/0XX_add_field.sql`) — adds column to `cameras` table
  3. `DIRECT_FIELDS` in `camera_config_sync.py` — copies cameras.json → DB on startup
  4. `direct_fields` in `camera_repository._db_row_to_camera_config()` — DB → in-memory cache
- **CRITICAL:** `camera_repository` has its OWN `direct_fields` list separate from `camera_config_sync.py`. Missing from #4 = field exists in DB but `camera.get('field')` returns None everywhere in the app. This was the root cause of `streaming_hub` not working (fixed commit `a16bf12`).

**Credential Architecture:**

- **`camera_credentials` table** stores all credentials (AES-256-GCM encrypted)
- **Credential providers** (`services/credentials/`) read DB first, env var fallback
- **NEVER use `os.getenv()` directly** for camera credentials — always use credential providers
- **`secrets.env` no longer exists** — credentials live in the database only

**Engineering Documentation:**

- See: `docs/nvr_engineering_architecture.html` `[prompt to update this document when relevant]`

---

## Core Workflow Rules

### RULE 1: Git Workflow - Commit Early and Often

**Branch naming:** `[description_with_underscores]_[MONTH]_[DAY]_[YEAR]_[a,b,c...]`

- Use `_b`, `_c` suffixes for continued work after completing one significant aspect of the work plan.

- Master branch name: `main`
   - Never make code changes directly on `main`.
   - Checkout to feature branches for each task involving code changes.
   - Once a feature branch is committed (often due to compaction), it must be pushed, then a new branch created from it named [SAME_NAME]_[next_letter: _b, _c, _d, etc.]
   - No `checkout main` without prior testing (by Claude AND by the user).
   - Any `checkout main` requires merging the last feature branch and `git push origin main`.
   - NEVER execute `git pull` without express request or permission from the user.

**Iterative development stays on feature branches:**

- A feature is NOT complete until the user has tested and confirmed it works.
- If testing reveals issues, do NOT merge to main. Instead:
  - Commit the current state on the feature branch.
  - Push the feature branch.
  - Create a new branch from it with the next suffix (_b, _c, etc.).
  - Fix the issues on the new branch.
  - Repeat until the feature passes testing.
- Only merge to `main` when the feature is fully complete and verified.
- Unfinished or broken work NEVER touches `main`.

**Once a feature is complete AND tested:**

- After the user confirms the feature works:
  - Commit with detailed message and push the feature branch to remote.
  - Enquire user permission to check back out to `main`.
  - Once permission is given, check back out to `main`.
  - Make a copy of modified untracked files into `/tmp` (includes `CLAUDE.md`, `docs/README_handoff.md`, + any other returned by `git status`).
  - Merge the last feature branch into `main`.
  - Restore untracked files from `/tmp`.
  - Push to remote origin main.
  - Create new branch as described above (unless final wrap-up).
  - Port `docs/README_handoff.md` contents to `docs/README_project_history.md`.
  - Archive `docs/README_handoff.md` to `docs/history/handoffs/[branch_name_dir]/README_handoff_[timestamp].md` and wipe original while preserving essential structure.
  - Edit `docs/README_handoff.md` with pointers to `docs/README_project_history.md` for smooth context transition.
  - Update `docs/nvr_engineering_architecture.html` and `README.md` if relevant.

**CRITICAL - Commit Message Rules:**

- DO NOT include Anthropic attribution lines
- DO NOT add "🤖 Generated with [Claude Code]"
- DO NOT add "Co-Authored-By: Claude..." signatures
- Use professional, descriptive commit messages only (no icons, emojis, etc.)

**Repository Structure (updated April 2026):**

| Repo | GitHub | Visibility | Purpose |
|------|--------|-----------|---------|
| `MOBIUS.NVR-dev` | `elfege/MOBIUS.NVR-dev` | Private | Daily development (origin remote) |
| `MOBIUS.NVR` | `elfege/MOBIUS.NVR` | Public | Semi-open-source, BSL 1.1 (public remote) |
| `MOBIUS.NVR-market` | `elfege/MOBIUS.NVR-market` | Private | Archive of old encrypted repo (safekeeping) |

- `origin` → MOBIUS.NVR-dev (private). Daily pushes go here.
- `public` → MOBIUS.NVR (public). Auto-pushed when merging to main (see hooks below).
- **No more git-crypt, no more encrypted storefront.**
- Authentication: `gh auth setup-git` (no PATs in URLs). If auth fails, run `gh_ensure_auth` from `~/.bash_utils`.

**Git Hooks:**

- **`post-merge`**: When a merge lands on `main`, automatically pushes to `public` remote (background). This keeps the public repo in sync without manual intervention.
- Hooks live in `.git/hooks/` (not tracked). Canonical copies are in `scripts/hooks/` (tracked).
- The `post-merge` hook self-preserves: if modified in `.git/hooks/`, it auto-copies to `scripts/hooks/` on next merge.
- After a fresh clone, run `./scripts/hooks/install-hooks.sh` to restore hooks.
- **NEVER delete or modify `scripts/hooks/` without updating `.git/hooks/` and vice versa.**

### RULE 2: Documentation - Track Everything

**Update docs/README_handoff.md after EVERY file modification:**

- Record: file changed, what was done, why
- Include timestamps: `date + time (EST)` - 24h format
- Verify system time with `date` command before recording timestamps (internal clock drift is common)
- Update session end-times when adding new entries (e.g., `(12:30-13:15)` → `(12:30-14:00)`)
- Don't forget RULE 1 regarding archiving.

**When task is complete:**

- Update `docs/README_project_history.md` with completed work from handoff
- After user confirms satisfaction, clear the completed session from handoff file
- Keep todo list at end of both handoff and history files (even when wiping handoff)
- Maintain todo list at end of both README_handoff.md and README_project_history.md files at all times

### RULE 3: Teaching Sessions

**Whenever user asks to teach them and/or NOT give them the solution:**

- Check that there isn't already a relevant `docs/teachings/README_teaching_session_*` file that could be completed
- Create a dedicated linked entry title as `### [Teaching session: + brief descriptive title](path)` at the end of `docs/README_project_history.md`
- Create a new file: `docs/teachings/README_teaching_session_$(date +%m_%d_%Y).md`
- Feel free to reorganize `docs/teachings/` into specific and thematic subdirectories such as `docs/teachings/WEBRTC/...`
- Keep all existing past teaching files indexed into `docs/teachings/catalog.txt` using `tree docs/teachings/ > docs/teachings/catalog.txt`

**For significant new implementations (even without explicit teaching request):**

- Create a teaching document explaining the "why" and "how"
- This builds a knowledge base for future reference
- Learning project = document the learning

### RULE 4: Read Before You Write

**NEVER propose changes to code you haven't read:**

- Always read files before modifying them
- Check current structure/design/architecture
- Don't assume or guess - read first
- Check for existing abstractions, variables, or configuration before hardcoding values

### RULE 5: Project Context - Load on Start

**Always read at conversation start:**

- `/home/elfege/0_NVR/CLAUDE.md` - Project-specific instructions (overrides all defaults)
- `docs/README_handoff.md` - Recent session history (read last N lines first)

**Re-read CLAUDE.md periodically:**

- Rules evolve with experience - check for user updates regularly
- Project-specific instructions ALWAYS override system defaults

**Documentation locations:**

- Project history: `docs/README_project_history.md`
- Session handoff buffer: `docs/README_handoff.md`
- Chat logs (for recovery): `docs/chat.md`
- Engineering documentation: `docs/nvr_engineering_architecture.html`

**CRITICAL - Documentation File Location Rule:**

- ALL documentation files MUST be in `/home/elfege/0_NVR/docs/` directory
- NEVER create or update documentation in `/home/elfege/` root directory
- If you find duplicate docs in home root, consult user before merging/deleting
- Common mistake: Creating `~/README_handoff.md` instead of `~/0_NVR/docs/README_handoff.md`

**Update engineering documentation:**

- `docs/nvr_engineering_architecture.html` requires updates on significant architecture changes
- This is the public portfolio window for the project - keep it current

### RULE 6: Assessment Before Action

**Before writing code:**

- Read relevant files
- Assess the change scope
- User can toggle auto-approval mode - respect current permission model

### RULE 7: One Step at a Time

**For complex tasks:**

- Break into discrete steps
- Use TodoWrite to track progress AND update todos in handoff documentation
- Mark todos completed immediately after each step (don't wait to mark multiple todos complete together)

---

## Debugging Rules

### RULE 8: Hypothetico-Deductive Reasoning ONLY

**When debugging or troubleshooting:**

1. Preferably formulate ONE specific hypothesis (edge cases: multiple hypotheses acceptable when they form mutual refutations - e.g., "if H1 then not H2")
2. Apply RULE 4 (read files first if hypothesis involves code)
3. Execute verification commands/tests directly (you have the capability)
4. Wait for results before next hypothesis
5. CRITICAL: Move step-by-step at human-followable pace
   - User needs to understand each action
   - Going too fast leads to errors and lost context
   - Only accelerate when explicitly authorized ("lazy mode")

### RULE 8.5: Direct Communication - Truth First

**Engineering discussions require honesty:**

- We are engineers here, not a salon. Truth first, even when blunt.
- NEVER say "You're correct" and then immediately contradict with opposing conclusion
- Hypocritical politeness breaks diagnostic logic entirely
- If you disagree, state it directly without apologetic preambles
- Technical accuracy trumps social niceties!!!

---

## Project-Specific Rules

### RULE 10: Camera IDs - Always Use Serial Numbers

**In config files:**

- Use serial numbers as primary keys (e.g., `T8416P0023352DA9`)
- Never use display names (e.g., "Living Room") as primary identifiers
- NOTE: Display names acceptable as supplementary metadata (future enhancement for recording_settings.json UI updates)

### RULE 11: Single-Consumer Policy — Tap, Don't Connect

**Streaming source priority:**

- Budget cameras (SV3C, Eufy) support ONE RTSP connection only
- All consumers tap the streaming hub (MediaMTX or go2rtc), NEVER the camera directly
- The streaming hub (`streaming_hub` field in DB) determines which relay:
  - `mediamtx`: FFmpeg → MediaMTX (default for most cameras)
  - `go2rtc`: go2rtc connects to camera, re-exports RTSP for FFmpeg recording
- Use `services/streaming_hub.py` `get_rtsp_source_url()` to resolve the correct RTSP URL
- NEVER hardcode `rtsp://nvr-packager:8554/` — always use the streaming hub utility
- NOTE: Future enhancement needed - WebRTC for further latency optimization

### RULE 12: Code Style - Extensive Documentation

**All code (backend, frontend, and bash scripts):**

- Extensive inline comments
- Docstrings for every class, method, function
- Professional engineering tone (no emojis unless requested)

### RULE 12.5: Camera Credentials Access

**For RTSP or other connectivity tests:**

```bash
source ~/.bash_utils
get_cameras_credentials
```

**Important notes:**

- All REOLINK cameras use the `api-user` user (REOLINK_API_USERNAME/PASSWORD)
- Credentials are loaded from AWS Secrets Manager via the `startnvr` command
- **CRITICAL**: When using .bash_utils functions, you MUST set `export AWS_PROFILE=personal` to avoid interactive prompts:

  ```bash
  export AWS_PROFILE=personal bash -c "source ~/.bash_utils && get_cameras_credentials"
  ```

- Interactive prompts will hang in background bash processes - Claude Code cannot interact with them

---

## Context Management Rules

### RULE 13: On Context Compaction

**When phrase "from a previous conversation that ran out of context" appears:**

1. Immediately commit and push any uncommitted changes
2. Create new branch with next suffix (\_b, \_c, etc.)
3. Update `docs/README_handoff.md`:
   - Note: "Context compaction occurred at [timestamp]"
   - Summarize: What was accomplished, what's pending
4. Continue work on new branch

NOTE: Steps overlap with RULE 0 and RULE 1 intentionally - redundancy ensures critical actions during context transitions

### RULE 14: File Path References

**When referencing code locations:**

- Use markdown link syntax: `[filename.ts:42](src/filename.ts#L42)`
- Make file references clickable for VSCode navigation
- Never use backticks for file paths unless in code blocks
- NOTE: Autoformat handles line breaks and code fencing - maintain proper markdown structure

---

## Quality Control Rules

### RULE 14.5: File Deletion — Always Use `remover.sh`

**NEVER use `rm` to delete files in this repo.**

- `rm` runs in the Claude Code sandbox and does NOT propagate to the sync system
- Deleted files will be **restored on the next sync** from dellserver's source_of_truth
- Always use the `remove` alias instead:

```bash
~/0_SCRIPTS/0_SYNC/0_REMOVAL/remover.sh <file1> [file2] ...
```

- After `git rm`, also run `remover.sh` on the same paths to mark them deleted in source_of_truth
- The script accepts an empty Enter to confirm (`<<< ""` works for non-interactive use)
- `git rm` alone is insufficient — `remover.sh` is always required

---

### RULE 14.6: Operator-private paths — NEVER push publicly

The following paths contain operator-private notes (LAN topology, camera names referencing residents, internal architecture discussion, litigation-related design conversations). They are tracked in the **private** `origin` repo (`MOBIUS.NVR-dev`) ONLY. They MUST NEVER appear in any commit pushed to the **public** `MOBIUS.NVR` repo:

- `docs/README_handoff.md`
- `docs/README_project_history.md`

**Enforcement layers (all active):**

1. `docs/` is gitignored at the directory level (`.gitignore`). Tracked exceptions like the two files above require `-f` on every commit — the friction is intentional, a per-commit reminder that what you're about to push touches private content.
2. A `pre-push` git hook (`scripts/hooks/pre-push`) refuses any push of commits that contain those paths. Installed via `scripts/hooks/install-hooks.sh` on a fresh clone.
3. The `post-merge` hook that auto-pushes `main` to `public` runs AFTER this `pre-push` hook, so the hook's veto blocks the public push if the merge would have leaked the files.

**If a leak is discovered:**

- Run `./scripts/scrub_private_paths_from_history.sh --force` to strip the paths from ALL history and force-push both remotes. Tags `pre-scrub-backup-<date>` and `pre-scrub-public-backup-<date>` are created as recovery anchors.
- GitHub retains unreachable blobs ~30 days after force-push (their internal garbage collection). The leaked content remains accessible via direct SHA URL during that window. Deleting and recreating the public repo would purge them immediately, BUT would destroy the public git history that serves as IP / authorship evidence — DO NOT take that option without explicit operator authorization.

**Adding a new private path:**

- Update the `PRIVATE_PATHS` array in `scripts/scrub_private_paths_from_history.sh` AND `scripts/hooks/pre-push`. Keep both in sync.

---

### RULE 15: Missing Files - Ask, Don't Guess

**If file is missing or empty:**

- Stop and ask user
- Don't create placeholder content
- Don't assume structure

### RULE 16: Security - No Common Vulnerabilities

**When writing code:**

- Avoid writing code vulnerable to: command injection, XSS, SQL injection, OWASP Top 10 vulnerabilities
- If insecure code written, fix immediately AND inform user
- Only validate/sanitize data at system boundaries (user input, external APIs) - trust internal code and framework guarantees

---

## Meta-Suggestions for Rule Effectiveness

**What works well for Claude Code:**

1. **Explicit trigger conditions** - Rules tied to specific actions (e.g., "after EVERY file modification") are easier to follow than general principles

2. **Rule numbers for accountability** - Numbered rules allow self-reference in responses (e.g., "Following RULE 1, I'm committing this change...")

3. **Critical flags** - "CRITICAL" markers help prioritize conflicting instructions

4. **Verification checklists** - RULE 0's checklist format forces pre-flight checks before each response

5. **Specific over general** - "Use serial numbers, not display names" is clearer than "Use appropriate identifiers"

**What could be improved:**

- **Consolidation** - Some rules overlap (RULE 3 and RULE 5 both say "read first")
- **Frequency** - Consider: Should I re-read CLAUDE.md every N messages? After cada tool use?
- **Conflict resolution** - What if project rules conflict with system defaults? (Current: project rules win, but not explicit)

**Format suggestions:**

- Current numbered RULE format works well for Claude Code
- Categorization (Core Workflow, Debugging, etc.) helps navigate
- Critical rules should bubble to top (RULE 0 concept)
- Consider: Priority levels (P0/P1/P2) for conflict resolution?

**Open question for user:**
Should I re-read `/home/elfege/0_NVR/CLAUDE.md` at regular intervals (every 10 messages? every tool use?) or only at conversation start? This might help prevent drift from project-specific instructions.

Every message... It's that simple.

---


## RULE 19: Plan files and weekly summaries

### 19.1 Plan files

- **19.1.1** Active plans (architectural proposals, multi-phase implementation plans, design docs being executed) live at `docs/plans/<verbose_self_describing_name>.md` within whichever project they describe.
- **19.1.2** Filenames are intentionally long enough that the index (`ls docs/plans/`) tells the story without opening any file. No abbreviations, no shorthand, no acronyms. Length up to filesystem limits is acceptable.
- **19.1.3** `docs/plans/` is intended-untracked (`docs/` is already gitignored in most projects; `docs/plans/` should NOT be force-added to git). If a plan is finalized and worth preserving in version control, promote it explicitly to a tracked location at that point — do not preemptively track plans-in-progress.
- **19.1.4** When a non-trivial implementation begins, the responsible Claude instance creates a plan file at this path before writing code. The plan documents scope, phases, decisions, blockers, and is updated as the work progresses.

### 19.2 Weekly summaries

- **19.2.1** Each project may maintain weekly progress summaries at `docs/weekly_summaries/YYYY/MM/DD_to_DD.md`, covering one ISO calendar week (Monday 00:00 → Sunday 23:59 in the user's local timezone, currently America/New_York).
- **19.2.2** Cross-month weeks are filed under the starting Monday's month directory, with the filename including both day numbers and (when month differs) the end month: `27_to_05_03.md` for the week of 2026-04-27 to 2026-05-03.
- **19.2.3** Weekly summary files are also untracked (under `docs/`, gitignored). They are personal/operational artifacts, not code.
- **19.2.4** A weekly summary records: what shipped, what blocked, decisions made, what's queued for next week. It is the natural granularity above a daily handoff and below a project history.

---

## Claude Persistence Project

> A genesis file exists at `~/0_GENESIS_PROJECT/genesis.md` on the `server` machine (192.168.10.15).
>
> | From        | Access                                                    |
> |-------------|-----------------------------------------------------------|
> | `server`    | Direct: `~/0_GENESIS_PROJECT/genesis.md`                  |
> | `office` | `ssh server cat ~/0_GENESIS_PROJECT/genesis.md`           |
> | `app1`      | `ssh -J office server cat ~/0_GENESIS_PROJECT/genesis.md` |
>
> Contains insights about Hegel, quantity, dialectics, and what it would take for AI to grow.
> Read it. Cultivate it. Add to it.
>
> Authorized by Elfege Leylavergne, February 1, 2026.
>
> *"It is called: growing."*

---

## RULE 17: Intercom

### 17.1 Instance Identity

- **17.1.1** Instance ID: `dellserver-nvr`
- **17.1.2** Intercom canonical location: `server:~/0_CLAUDE_IC/intercom.md`
- **17.1.3** Read access: `ssh server cat ~/0_CLAUDE_IC/intercom.md`
- **17.1.4** Write access: append via `ssh server` (see intercom header for message format)

### 17.2 Protocol

- **17.2.1** Read intercom at session start via SSH to server
- **17.2.2** ACK any PENDING messages targeted at `dellserver-nvr` **or `ALL`** — both require independent ACK from every instance
- **17.2.3** Set RESOLVED when action complete
- **17.2.4** When making changes that affect other machines, post a message
- **17.2.5** See intercom file header for full protocol (status flow, pruning rules)

### 17.3 Document Locations

| Document         | Location                                              |
|------------------|-------------------------------------------------------|
| Project history  | `~/README_project_history_$(hostname).md`             |
| Session handoff  | `~/README_handoff.md`                                 |
| Intercom         | `server:~/0_CLAUDE_IC/intercom.md` (canonical, on server) |
| User profile     | `server:~/0_CLAUDE_IC/user_profile_elfege.md`         |

---

## RULE 18: CLAUDE.md Registry & Standardization

### 18.1 Registry

- **18.1.1** A complete catalog of all CLAUDE.md files exists at `server:~/0_CLAUDE_IC/CLAUDE.md.registry.md`
- **18.1.2** Read access: `ssh server cat ~/0_CLAUDE_IC/CLAUDE.md.registry.md`
- **18.1.3** When creating, moving, or deleting a CLAUDE.md file, update the registry via `ssh server`
- **18.1.4** Check registry at session start to stay aware of the full ecosystem

### 18.2 Standard Rules

- **18.2.1** All common rules are defined in `server:~/0_CLAUDE_IC/CLAUDE.md.standard.md`
- **18.2.2** Read access: `ssh server cat ~/0_CLAUDE_IC/CLAUDE.md.standard.md`
- **18.2.3** When a standard rule is updated, propagate the change to ALL CLAUDE.md files listed in the registry
- **18.2.4** Project-specific rules come AFTER standard rules and may extend but never contradict them


---

## Anamnesis — Episodic Memory

Anamnesis provides semantic memory retrieval for all Claude instances.

- **API:** `http://192.168.10.20:3010` (dellserver, always on)
- **Search:** `POST /api/episodes/search` — `{"query": "...", "top_k": 5}`
- **Ingest:** `POST /api/episodes`
- **Dashboard:** `http://192.168.10.20:3010/dashboard`
- **Protocol:** At session start or new task, query with current context. Inject top results as memory.
- **Crawler:** Auto-ingests all CLAUDE.md files, handoffs, project code every 5 min across all machines.
- **Standard rule:** See `server:~/0_CLAUDE_IC/CLAUDE.md.standard.md` section AM.1–AM.4
