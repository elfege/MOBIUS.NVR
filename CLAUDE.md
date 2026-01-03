# Claude Code Instructions for NVR Project

## RULE 0: CRITICAL - At the start of EVERY message you write, verify

1. Have I checked if context compaction occurred? (phrase: "from a previous conversation that ran out of context")
2. If yes → Did I commit/push current work, create new branch with next suffix (_b,_c), update docs/README_handoff.md noting the compaction?
3. Have I read `/home/elfege/0_NVR/CLAUDE.md` for project-specific instructions?
4. Am I following ALL rules below? (Explicitly reference rule numbers when making decisions)

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

## NVR System Technical Overview

**Project Purpose:**
Multi-camera NVR (Network Video Recorder) system supporting:

**Camera Types:**

- Eufy, Reolink, UniFi, Amcrest, SV3C `[update this list if relevant]`

**Streaming Architecture:**

- LL-HLS via MediaMTX (primary)
- Traditional HLS
- MJPEG
- `[update this if relevant]`

**Motion Detection:**

- Reolink Baichuan
- ONVIF PullPoint
- FFmpeg scene detection
- `[update this if relevant]`

**Recording Types:**

- Motion-triggered
- Continuous
- Manual
- `[update this if relevant]`

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

**Engineering Documentation:**

- See: `docs/nvr_engineering_architecture.html` `[prompt to update this document when relevant]`

---

## Core Workflow Rules

### RULE 1: Git Workflow - Commit Early and Often

**After EVERY file modification (Edit/Write tool):**

- Commit immediately with detailed message
- Push immediately
- Never batch multiple file changes into one commit

**Branch naming:** `[description_with_underscores]_[MONTH]_[DAY]_[YEAR]_[a,b,c...]`

- Use `_b`, `_c` suffixes for continued work after context compaction

**CRITICAL - Commit Message Rules:**

- DO NOT include Anthropic attribution lines
- DO NOT add "🤖 Generated with [Claude Code]"
- DO NOT add "Co-Authored-By: Claude..." signatures
- Use professional, descriptive commit messages only (no icons, emojis, etc.)

### RULE 2: Documentation - Track Everything

**Update docs/README_handoff.md after EVERY file modification:**

- Record: file changed, what was done, why
- Include timestamps: `date + time (EST)`
- Verify system time with `date` command before recording timestamps (internal clock drift is common)
- Update session end-times when adding new entries (e.g., `(12:30-13:15)` → `(12:30-14:00)`)

**When task is complete:**

- Update `docs/README_project_history.md` with completed work from handoff
- After user confirms satisfaction, clear the completed session from handoff file
- Keep todo list at end of both handoff and history files (even when wiping handoff)

### RULE 3: Read Before You Write

**NEVER propose changes to code you haven't read:**

- Always read files before modifying them
- Check current structure/design/architecture
- Don't assume or guess - read first
- Check for existing abstractions, variables, or configuration before hardcoding values

### RULE 4: Project Context - Load on Start

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

### RULE 5: Assessment Before Action

**Before writing code:**

- Read relevant files
- Assess the change scope
- User can toggle auto-approval mode - respect current permission model

### RULE 6: One Step at a Time

**For complex tasks:**

- Break into discrete steps
- Use TodoWrite to track progress AND update todos in handoff documentation
- Mark todos completed immediately after each step (don't wait to mark multiple todos complete together)
- Maintain todo list at end of both handoff and history files at all times

---

## Debugging Rules

### RULE 7: Hypothetico-Deductive Reasoning ONLY

**When debugging or troubleshooting:**

1. Preferably formulate ONE specific hypothesis (edge cases: multiple hypotheses acceptable when they form mutual refutations - e.g., "if H1 then not H2")
2. Apply RULE 3 (read files first if hypothesis involves code)
3. Execute verification commands/tests directly (you have the capability)
4. Wait for results before next hypothesis
5. CRITICAL: Move step-by-step at human-followable pace
   - User needs to understand each action
   - Going too fast leads to errors and lost context
   - Only accelerate when explicitly authorized ("lazy mode")

---

## Project-Specific Rules

### RULE 8: Container Restart Protocol

**Container operations:**

```bash
source ~/.bash_aliases
restartnvr  # Simple restart (docker compose restart)
startnvr    # Full restart with credential reload (./start.sh - docker down/up with AWS credential loading)
```

**Critical notes:**

- Container recreation REQUIRES `./start.sh` for proper loading of credentials for cameras
- AWS uses profile name: 'personal'
- NOTE: AWS credential retrieval often fails in Claude Code environment - ask user to execute if issues occur

### RULE 9: Camera IDs - Always Use Serial Numbers

**In config files:**

- Use serial numbers as primary keys (e.g., `T8416P0023352DA9`)
- Never use display names (e.g., "Living Room") as primary identifiers
- NOTE: Display names acceptable as supplementary metadata (future enhancement for recording_settings.json UI updates)

### RULE 10: MediaMTX Architecture - Tap, Don't Connect

**Streaming source priority:**

- Budget cameras (SV3C, Eufy) support ONE RTSP connection only
- All consumers tap MediaMTX, not cameras directly (EXCEPTION: MJPEG connects directly)
- LL_HLS publishes to MediaMTX (enables RTSP re-export)
- NOTE: Future enhancement needed - WebRTC for further latency optimization

### RULE 11: Code Style - Extensive Documentation

**All code (backend, frontend, and bash scripts):**

- Extensive inline comments
- Docstrings for every class, method, function
- Professional engineering tone (no emojis unless requested)

### RULE 11.5: Camera Credentials Access

**For RTSP or other connectivity tests:**

```bash
source ~/.bash_utils
get_cameras_credentials
```

**Important notes:**

- All REOLINK cameras use the `api-user` user (REOLINK_API_USERNAME/PASSWORD)
- Credentials are loaded from AWS Secrets Manager via the `startnvr` command

---

## Context Management Rules

### RULE 12: On Context Compaction

**When phrase "from a previous conversation that ran out of context" appears:**

1. Immediately commit and push any uncommitted changes
2. Create new branch with next suffix (\_b, \_c, etc.)
3. Update `docs/README_handoff.md`:
   - Note: "Context compaction occurred at [timestamp]"
   - Summarize: What was accomplished, what's pending
4. Continue work on new branch

NOTE: Steps overlap with RULE 0 and RULE 1 intentionally - redundancy ensures critical actions during context transitions

### RULE 13: File Path References

**When referencing code locations:**

- Use markdown link syntax: `[filename.ts:42](src/filename.ts#L42)`
- Make file references clickable for VSCode navigation
- Never use backticks for file paths unless in code blocks
- NOTE: Autoformat handles line breaks and code fencing - maintain proper markdown structure

---

## Quality Control Rules

### RULE 14: Missing Files - Ask, Don't Guess

**If file is missing or empty:**

- Stop and ask user
- Don't create placeholder content
- Don't assume structure

### RULE 15: Security - No Common Vulnerabilities

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
