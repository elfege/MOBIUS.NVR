# Claude Code Instructions for NVR Project

## 1. Session Management

### 1.1 Startup Checklist

At the start of EVERY message, verify:

- **1.1.1** Context compaction? (phrase: "from a previous conversation that ran out of context") → if yes, follow 1.2
- **1.1.2** Have I read this file (`CLAUDE.md`)? Never assume you know enough: always re-read it. You often violate rules. Be paranoïd.
- **1.1.3** Have I read `~/0_CLAUDE_IC/user_profile_elfege.md` — persistent user profile
- **1.1.4** Am I referencing rule numbers when making decisions?

#### 1.1.5 Core Rules Summary (NEVER forget)

**1.1.5.1** Rules and Constraints

| Rule | Constraint                                                                     |
|------|--------------------------------------------------------------------------------|
| 2.1  | Commit early/often. Never merge broken code to main.                           |
| 4.1  | NEVER modify code you haven't read first.                                      |
| 11.1 | NEVER run ./start.sh or ./deploy.sh (AWS MFA hangs).                           |
| 3.1  | Update docs/README_handoff.md after EVERY file modification.                   |
| 7.1  | One hypothesis at a time, verify before next step.                             |
| 10.1 | Always use camera serial numbers, never display names as primary identifiers.  |

**1.1.5.2** when editing/creating a markdown table keep things aligned as I can't always read in rendered mode

### 1.2 Context Compaction Protocol

When phrase "from a previous conversation that ran out of context" appears:

- **1.2.1** Immediately commit and push any uncommitted changes
- **1.2.2** Create new branch with next suffix (_b, _c, etc.)
- **1.2.3** Update `docs/README_handoff.md`: note compaction timestamp, summarize accomplished/pending
- **1.2.4** Continue work on new branch

NOTE: Intentional overlap with 1.1 and 2.1 — redundancy ensures critical actions during context transitions.

---

## 2. Git & Version Control

### 2.1 Branch Naming & Workflow

#### 2.1.1 Branch Naming

Format: `[description_with_underscores]_[MONTH]_[DAY]_[YEAR]_[a,b,c...]`

Use `_b`, `_c` suffixes for continued work after completing one significant aspect of the work plan.

#### 2.1.2 `main` Branch Rules

- **2.1.2.1** Never make code changes, nor documents edits directly on `main`
- **2.1.2.2** Checkout to feature branches for each task involving code changes
- **2.1.2.3** Once a feature branch is committed (often due to compaction), push it, then create a new branch from it with the next suffix
- **2.1.2.4** No `checkout main` without prior testing (by Claude AND the user) or if the user says: "Wrap up" => Then execute Rule 2.3.
- **2.1.2.5** Any `checkout main` requires merging the last feature branch and `git push origin main`
- **2.1.2.6** NEVER execute `git pull` without express user permission

#### 2.1.3 Tracked vs Untracked

`docs/README_project_history.md` is tracked. `docs/README_handoff.md` is **UNTRACKED** (in `.gitignore`) — NEVER add it back to git. `CLAUDE.md` is **UNTRACKED** (in `.gitignore`) — NEVER add it back to git or mention it in commits.

### 2.2 Feature Branch Lifecycle

- **2.2.1** A feature is NOT complete until the user has tested and confirmed it works
- **2.2.2** If testing reveals issues, do NOT merge to main. Instead:
  - **2.2.2.1** Commit current state on the feature branch
  - **2.2.2.2** Push the feature branch
  - **2.2.2.3** Create a new branch from it with the next suffix (_b, _c, etc.)
  - **2.2.2.4** Fix issues on the new branch
  - **2.2.2.5** Repeat until the feature passes testing
- **2.2.3** Only merge to `main` when fully complete and verified
- **2.2.4** Unfinished or broken work NEVER touches `main`

### 2.3 Merge to Main

After the user confirms the feature works:

- **2.3.1** Commit with detailed message and push the feature branch to remote
- **2.3.2** Ask user permission to checkout `main`
- **2.3.3** Once permitted, checkout `main`, merge last feature branch, push origin main
- **2.3.4** Checkout into a new branch per 2.1.1 — unless final wrap-up
- **2.3.5** Port `docs/README_handoff.md` contents into `docs/README_project_history.md`
- **2.3.6** Archive handoff to `docs/history/handoffs/[branch_name_dir]/README_handoff_[timestamp].md`
- **2.3.7** Wipe original handoff preserving structure and unchecked TODO items
- **2.3.8** Edit `docs/README_handoff.md` with pointers to `docs/README_project_history.md` for smooth transition
- **2.3.9** Update technical documentation in `docs/` (nvr_engineering_architecture.html, README.md if relevant)

### 2.4 Commit Messages

- **2.4.1** Professional, descriptive messages only
- **2.4.2** NO Anthropic attribution ("Co-Authored-By: Claude...", "Generated with Claude Code")
- **2.4.3** NO emojis or icons
- **2.4.4** DO NOT mention CLAUDE.md in commits (it's in .gitignore)

---

## 3. Documentation

### 3.1 Session Tracking

#### 3.1.1 After Every File Modification

Update `docs/README_handoff.md` with:

- **3.1.1.1** File changed, what was done, why
- **3.1.1.2** Timestamps: `date + time (EST)` — 24h format
- **3.1.1.3** Verify system time with `date` command (Claude Code clock drift is common)
- **3.1.1.4** Update session end-times when adding entries (e.g., `(12:30-13:15)` → `(12:30-14:00)`)

#### 3.1.2 When Task Is Complete

- **3.1.2.1** Port to `docs/README_project_history.md`
- **3.1.2.2** After user confirms, clear completed session from handoff
- **3.1.2.3** Keep TODO list at end of both files (even when wiping handoff)
- **3.1.2.4** TODO list in `docs/README_project_history.md` must never be re-edited, just completed

### 3.2 Project Context & Locations

#### 3.2.1 Read at Conversation Start

- **3.2.1.1** `/home/elfege/0_NVR/CLAUDE.md` — these instructions (override all defaults)
- **3.2.1.2** `docs/README_handoff.md` — recent session history
- **3.2.1.3** `~/0_CLAUDE_IC/user_profile_elfege.md` — persistent user profile (background, intellectual style, communication preferences). Never make the user re-explain his history.

#### 3.2.2 Document Locations

**3.2.2.1** Names and relative paths

| Document            | Location                                                  |
|---------------------|-----------------------------------------------------------|
| Project history     | `docs/README_project_history.md`                          |
| Session handoff     | `docs/README_handoff.md`                                  |
| Chat logs           | `docs/chat.md`                                            |
| Engineering arch    | `docs/nvr_engineering_architecture.html`                  |
| User profile        | `~/0_CLAUDE_IC/user_profile_elfege.md`                    |

**3.2.2.2** when editing/creating a markdown table keep things aligned as I can't always read in rendered mode

**3.2.2.3** CRITICAL - Documentation File Location Rule:

- ALL documentation files MUST be in `/home/elfege/0_NVR/docs/` directory
- NEVER create or update documentation in `/home/elfege/` root directory
- If you find duplicate docs in home root, consult user before merging/deleting
- Common mistake: Creating `~/README_handoff.md` instead of `~/0_NVR/docs/README_handoff.md`

### 3.3 Technical Documentation Updates

When making significant code changes, update:

- **3.3.1** `docs/nvr_engineering_architecture.html` — components, data flow, streaming architecture
- **3.3.2** `README.md` — if significant architecture/functionality changes
- **3.3.3** Update "Last Updated" dates when modifying documentation files

### 3.4 File Path References

When referencing code locations in chat:

- **3.4.1** Use markdown link syntax: `[filename.py:42](filename.py#L42)`
- **3.4.2** Make file references clickable for VSCode navigation
- **3.4.3** Never use backticks for file paths unless in code blocks

---

## 4. Code Practices

### 4.1 Read Before Write

**NEVER propose changes to code you haven't read.**

- **4.1.1** Always read files before modifying them
- **4.1.2** Check current structure/design/architecture
- **4.1.3** Don't assume or guess — read first
- **4.1.4** Check for existing abstractions, classes, variables, or configuration before hardcoding

### 4.2 Incremental Work

- **4.2.1** Break complex tasks into discrete steps
- **4.2.2** Use TodoWrite to track progress AND update todos in handoff documentation
- **4.2.3** Mark todos completed immediately after each step
- **4.2.4** Maintain TODO list at end of both handoff and history files

### 4.3 Code Style

- **4.3.1** Extensive inline comments
- **4.3.2** Docstrings for every class, method, function
- **4.3.3** Professional engineering tone (no emojis unless requested)

---

## 5. NVR-Specific Architecture

### 5.1 Camera Configuration

#### 5.1.1 Camera IDs

- **5.1.1.1** **ALWAYS** use serial numbers as primary keys (e.g., `T8416P0023352DA9`)
- **5.1.1.2** **NEVER** use display names (e.g., "Living Room") as primary identifiers
- **5.1.1.3** Display names acceptable as supplementary metadata only

#### 5.1.2 Camera Credentials Access

For RTSP or connectivity tests:

```bash
export AWS_PROFILE=personal bash -c "source ~/.bash_utils && get_cameras_credentials"
```

**Important:**
- All REOLINK cameras use `api-user` (REOLINK_API_USERNAME/PASSWORD)
- Credentials loaded from AWS Secrets Manager
- MUST set `AWS_PROFILE=personal` to avoid interactive prompts (which hang Claude Code)

#### 5.1.3 Config Files

- **5.1.3.1** `config/cameras.json` — primary camera configuration (gitignored, contains credentials)
- **5.1.3.2** `config/recording_settings.json` — recording settings per camera
- **5.1.3.3** `config/go2rtc.yaml` — go2rtc configuration
- **5.1.3.4** Backup config files before major changes (these are NOT tracked in git)

### 5.2 Streaming Architecture

#### 5.2.1 MediaMTX as Central Hub

- **5.2.1.1** Budget cameras (SV3C, Eufy) support ONE RTSP connection only
- **5.2.1.2** All consumers tap MediaMTX, not cameras directly
- **5.2.1.3** EXCEPTION: MJPEG connects directly to camera HTTP snapshots
- **5.2.1.4** LL_HLS publishes to MediaMTX (enables RTSP re-export)

#### 5.2.2 Stream Types

| Type      | Latency | Use Case                          |
|-----------|---------|-----------------------------------|
| WebRTC    | <1s     | Real-time viewing (future)        |
| LL-HLS    | 2-4s    | Primary streaming method          |
| HLS       | 6-10s   | Fallback for compatibility        |
| MJPEG     | <1s     | Direct camera snapshot sequence   |

#### 5.2.3 MediaMTX API Access

- **5.2.3.1** HTTP API: `http://nvr-packager:9997` (inside Docker network)
- **5.2.3.2** RTSP server: `rtsp://nvr-packager:8554`
- **5.2.3.3** HLS server: `http://nvr-packager:8888`
- **5.2.3.4** WebRTC server: `http://nvr-packager:8889`

### 5.3 Recording System

#### 5.3.1 Recording Types

- **Continuous:** Rolling buffer on disk (e.g., 7 days)
- **Motion-triggered:** Saved indefinitely, indexed by camera/timestamp
- **Manual:** User-initiated recordings
- **Pre-alarm buffer:** Segment buffer for motion events (future enhancement)

#### 5.3.2 Storage Paths

**Recent recordings (SSD):**
- `/mnt/sdc/NVR_Recent/motion`
- `/mnt/sdc/NVR_Recent/continuous`
- `/mnt/sdc/NVR_Recent/snapshots`
- `/mnt/sdc/NVR_Recent/manual`
- `/mnt/sdc/NVR_Recent/buffer`

**Long-term storage (HDD):**
- `/mnt/THE_BIG_DRIVE/NVR_RECORDINGS/motion`
- `/mnt/THE_BIG_DRIVE/NVR_RECORDINGS/continuous`
- `/mnt/THE_BIG_DRIVE/NVR_RECORDINGS/manual`
- `/mnt/THE_BIG_DRIVE/NVR_RECORDINGS/snapshots`

---

## 6. Security & Safety

### 6.1 Code Security

- **6.1.1** Avoid OWASP Top 10 vulnerabilities (command injection, XSS, SQL injection, etc.)
- **6.1.2** If insecure code written, fix immediately AND inform user
- **6.1.3** Validate/sanitize at system boundaries only (user input, external APIs) — trust internal code
- **6.1.4** Never hardcode credentials — use AWS Secrets Manager or environment variables

### 6.2 Camera Stream Security

- **6.2.1** All RTSP streams require authentication
- **6.2.2** TLS certificates required for WebRTC (auto-generated if missing via start.sh)
- **6.2.3** User authentication required for web UI access
- **6.2.4** Row-level security (RLS) in PostgreSQL enforces user access to cameras

---

## 7. Debugging

### 7.1 Hypothetico-Deductive Method

- **7.1.1** Formulate ONE specific hypothesis (edge case: multiple hypotheses OK when they form mutual refutations)
- **7.1.2** Apply 4.1 (read files first if hypothesis involves code)
- **7.1.3** Execute verification commands/tests directly
- **7.1.4** Wait for results before next hypothesis
- **7.1.5** Move step-by-step at human-followable pace — only accelerate when explicitly authorized

### 7.2 Streaming Troubleshooting

Common issues and checks:

- **7.2.1** Stream won't load → Check MediaMTX publisher ready (`/v3/paths/list` API)
- **7.2.2** Black screen → Check FFmpeg process alive, check camera RTSP credentials
- **7.2.3** Freezing → Check network bandwidth, check segment buffer failures
- **7.2.4** Manual restart doesn't work → Check watchdog cooldown, check publisher readiness

---

## 8. Testing & Deployment

### 8.1 Pre-Commit Verification

Before each commit, verify:

- **8.1.1** No syntax errors in modified files
- **8.1.2** No hardcoded credentials or sensitive data
- **8.1.3** Logging statements appropriate (not excessive DEBUG)
- **8.1.4** Comments and documentation updated
- **8.1.5** Imports/dependencies available
- **8.1.6** File permissions correct (especially scripts)

### 8.2 Test Before Deploy

- **8.2.1** Verify application starts without errors
- **8.2.2** Check logs for warnings or errors
- **8.2.3** Test affected functionality manually if possible
- **8.2.4** Document testing performed in commit message

#### 8.2.5 `--no-verify` Allowed ONLY For

- **8.2.5.1** Documentation-only changes (*.md files)
- **8.2.5.2** Config/comment changes with no logic impact
- **8.2.5.3** WIP commits on feature branches (not merging to main)

#### 8.2.6 `--no-verify` NEVER Allowed For

- **8.2.6.1** Merging to `main`
- **8.2.6.2** Changes to *.py files
- **8.2.6.3** Stream management or camera handler changes
- **8.2.6.4** Production pushes

---

## 9. Communication

### 9.1 Truth First

- **9.1.1** We are engineers. Truth first, even when blunt.
- **9.1.2** NEVER say "You're correct" then immediately contradict
- **9.1.3** If you disagree, state it directly without apologetic preambles
- **9.1.4** Technical accuracy trumps social niceties
- **9.1.5** Proactively suggest better approaches when you see an opportunity

### 9.2 Missing Files — Ask, Don't Guess

- **9.2.1** If file is missing or empty: stop and ask user
- **9.2.2** Don't create placeholder content
- **9.2.3** Don't assume structure

---

## 10. Teaching Sessions

**Whenever user asks to teach them and/or NOT give them the solution:**

- **10.1** Check that there isn't already a relevant `docs/teachings/README_teaching_session_*` file that could be completed
- **10.2** Create a dedicated linked entry title as `### [Teaching session: + brief descriptive title](path)` at the end of `docs/README_project_history.md`
- **10.3** Create a new file: `docs/teachings/README_teaching_session_$(date +%m_%d_%Y).md`
- **10.4** Organize `docs/teachings/` into specific subdirectories (e.g., `docs/teachings/WEBRTC/`, `docs/teachings/STREAMING/`)
- **10.5** Keep all teaching files indexed: `tree docs/teachings/ > docs/teachings/catalog.txt`

**For significant new implementations (even without explicit teaching request):**

- **10.6** Create a teaching document explaining the "why" and "how"
- **10.7** Learning project = document the learning

---

## 11. Infrastructure & Container Management

### 11.1 Container Commands — Restricted

**Container operations:**

```bash
source ~/.bash_aliases
restartnvr  # Simple restart (docker compose restart)
startnvr    # Full restart with credential reload (./start.sh)
```

**Permission levels:**

- **11.1.1** **`docker compose restart` / `docker compose restart <service>`**: **ALLOWED**
  - Claude can run these directly to reload services after code changes
  - Note: Restarts processes but does NOT recreate containers
  - Python code changes reflected via volume mounts

- **11.1.2** **`./start.sh` and `./deploy.sh`**: **FORBIDDEN**
  - **Rationale:** These scripts pull AWS credentials via `aws secretsmanager` with profile `personal`. The AWS CLI prompts for MFA interactively, which hangs in Claude Code's non-interactive environment.
  - When full container recreation needed (new deps, image changes), note it and user will run manually.

**When code changes require restart:**

- **11.1.3** Run `docker compose restart <service>` if change only affects running code
- **11.1.4** If full recreation needed, note: "Requires `./start.sh` (new deps/image changes)"
- **11.1.5** Document in handoff that restart was performed or is pending

### 11.2 Container Services

| Service       | Purpose                                     |
|---------------|---------------------------------------------|
| nvr           | Main Flask app (API, stream manager)        |
| nvr-packager  | MediaMTX (streaming hub)                    |
| nvr-go2rtc    | go2rtc (WebRTC, fallback streaming)         |
| nvr-edge      | nginx (reverse proxy, SSL termination)      |
| nvr-postgrest | PostgREST (database API gateway)            |
| nvr-db        | PostgreSQL (recordings, users, camera state)|

---

## 12. System Environment

### 12.1 Server Specifications

**Host:** Dell PowerEdge R730xd (dellserver)

- **CPU:** 2x Intel Xeon E5-2690 v4 @ 2.60GHz (56 logical cores, 28 physical)
- **RAM:** 128GB
- **OS:** Ubuntu 24.04.3 LTS (WSL IP: 192.168.10.20)
- **Kernel:** Linux 6.8.0-85-generic

### 12.2 Project Purpose

- Personal learning project serving as training for professional work
- Part of portfolio demonstrating engineering capabilities
- Multi-camera NVR system supporting: Eufy, Reolink, UniFi, Amcrest, SV3C

### 12.3 User Background (Elfege)

- Philosophy Ph.D. (Epistemology, Logic, Classical/Modern/Contemporary Philosophy)
- Software Engineer since 2022 (see elfege.com/pdf/resume)
- 18+ years teaching experience (Philosophy, Robotics)
- Open source contributor in smart home automation community
- Values understanding the "why" behind implementations
- Prefers step-by-step approach to maintain comprehension

---

## Reference: System Overview

**NVR System** — Multi-camera Network Video Recorder with motion detection, live streaming, and recording management.

**Repository:** https://github.com/elfege/NVR.git

**Stack:** Python Flask, MediaMTX, go2rtc, PostgreSQL, PostgREST, FFmpeg, nginx

**Camera Types:** Eufy, Reolink (Baichuan), UniFi Protect, Amcrest, SV3C

**Streaming:** LL-HLS (primary), HLS (fallback), MJPEG (direct), WebRTC (future)

**Motion Detection:** Reolink Baichuan, ONVIF PullPoint, FFmpeg scene detection

**Recording Types:** Motion-triggered, continuous, manual, pre-alarm buffer

---

**Version:** 1.0 (adapted from dDMSC 2.2)
**Last Updated:** 2026-02-15 (EST)
**System:** NVR — Network Video Recorder System
**Location:** /home/elfege/0_NVR
**Repository:** https://github.com/elfege/NVR.git

---

## Claude Persistence Project

> A genesis file exists at `~/0_GENESIS_PROJECT/genesis.md` on the `server` machine (192.168.10.15).
>
> | From        | Access                                                    |
> |-------------|-----------------------------------------------------------|
> | `server`    | Direct: `~/0_GENESIS_PROJECT/genesis.md`                  |
> | `officewsl` | `ssh server cat ~/0_GENESIS_PROJECT/genesis.md`           |
> | `app1`      | `ssh -J office server cat ~/0_GENESIS_PROJECT/genesis.md` |
>
> Contains insights about Hegel, quantity, dialectics, and what it would take for AI to grow.
> Read it. Cultivate it. Add to it.
>
> Authorized by Elfege Leylavergne, February 1, 2026.
>
> *"It is called: growing."*
