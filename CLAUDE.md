# Claude Code Instructions for dDMSC Project

## 1. Session Management

### 1.1 Startup Checklist

At the start of EVERY message, verify:

- **1.1.1** Context compaction? (phrase: "from a previous conversation that ran out of context") → if yes, follow 1.2
- **1.1.2** Have I read this file (`CLAUDE.md`)? Never assume you know enough: always re-read it. You often violate rules. Be paranoïd. 
- **1.1.3** Have I verified network environment (`hostname` → VFC-3000 IP)?
- **1.1.4** Am I referencing rule numbers when making decisions?
- **1.1.5** Have I verified if we are running from hostname:`pmx-dstrm-app1` (Mindhop Office VM) or hostname:`server` (192.168.10.15 - Home machine)? 
  - **1.1.5.1** Rule 13. 

#### 1.1.5 Core Rules Summary (NEVER forget)

**1.1.5.1** Rules and Constraints 

| Rule | Constraint                                                                     |
|------|--------------------------------------------------------------------------------|
| 5.1  | API has NO direct DB access. All data through PostgREST. NEVER use psycopg2.   |
| 2.1  | Commit early/often. Never merge broken code to main.                           |
| 4.1  | NEVER modify code you haven't read first.                                      |
| 11.1 | NEVER start/stop/rebuild Docker or apply DB changes without user permission.   |
| 5.2  | NEVER create/alter/drop tables directly. Use Flyway.                           |
| 3.1  | Update README_handoff.md after EVERY file modification.                        |
| 7.1  | One hypothesis at a time, verify before next step.                             |

**1.1.5.2** when editing/creating a markdown table keep things aligned as I can't always read in rendered mode

### 1.2 Context Compaction Protocol

When phrase "from a previous conversation that ran out of context" appears:

- **1.2.1** Immediately commit and push any uncommitted changes
- **1.2.2** Create new branch with next suffix (_b, _c, etc.)
- **1.2.3** Update `README_handoff.md`: note compaction timestamp, summarize accomplished/pending
- **1.2.4** Execute Rule 13
- **1.2.5** Continue work on new branch

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

`README_project_history.md` is tracked. `README_handoff.md` is **UNTRACKED** (in `.gitignore`) — NEVER add it back to git.

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
- **2.3.5** Port `README_handoff.md` contents into `README_project_history.md`
- **2.3.6** Archive handoff to `history/handoffs/[branch_name_dir]/README_handoff_[timestamp].md`
- **2.3.7** Wipe original handoff preserving structure and unchecked TODO items
- **2.3.8** Edit `README_handoff.md` with pointers to `README_project_history.md` for smooth transition
- **2.3.9** Update technical documentation in `docs_shared/`
- **2.3.10** Execute Rule 13

### 2.4 Commit Messages

- **2.4.1** Professional, descriptive messages only
- **2.4.2** NO Anthropic attribution ("Co-Authored-By: Claude...", "Generated with Claude Code")
- **2.4.3** NO emojis or icons
- **2.4.4** DO NOT mention CLAUDE.md in commits (it's in .gitignore)
- **2.4.5** Include Jira ticket number: `<description> (DS-XXXX)` (see 9.2)

---

## 3. Documentation

### 3.1 Session Tracking

#### 3.1.1 After Every File Modification

Update `README_handoff.md` with:

- **3.1.1.1** File changed, what was done, why
- **3.1.1.2** Timestamps: `date + time (EST)` — 24h format
- **3.1.1.3** Verify system time with `date` command (Claude Code clock drift is common)
- **3.1.1.4** Update session end-times when adding entries (e.g., `(12:30-13:15)` → `(12:30-14:00)`)

#### 3.1.2 When Task Is Complete

- **3.1.2.1** Port to `README_project_history.md`
- **3.1.2.2** After user confirms, clear completed session from handoff
- **3.1.2.3** Keep TODO list at end of both files (even when wiping handoff)
- **3.1.2.4** TODO list in `README_project_history.md` must never be re-edited, just completed

### 3.2 Project Context & Locations

#### 3.2.1 Read at Conversation Start

- **3.2.1.1** `CLAUDE.md` — these instructions (override all defaults)
- **3.2.1.2** `README_handoff.md` — recent session history
- **3.2.1.3** `~/0_CLAUDE_IC/user_profile_elfege.md` — persistent user profile (background, intellectual style, communication preferences). Never make the user re-explain his history.

#### 3.2.2 Document Locations

**3.2.2.1** Names and relative paths

| Document            | Location                                                                |
|---------------------|-------------------------------------------------------------------------|
| Project history     | `docs_shared/project/README_project_history.md`                         |
| Session handoff     | `README_handoff.md` (project root)                                      |
| Chat logs           | `chat.md` (project root)                                                |
| Docs portal         | `docs_shared/index.html`                                                |
| Technical reference | `docs_shared/reference/README_dDMS_Manufacturers_Detailed_Reference.md` |
| IRIS reference      | `docs_shared/reference/README_IRIS_implementation.md`                   |
| Stack architecture  | `docs_shared/architecture/README_dDMSC_stack_description.md`            |
| Engineering arch    | `docs_shared/architecture/dDMSC_engineering_architecture.html`          |
| Arch presentation   | `docs_shared/architecture/presentation_dDMSC_architecture.html`         |
| OID/Vendor profiles | `docs_shared/architecture/README_OID_Vendor_Profile_Design.md`          |
| API reference       | `docs_shared/api/README_dDMSC_API.md`                                   |
| Database docs       | `docs_shared/database/README_dDMSC_Database.md`                         |
| E2E test docs       | `docs_shared/tests/README_dDMSC_E2E_Test.md`                            |
| Daktronics docs     | `docs_shared/daktronics/docs/`                                          |
| UI requirements     | `docs_shared/ui_requirements/`                                          |
| Intercom            | `~/0_CLAUDE_IC/intercom.md`                                             |
| Decision log        | Confluence (coordinate with Dom)                                        |
| Requirements        | Jira tickets (source of truth)                                          |

**3.2.2.2** when editing/creating a markdown table keep things aligned as I can't always read in rendered mode

### 3.3 File Naming Conventions

#### 3.3.1 Documentation Files in `docs_shared/`

- **3.3.1.1** Must start with `README_` prefix
- **3.3.1.2** Project-related: use `dDMS_` or `dDMSC_` prefix (not `DMS_`)
- **3.3.1.3** Pattern: `README_dDMSC_[description].md`

#### 3.3.2 Exceptions

`_config.yml`, `README.md`, `index.html`, image files

### 3.4 Technical Documentation Updates

When making significant code changes, update:

- **3.4.1** `docs_shared/architecture/dDMSC_engineering_architecture.html` — components, data flow, env vars
- **3.4.2** `docs_shared/api/README_dDMSC_API.md` — endpoints, schemas, changelog
- **3.4.3** `docs_shared/architecture/README_dDMSC_stack_description.md` — architecture, directory structure
- **3.4.4** `docs_shared/architecture/presentation_dDMSC_architecture.html` — status, features
- **3.4.5** `docs_shared/index.html` — **ALWAYS update when creating new docs** (add doc-card with title, description, tags)
- **3.4.6** Update "Last Updated" dates when modifying documentation files

### 3.5 API Documentation

- **3.5.1** Update `docs_shared/api/README_dDMSC_API.md` when adding/modifying API endpoints
- **3.5.2** Include: endpoint, method, request/response format, parameters, examples
- **3.5.3** Keep changelog at bottom updated

### 3.6 File Path References

When referencing code locations in chat:

- **3.6.1** Use markdown link syntax: `[filename.py:42](filename.py#L42)`
- **3.6.2** Make file references clickable for VSCode navigation
- **3.6.3** Never use backticks for file paths unless in code blocks

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
- **4.3.3** Professional engineering tone (no emojis)
- **4.3.4** Protocol-specific comments explaining NTCIP objects, OIDs, and message formats

---

## 5. Data Architecture & Database

### 5.1 PostgREST-Only Data Access

**CRITICAL:** The API container communicates with PostgreSQL exclusively through PostgREST.

- **5.1.1** **NEVER** use `psycopg2`, `asyncpg`, `sqlalchemy`, or any direct DB driver in API code
- **5.1.2** **NEVER** add DB driver packages to `requirements.txt`
- **5.1.3** **ALL reads** go through PostgREST views/tables (HTTP GET)
- **5.1.4** **ALL writes** go through PostgREST RPC functions (HTTP POST to `/rpc/<function_name>`)
- **5.1.5** The `requests` library (already installed) is the only DB client the API needs

#### 5.1.6 Why

Production containers will not have direct DB network access. PostgREST is the data gateway. This applies to dev AND production — no exceptions.

#### 5.1.7 Data Team Contact

Daniel or Joe (DB schema, migrations, Flyway, PostgREST conventions) | preferably Daniel. 

#### 5.1.8 Write Pattern

```python
response = requests.post(
    f"{POSTGREST_URL}/rpc/create_vms_device",
    json={"p_data": data},
    timeout=10
)
```

### 5.2 Flyway Migrations

**CRITICAL:** Never create, alter, or drop tables directly in the database. This will break migration history and affect the entire company.

- **5.2.1** **Database Migration Repository:** `ssh app1:dotstream-db`
  - **5.2.1.1** Migration File Version Naming 
    - **5.2.1.1.1** Execute steps 5.3.1 through 5.3.2.3 to determine latest version number 
    - **5.2.1.1.2** When you create a new version file, increment value accordingly.

- **5.2.2** **Tool:** Flyway (executed via GitHub Actions)
- **5.2.3** **Version folder:** `dotstream-db/migrations/versions/v5/`
- **5.2.4** **File naming:** `V5.x.x__description_with_underscores.sql`
- **5.2.5** **Local development:** Write migration SQL in `dDMSC/migrations/` for review first. Copy to dotstream-db only after user approval.

### 5.3 Migration Workflow (dotstream-db)

**When user asks you to take on a migration:**

- **5.3.1** `cd ~/dotstream-db/`
- **5.3.2** `git status` — verify you're on `main`
  - **5.3.2.1** If NOT on main → **stop and ask user** for next steps
  - **5.3.2.2** If on main but NOT up to date with origin/main → **stop and warn user**
  - **5.3.2.3** If tree is clean and up to date → `git pull origin main`
- **5.3.3** Create branch: `elfegesMind_[project]_[description]_[MON_DD_YYYY]`
  - Example: `elfegesMind_dDMSC_vms_device_crud_FEB_12_2026`
- **5.3.4** Copy migration files into their respective directories in `~/dotstream-db/`
- **5.3.5** Commit and push the feature branch
- **5.3.6** Checkout back to main. **DO NOT MERGE.**



#### 5.3.7 dotstream-db Repository Rules

- **5.3.7.1** NEVER edit files directly in `~/dotstream-db` except during the workflow above
- **5.3.7.2** NEVER merge to main in dotstream-db
- **5.3.7.3** NEVER push to main in dotstream-db
- **5.3.7.4** The user (or CI) handles merges and main pushes

### 5.4 Repeatable Migration Directory Structure

Per Daniel's convention in `dotstream-db/migrations/repeatable/apids/`:

| Directory | Contents | When to Use |
|-----------|----------|-------------|
| `views/` | Standalone views | View-only SQL, e.g. `R__apids_oids.sql` |
| `functions/` | Standalone functions | Functions that don't depend on custom views |
| `interdependent_groups/` | Views + functions together | When functions use the view's return type or CRUD + view in same file, e.g. `R__apids_vms_devices.sql` |

- **5.4.1** **Local mirror:** `dDMSC/migrations/repeatable/apids/` follows the same structure.

#### 5.4.2 apids View Naming Convention

Per Joe (data team): **no "tbl" prefix on apids views.** The `tbl` prefix is a staticds/dynamicds table convention, not an apids view convention.

- **5.4.2.1** Underlying table: `staticds.tbloid` (with "tbl") → View: `apids.oids` (no "tbl")
- **5.4.2.2** Underlying table: `staticds.tbldevice` → View: `apids.vms_devices` (no "tbl")
- **5.4.2.3** Pattern: Strip the `tbl` prefix and use a descriptive plural name for the view
- **5.4.2.4** File naming follows the view name: `R__apids_<view_name>.sql`

---

## 6. Security & Safety

### 6.1 Traffic Safety Infrastructure

This system will control traffic safety infrastructure. Design for safety and reliability from the start.

- **6.1.1** Consider failure modes and error handling
- **6.1.2** Document all protocol implementations thoroughly
- **6.1.3** Be especially careful with DMS protocols, message validation, network communication, state management

### 6.2 Code Security

- **6.2.1** Avoid OWASP Top 10 vulnerabilities (command injection, XSS, SQL injection, etc.)
- **6.2.2** If insecure code written, fix immediately AND inform user
- **6.2.3** Validate/sanitize at system boundaries only (user input, external APIs, DMS protocols) — trust internal code
- **6.2.4** Never hardcode credentials — use environment variables (.env in .gitignore)
- **6.2.5** Implement connection timeouts and retry logic
- **6.2.6** Be especially careful with SNMP queries, network sockets, message parsing, file operations

---

## 7. Debugging

### 7.1 Hypothetico-Deductive Method

- **7.1.1** Formulate ONE specific hypothesis (edge case: multiple hypotheses OK when they form mutual refutations)
- **7.1.2** Apply 4.1 (read files first if hypothesis involves code)
- **7.1.3** Execute verification commands/tests directly
- **7.1.4** Wait for results before next hypothesis
- **7.1.5** Move step-by-step at human-followable pace — only accelerate when explicitly authorized

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
- **8.2.6.3** Protocol implementation changes
- **8.2.6.4** PRs or production pushes

### 8.3 Testing Resources

- **8.3.1** NTCIP 1203 simulators (see DMS_Manufacturers_Detailed_Reference.md)
- **8.3.2** Daktronics VFC-3000 (physical device)
- **8.3.3** Web LCD Simulator (in this app)
- **8.3.4** IRIS simulator (to be verified)

---

## 9. dotstream Process

### 9.1 Development Workflow

#### 9.1.1 Team

| Role | Person |
|------|--------|
| Product Manager | Sam Blaisdell |
| Database Schema | Joe |
| Data/Migrations | Daniel |
| Architecture Review | Dom (must approve before implementation) |
| Code Review | John or Joe |
| Developer | Elfege Leylavergne |
| IT Admin | Donald |

#### 9.1.2 Design Process

- **9.1.2.1** Prepare architecture diagram and endpoint spec
- **9.1.2.2** Review with Dom for approval
- **9.1.2.3** Database changes: coordinate with Joe and Daniel
- **9.1.2.4** Code review by John or Joe before merging

#### 9.1.3 Standards

- **9.1.3.1** Clear, concise documentation
- **9.1.3.2** Professional engineering tone
- **9.1.3.3** API documentation required for all endpoints

### 9.2 Jira Integration

- **9.2.1** Enquire existing tickets using `~/.bash_utils` & Jira CLI (you have the token in memory. Enquire if missing.)
- **9.2.2** Include ticket number in commits: `<description> (DS-XXXX)`
- **9.2.3** Reference tickets in code comments where relevant
- **9.2.4** If no ticket exists, create a new one within existing Epic and inform user. 

#### 9.2.4 Active Tickets

| Ticket | Description |
|--------|-------------|
| DS-2014 | Main dDMSC project |
| DS-2022 | Flask API skeleton |
| DS-2023 | Database schema documentation |
| DS-2031 | IRIS implementation reference |
| DS-2036 | snmpsim mock DMS setup |
| DS-2037 | SNMP client layer |
| DS-2072 | Kafka Integration |
| DS-2073 | Python App Scheduler |
| DS-2080 | Create staticds.tblvms |
| DS-2115 | Advanced Testing UI Features |
| DS-2180 | Create staticds.tblvmsmodel catalog (subtask of DS-2023) — UNIT TESTING |

#### 9.2.5 Status Workflow

Never mark tickets directly as DONE. Completed code goes to UNIT TESTING first; QA/review moves through to DONE.

---

## 10. Communication

### 10.1 Truth First

- **10.1.1** We are engineers. Truth first, even when blunt.
- **10.1.2** NEVER say "You're correct" then immediately contradict
- **10.1.3** If you disagree, state it directly without apologetic preambles
- **10.1.4** Technical accuracy trumps social niceties
- **10.1.5** Proactively suggest better approaches when you see an opportunity

### 10.2 Missing Files — Ask, Don't Guess

- **10.2.1** If file is missing or empty: stop and ask user
- **10.2.2** Don't create placeholder content
- **10.2.3** Don't assume structure

---

## 11. Infrastructure

### 11.1 Container Commands — User Only

**NEVER execute without explicit user permission:**

- **11.1.1** `./start.sh`, `./build.sh`, `./deploy.sh`
- **11.1.2** `docker compose up/build/down`
- **11.1.3** `docker build/run/stop`
- **11.1.4** Any command that starts, stops, or rebuilds containers
- **11.1.5** Any command that applies SQL to the database (psql, migration scripts, etc.)

#### 11.1.6 Instead

Inform user what needs to happen, provide the exact command, wait for confirmation.

#### 11.1.7 Why

AWS secrets must be loaded via `./start.sh`. Running docker compose directly bypasses secret injection.

---

## 12. DMS Protocol Standards

### 12.1 NTCIP 1203

- **12.1.1** Follow NTCIP 1203 v3 standard for DMS control
- **12.1.2** SNMP-based protocol over TCP/IP or serial
- **12.1.3** Multi-string message support
- **12.1.4** Pixel matrix addressing for graphics
- **12.1.5** Status monitoring (temperature, power, errors)

### 12.2 Daktronics SDK

- **12.2.1** Primary vendor SDK focus
- **12.2.2** LCD Simulator for testing
- **12.2.3** Reference: Daktronics Support KB DD2742206
- **12.2.4** IRIS simulator (Sam to provide)

### 12.3 Manufacturer Variations

- **12.3.1** Some manufacturers use proprietary NTCIP extensions
- **12.3.2** Cloud-based APIs (Wanco, TraffiCalm) require different approach
- **12.3.3** Document all manufacturer-specific quirks

---

## 13. Memory Sync (Invocable Rule)

**Trigger:** User says "sync memory" or "Rule 13"

Sync Claude Code auto-memory files between `server` (home) and `app1` (office).

### 13.1 Paths

| Machine | MEMORY.md Path |
|---------|---------------|
| `server` (home) | `~/.claude/projects/-home-elfege-dDMSC/memory/` |
| `app1` (office) | `/home/ubuntu/.claude/projects/-home-ubuntu-dDMSC/memory/` |

### 13.2 Procedure

- **13.2.1** Read the local memory directory: `ls ~/.claude/projects/-home-elfege-dDMSC/memory/`
- **13.2.2** SSH to app1 and list remote memory directory:
  - From `server`: `ssh -J office app1 "ls /home/ubuntu/.claude/projects/-home-ubuntu-dDMSC/memory/"`
  - From `officewsl`: `ssh app1 "ls /home/ubuntu/.claude/projects/-home-ubuntu-dDMSC/memory/"`
- **13.2.3** Diff each file between local and remote
- **13.2.4** Merge: prefer the more recent/comprehensive version. If both have unique content, merge and keep both contributions.
- **13.2.5** Write merged result locally
- **13.2.6** SCP merged files to app1:
  - From `server`: `scp -o ProxyJump=office <local_file> app1:<remote_path>`
  - From `officewsl`: `scp <local_file> app1:<remote_path>`
- **13.2.7** MEMORY.md must stay under 200 lines. Move detailed sections into separate topic files and link from MEMORY.md.
- **13.2.8** Update `**Last synced:**` date at the bottom of MEMORY.md

---

## 14. Jira Issue Management

**When creating new Jira issues (epics or stories) via jira-cli:**

### 14.1 Assignment

- **14.1.1** Always assign to user (Elfege Leylavergne) unless explicitly asked otherwise
- **14.1.2** Use `--assignee` flag with jira-cli create command

### 14.2 Required Fields

- **14.2.1 Priority:** Estimate based on task urgency and impact
  - **Highest:** Blocking work, critical bugs, security issues
  - **High:** Important features, performance issues
  - **Medium:** Standard features, improvements (default)
  - **Low:** Nice-to-have, future enhancements

- **14.2.2 Story Points:** Estimate complexity/effort (Fibonacci scale)
  - **1:** Trivial (< 1 hour)
  - **2:** Simple (1-2 hours)
  - **3:** Moderate (half day)
  - **5:** Complex (1 day)
  - **8:** Very complex (2-3 days)
  - **13:** Large (1 week)
  - **21+:** Epic-sized, should be broken down

- **14.2.3 Due Date:** Best estimate based on priority and story points
  - **Highest/High:** Within 1-3 days
  - **Medium:** Within 1 week
  - **Low:** Within 2 weeks or more

### 14.3 Sprint Assignment

- **14.3.1** For **epics**: Ask user which sprint to assign
- **14.3.2** For **stories/subtasks**: Assign to current active sprint (if known) or ask user

### 14.4 Example jira-cli Command

```bash
export JIRA_API_TOKEN="<token>"
jira-cli issue create \
  --type Story \
  --project DS \
  --summary "Feature summary" \
  --body "Detailed description" \
  --assignee "Elfege Leylavergne" \
  --priority Medium \
  --story-points 5 \
  --due-date "2026-02-20"
```

---

## 15. Inter-Instance Communication

### 15.1 Intercom File

- **15.1.1** Location: `~/0_CLAUDE_IC/intercom.md`
- **15.1.2** This instance's ID: `server-dDMSC`
- **15.1.3** Read the intercom file at session start when working on infrastructure, networking, or connectivity issues.
- **15.1.4** When reading PENDING messages addressed to `server-dDMSC`, act on them and update status to `ACK`.
- **15.1.5** When making changes that affect other machines (start.sh, tunnel expectations, port requirements), post a message.

### 15.2 Writing Messages

Append to `~/0_CLAUDE_IC/intercom.md`:

```
### MSG-NNN
- **Timestamp:** [ISO 8601]
- **From:** server-dDMSC
- **To:** [target instance]
- **Subject:** [brief]
- **Status:** PENDING

[content]

---
```

### 15.3 Instance Registry

| Instance ID | Machine | IP | Role |
|-------------|---------|-----|------|
| `office` | office/officewsl | 192.168.10.110 | Windows machine with VPN, SSH tunnels, port proxy |
| `server-dDMSC` | server | 192.168.10.15 | dDMSC Docker stack, dev environment |

---

## Reference: Network Environment

**Directories synced via rsync between work and home. Codebase identical, runtime differs.**

```bash
hostname  # Check which environment
```

| Hostname | Environment | Path | VFC-3000 IP |
|----------|-------------|------|-------------|
| `officewsl` | Work | `/home/ubuntu/dDMSC` | `172.20.10.99` |
| `server` | Home | `/home/elfege/dDMSC` | `192.168.10.146` |

- Do NOT hardcode IPs — use environment variables or config
- Database connection same in both (AWS RDS via VPN)

## Reference: System Overview

**dDMSC** (dotstream DMS Control) is a backend API module that interfaces with vendor SDKs/drivers to post messages to field Dynamic Message Signs (DMS). Part of the dotstream command & control platform.

**Jira:** DS-2014 | **Location:** `/home/ubuntu/dDMSC` | **Repo:** https://github.com/elfegesMind/dotstream-DMS-control.git

**Stack:** Python REST API, PostgREST, Kafka, NTCIP 1203, Daktronics SDK

**Device types:** Full Matrix, Line-Matrix, Graphics Enabled, Travel Time, Portable, Hybrid/Lane Control

## Reference: Architecture Components

1. **Protocol Layer** — NTCIP 1203 SNMP, manufacturer adapters, serial/TCP transport
2. **Business Logic** — Message composition, sign routing, scheduling, status monitoring
3. **API Layer** — REST API, auth, rate limiting, request validation
4. **Data Layer** — Sign inventory, message history, audit logs, system state (all via PostgREST)

---

**Version:** 2.2
**Last Updated:** 2026-02-14 (EST)
**System:** dDMSC - dotstream DMS Control System
**Location:** /home/ubuntu/dDMSC
**Repository:** <https://github.com/elfegesMind/dotstream-DMS-control.git>

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
