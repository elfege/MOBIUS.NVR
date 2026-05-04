"""
Weekly summarizer service — part of the evidence collection package.

Why this lives here (and not in ~/0_SCRIPTS/)
=============================================

Per the user's standing direction (2026-04-28): the weekly summary is
**a feature of the evidence-gathering functionality, not an external
script**. Same way the future people-recognition module, future
Whisper transcriber, future Child-Monitor signal selector all live
inside ``services/evidence/`` — every "intelligence layer" the
evidence pipeline grows belongs in this package.

What this service does
======================

For a given calendar week (Monday 00:00 → Sunday 23:59 in the user's
local timezone, currently America/New_York), the service:

  1. **Gathers raw signal** for that week from three free, local sources:
     - ``git log`` for the project repo (commits + messages)
     - Anamnesis ``/api/episodes/recent`` (semantic memory of work)
     - The evidence manifest (``MANIFEST.jsonl``) — capture events,
       lifecycle events, retention events from this very pipeline
  2. **Builds a prompt** using a canonical template that mirrors the
     hand-written first summary at ``docs/weekly_summaries/2026/04/
     27_to_05_03.md`` (sections: What shipped / What blocked /
     Decisions / Queued / Carried-over TODOs).
  3. **Generates the markdown** by streaming chunks from a GPU-backed
     LLM via ``AnamnesisClient.chat_stream(model="qwen2.5:14b",
     backend="ollama")``. Anamnesis routes that to office's RX 6800.
     Cost per run: $0.
  4. **Archives any existing summary** for the same week (per the
     "preserve before overwrite" directive — old summaries go to
     ``./archive/WeeklySummarizerService/<date>/...``).
  5. **Writes the new summary** to
     ``docs/weekly_summaries/YYYY/MM/DD_to_DD.md`` (cross-month weeks
     file under the start month with both month numbers in the
     filename, per the canonical convention in RULE 19).
  6. **Logs a lifecycle event** to the manifest:
     ``{"event_type": "weekly_summary_generated", ...}``.

Lifecycle
=========

This is a **one-shot** service. ``run()`` does the work and returns;
the worker thread exits. The ``ExtractorSupervisor`` (or any cron-
like scheduler) is responsible for re-spawning it on the next
scheduled tick.

Recommended schedule: Monday 09:00 EDT, summarizing the prior Mon-Sun.
That can be done with a simple ``cron`` entry, or via the existing
NVR scheduling infrastructure once it's wired up.

For ad-hoc use (regenerate this week, generate next week, fill a gap)
construct the service with an explicit ``week_anchor`` date and call
``start()`` (or just ``run()`` synchronously, which is fine for a one-
shot).
"""

# ----- standard library --------------------------------------------------
import os                                       # env vars, path joins
import socket                                   # default instance ID
import subprocess                               # git log invocation
from dataclasses import dataclass, field        # context container
from datetime import date, datetime, timedelta, timezone   # date math
from pathlib import Path                        # all paths are pathlib
from typing import Any, Dict, List, Optional    # type hints

# ----- evidence package internals ----------------------------------------
from services.evidence.base import EvidenceService, PROJECT_ROOT
from services.evidence.anamnesis_client import AnamnesisClient

# ----- module-level constants --------------------------------------------

# Where weekly summaries live, relative to the project root. Per RULE 19
# this directory is intended-untracked (gitignored).
WEEKLY_SUMMARIES_DIR: Path = PROJECT_ROOT / "docs" / "weekly_summaries"

# Default model + backend used when generating the summary text. These
# are also the AnamnesisClient defaults; we re-declare here so changing
# the summarizer's model never requires touching the client.
DEFAULT_MODEL: str = "qwen2.5:14b"
DEFAULT_BACKEND: str = "ollama"

# Cap how many git-log entries we put in the prompt. Long weeks of
# heavy refactoring can produce hundreds of commits; sending all of
# them to the model wastes context and produces less-focused output.
# 80 is plenty for a weekly summary (the first hand-written one had
# under 30 commits in its window).
MAX_GIT_COMMITS_IN_PROMPT: int = 80

# Cap on Anamnesis episodes included as context. Same reasoning.
MAX_EPISODES_IN_PROMPT: int = 30

# Cap on how many of the most-recent manifest entries we summarize
# at the prompt level (we always include aggregate stats; this is the
# detailed sample).
MAX_MANIFEST_SAMPLES_IN_PROMPT: int = 20

# How long to wait between streamed chunks before declaring the
# generator stuck. 14B-parameter models on a busy GPU sometimes pause
# 5-10s between chunks; 180s is generous.
PER_CHUNK_READ_TIMEOUT_SECONDS: float = 180.0


# =========================================================================
# WeekRange — small value object for "the week we're summarizing"
# =========================================================================

@dataclass(frozen=True)
class WeekRange:
    """
    A specific Monday-to-Sunday window expressed as a pair of
    timezone-aware datetimes plus the canonical filename it maps to.

    Frozen because once we've decided which week we're summarizing,
    the answer should not change underneath us mid-run.

    Filename convention (per RULE 19):

      * Same calendar month   → ``DD_to_DD.md``
                                 e.g. ``20_to_26.md``
      * Cross calendar month  → ``DD_to_<MM>_DD.md`` filed under the
                                 START month's directory
                                 e.g. ``27_to_05_03.md`` under ``04/``
    """

    monday_start: datetime          # Mon 00:00:00 (tzinfo-aware, local TZ)
    sunday_end: datetime            # Sun 23:59:59 (tzinfo-aware, local TZ)

    @property
    def filename(self) -> str:
        """Compute the canonical filename for this week range."""
        start_dd = self.monday_start.strftime("%d")
        if self.monday_start.month == self.sunday_end.month:
            # Within one month: just two day numbers.
            end_dd = self.sunday_end.strftime("%d")
            return f"{start_dd}_to_{end_dd}.md"
        # Cross-month: encode end month in the filename so the
        # destination directory (start month) doesn't lose track.
        end_mm = self.sunday_end.strftime("%m")
        end_dd = self.sunday_end.strftime("%d")
        return f"{start_dd}_to_{end_mm}_{end_dd}.md"

    @property
    def directory(self) -> Path:
        """Where the summary file goes: ``YYYY/MM/`` under the start
        Monday's year/month."""
        yyyy = self.monday_start.strftime("%Y")
        mm = self.monday_start.strftime("%m")
        return WEEKLY_SUMMARIES_DIR / yyyy / mm

    @property
    def output_path(self) -> Path:
        """Full path to the markdown file we will write."""
        return self.directory / self.filename

    @property
    def days_span(self) -> int:
        """Always 7 — but useful for documentation / sanity checks."""
        return (self.sunday_end.date() - self.monday_start.date()).days + 1

    @classmethod
    def for_anchor(
        cls,
        anchor: Optional[datetime] = None,
        offset_weeks: int = 0,
    ) -> "WeekRange":
        """
        Build the WeekRange containing ``anchor``, then shift by
        ``offset_weeks`` (negative = past, positive = future).

        Examples
        --------
        ``for_anchor()`` — current week (Mon-Sun) anchored on now.
        ``for_anchor(offset_weeks=-1)`` — last week (the typical
                                          "summarize what just ended"
                                          schedule).
        ``for_anchor(datetime(2026,4,30))`` — the week containing
                                              April 30, 2026.
        """
        # Anchor defaults to "now in local timezone". We need a tz-
        # aware datetime so weekday math is unambiguous; ``astimezone``
        # without args uses the system local timezone.
        if anchor is None:
            anchor = datetime.now().astimezone()
        elif anchor.tzinfo is None:
            anchor = anchor.astimezone()

        # ``weekday()`` returns Mon=0 .. Sun=6. So Monday of the
        # anchor's week is anchor - weekday days.
        days_since_monday = anchor.weekday()
        monday_date: date = (anchor.date()
                             - timedelta(days=days_since_monday)
                             + timedelta(weeks=offset_weeks))
        sunday_date: date = monday_date + timedelta(days=6)

        # Build full datetimes at the boundary times. The local-TZ-aware
        # ``anchor.tzinfo`` is reused so daylight-saving transitions land
        # on the right side of the boundary.
        monday_start = datetime.combine(
            monday_date,
            datetime.min.time(),
            tzinfo=anchor.tzinfo,
        )
        sunday_end = datetime.combine(
            sunday_date,
            datetime.max.time().replace(microsecond=0),
            tzinfo=anchor.tzinfo,
        )
        return cls(monday_start=monday_start, sunday_end=sunday_end)


# =========================================================================
# WeeklyContext — the gathered raw signal we feed into the LLM prompt
# =========================================================================

@dataclass
class WeeklyContext:
    """
    All raw signal gathered for one weekly summary, before we hand it
    to the LLM. Kept in its own dataclass so the gathering logic and
    the prompt-building logic are clearly separated and individually
    testable.
    """

    week: WeekRange

    # Free-form metadata that ends up in the prompt header.
    project_name: str = ""
    instance_id: str = ""
    branch_name: str = ""

    # ``git log`` output — one entry per commit, each a small dict.
    git_commits: List[Dict[str, str]] = field(default_factory=list)

    # Anamnesis episodes recently ingested, optionally project-filtered.
    episodes: List[Dict[str, Any]] = field(default_factory=list)

    # Sample of recent manifest entries plus aggregate stats.
    manifest_recent: List[Dict[str, Any]] = field(default_factory=list)
    manifest_aggregates: Dict[str, int] = field(default_factory=dict)

    # The current handoff content, if a recent one exists.
    handoff_excerpt: str = ""


# =========================================================================
# WeeklySummarizerService — the concrete service
# =========================================================================

class WeeklySummarizerService(EvidenceService):
    """
    One-shot service that produces one weekly summary file.

    Construct with an explicit week (or omit to summarize "last week"),
    call ``start()`` (or ``run()`` directly for synchronous behavior),
    and a markdown file lands in ``docs/weekly_summaries/YYYY/MM/``.

    Typical usage from a scheduler (cron, systemd timer, etc.)::

        # Every Monday at 09:00 local — summarize the prior week.
        svc = WeeklySummarizerService(week_offset=-1)
        svc.run()   # synchronous; no need to start a thread for a
                    # cron job that's already a single shot

    Or from inside the supervisor::

        svc = WeeklySummarizerService(
            manifest=shared_manifest,
            week_offset=-1,
        )
        svc.start()
        # ... do other things ...
        svc.stop()  # waits for run() to finish

    Re-running for the same week (e.g. you want to regenerate after
    fixing a prompt bug) will archive the existing summary file before
    overwriting it. To prevent regeneration, pass ``force=False`` and
    the service will return early if the output already exists.
    """

    def __init__(
        self,
        manifest=None,
        litigation_root=None,
        # Which week to summarize:
        week_anchor: Optional[datetime] = None,
        week_offset: int = -1,                  # default = "last week"
        # Where context comes from:
        project_repo: Path = PROJECT_ROOT,
        project_name: str = "0_MOBIUS.NVR",
        anamnesis_instance_filter: Optional[str] = None,
        # Generation:
        anamnesis: Optional[AnamnesisClient] = None,
        model: str = DEFAULT_MODEL,
        backend: str = DEFAULT_BACKEND,
        # Behavior:
        force: bool = True,                     # regenerate even if file exists
    ) -> None:
        """
        Parameters
        ----------
        manifest, litigation_root:
            Forwarded to ``EvidenceService.__init__``.
        week_anchor:
            A datetime within the desired week. ``None`` means "now".
            Combined with ``week_offset`` to allow "summarize last week"
            without the caller having to compute dates.
        week_offset:
            Weeks before/after ``week_anchor``'s week. Default ``-1``
            ("last week") matches the canonical Monday-morning-cron use
            case. Use ``0`` for the current (in-progress) week.
        project_repo:
            Path to the project's git repo root for ``git log``. Defaults
            to ``PROJECT_ROOT`` (the NVR repo).
        project_name:
            Used as Anamnesis filter (``project=...``) AND as the human-
            readable name in the prompt header. Match the Anamnesis
            ingest project name for accurate retrieval.
        anamnesis_instance_filter:
            Optional ``instance=...`` filter for Anamnesis. ``None``
            means "all instances". Useful if you only want this
            machine's episodes.
        anamnesis:
            Inject a pre-built ``AnamnesisClient`` for testing or
            session reuse. Defaults to a fresh client.
        model, backend:
            Override the LLM model and Anamnesis backend used for
            generation. Defaults are GPU-backed qwen2.5:14b on Ollama.
        force:
            If ``True`` (default), regenerate even if the output file
            already exists (archiving the old one first). If ``False``,
            log a message and return early — useful when running on a
            recurring schedule that you don't want to overwrite manual
            edits with.
        """
        super().__init__(manifest=manifest, litigation_root=litigation_root)

        # Compute the WeekRange immediately so subsequent slow steps
        # (git log, Anamnesis search, generation) all share the same
        # canonical answer for "which week".
        self.week: WeekRange = WeekRange.for_anchor(
            anchor=week_anchor,
            offset_weeks=week_offset,
        )

        self.project_repo: Path = Path(project_repo)
        self.project_name: str = project_name
        self.anamnesis_instance_filter: Optional[str] = anamnesis_instance_filter

        # Re-use a passed-in client; otherwise build our own.
        self.anamnesis: AnamnesisClient = anamnesis or AnamnesisClient()
        self.model: str = model
        self.backend: str = backend
        self.force: bool = force

    # -----------------------------------------------------------------
    # The required EvidenceService.run() implementation
    # -----------------------------------------------------------------

    def run(self) -> None:
        """
        Perform the full one-shot generation pipeline. See module
        docstring for the high-level flow.

        This method is what the worker thread executes when ``start()``
        is called. It is also safe to call synchronously (which is the
        recommended pattern when invoked from cron — no need to spin up
        a thread just to immediately ``join`` it).
        """
        self.log.info("generating weekly summary for %s → %s",
                      self.week.monday_start.date(),
                      self.week.sunday_end.date())
        self.log.info("output target: %s", self.week.output_path)

        # If the file already exists and the user asked us to be polite,
        # bail early. The default is to regenerate, with archiving.
        if self.week.output_path.exists() and not self.force:
            self.log.info(
                "output already exists and force=False; skipping. "
                "Existing file: %s",
                self.week.output_path,
            )
            return

        # ----- Phase 1: gather context (cheap, all local) -----
        ctx = self._gather_context()

        # ----- Phase 2: build the prompt -----
        prompt = self._build_prompt(ctx)

        # ----- Phase 3: generate the markdown via Anamnesis -----
        markdown = self._generate_markdown(prompt)

        # ----- Phase 4: archive any existing file, then write -----
        self.week.output_path.parent.mkdir(parents=True, exist_ok=True)
        if self.week.output_path.exists():
            # Archive helper from the EvidenceService base class.
            self.archive_to(
                self.week.output_path,
                reason="regenerated_by_weekly_summarizer",
            )
        self.week.output_path.write_text(markdown, encoding="utf-8")
        self.log.info("wrote %d chars to %s",
                      len(markdown), self.week.output_path)

        # ----- Phase 5: lifecycle event in the manifest -----
        self.log_lifecycle_event(
            event_type="weekly_summary_generated",
            week_start_utc=self.week.monday_start.astimezone(timezone.utc).isoformat(),
            week_end_utc=self.week.sunday_end.astimezone(timezone.utc).isoformat(),
            output_path=str(self.week.output_path.relative_to(PROJECT_ROOT)),
            output_chars=len(markdown),
            model=self.model,
            backend=self.backend,
            git_commits_used=len(ctx.git_commits),
            episodes_used=len(ctx.episodes),
            manifest_entries_aggregated=sum(ctx.manifest_aggregates.values()),
        )

    # -----------------------------------------------------------------
    # Context gathering — three small, focused private methods
    # -----------------------------------------------------------------

    def _gather_context(self) -> WeeklyContext:
        """
        Build the ``WeeklyContext`` from all raw sources. Each source
        is wrapped in try/except so a single failed source (e.g.
        Anamnesis temporarily down) doesn't kill the whole summary.
        """
        ctx = WeeklyContext(
            week=self.week,
            project_name=self.project_name,
            instance_id=self._guess_instance_id(),
            branch_name=self._read_current_git_branch(),
        )
        # Each gather call is best-effort; failures are logged and the
        # corresponding section just becomes empty in the prompt.
        try:
            ctx.git_commits = self._gather_git_commits()
        except Exception:
            self.log.exception("git log gathering failed; continuing")
        try:
            ctx.episodes = self._gather_anamnesis_episodes()
        except Exception:
            self.log.exception("anamnesis episode fetch failed; continuing")
        try:
            ctx.manifest_recent, ctx.manifest_aggregates = \
                self._gather_manifest_summary()
        except Exception:
            self.log.exception("manifest summary failed; continuing")
        try:
            ctx.handoff_excerpt = self._gather_handoff_excerpt()
        except Exception:
            self.log.exception("handoff excerpt failed; continuing")
        return ctx

    def _gather_git_commits(self) -> List[Dict[str, str]]:
        """
        Run ``git log`` constrained to the week and return one dict per
        commit with hash, ISO timestamp, author, and the first line of
        the message (the "subject"). Capped at MAX_GIT_COMMITS_IN_PROMPT.
        """
        # Format string for git log: tab-separated fields chosen to be
        # easy to split() on. Using %x09 for literal tab and %s for
        # subject (which by definition has no embedded newlines).
        fmt = "%H%x09%aI%x09%an%x09%s"
        cmd = [
            "git", "log",
            f"--since={self.week.monday_start.isoformat()}",
            f"--until={self.week.sunday_end.isoformat()}",
            f"--pretty=format:{fmt}",
            "--no-merges",         # merge commits add noise without info
        ]
        # ``cwd`` matters here — git resolves the repo from the current
        # directory and we want to be unambiguous about which one.
        result = subprocess.run(
            cmd,
            cwd=str(self.project_repo),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            self.log.warning("git log failed rc=%d: %s",
                             result.returncode,
                             result.stderr.strip()[:300])
            return []
        commits: List[Dict[str, str]] = []
        for line in result.stdout.splitlines():
            if not line:
                continue
            parts = line.split("\t", 3)
            if len(parts) != 4:
                continue
            commits.append({
                "hash": parts[0][:12],            # short hash for prompt brevity
                "iso": parts[1],
                "author": parts[2],
                "subject": parts[3],
            })
            if len(commits) >= MAX_GIT_COMMITS_IN_PROMPT:
                break
        return commits

    def _gather_anamnesis_episodes(self) -> List[Dict[str, Any]]:
        """
        Pull recent episodes from Anamnesis (last 7 days), optionally
        filtered by project and instance, capped to
        MAX_EPISODES_IN_PROMPT.

        Note the ``days=7`` here is "the last 7 days from now" per
        Anamnesis's recent endpoint, not "Mon-Sun specifically". For
        the typical "summarize last week" use case those align well; if
        ``week_offset`` were further in the past we'd be over-fetching.
        That's still cheap (the endpoint returns metadata, not full
        bodies) and we'll filter to the actual week below.
        """
        episodes = self.anamnesis.recent_episodes(
            days=7,
            project=self.project_name,
            instance=self.anamnesis_instance_filter,
            limit=MAX_EPISODES_IN_PROMPT * 3,   # over-fetch then filter
        )
        # Try to filter by an "iso" / "created_at" / "timestamp" field
        # if present — the schema isn't 100% stable, so we tolerate
        # several names. If none parse, we fall back to the raw list
        # (we're already capped on size, so this is bounded).
        filtered: List[Dict[str, Any]] = []
        for ep in episodes:
            iso = (ep.get("created_at") or ep.get("timestamp")
                   or ep.get("iso") or ep.get("ingested_at"))
            if not iso:
                filtered.append(ep)
                continue
            try:
                t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
            except ValueError:
                filtered.append(ep)
                continue
            if (self.week.monday_start.astimezone(timezone.utc)
                    <= t.astimezone(timezone.utc)
                    <= self.week.sunday_end.astimezone(timezone.utc)):
                filtered.append(ep)
            if len(filtered) >= MAX_EPISODES_IN_PROMPT:
                break
        return filtered

    def _gather_manifest_summary(
        self,
    ) -> "tuple[List[Dict[str, Any]], Dict[str, int]]":
        """
        Walk the manifest forward and collect:

          * A small sample (most-recent) of entries that fall within
            the week — for the prompt, so the model can quote specific
            events.
          * Aggregate counts per ``event_type`` for the whole week —
            for prompt header context and for the lifecycle log.
        """
        recent: List[Dict[str, Any]] = []
        agg: Dict[str, int] = {}

        # Bound the iteration to UTC since manifest stores UTC strings.
        wk_start_utc = self.week.monday_start.astimezone(timezone.utc)
        wk_end_utc = self.week.sunday_end.astimezone(timezone.utc)

        for entry in self.manifest.iter_entries():
            iso = entry.get("timestamp_utc")
            if not iso:
                continue
            try:
                t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
            except ValueError:
                continue
            if not (wk_start_utc <= t <= wk_end_utc):
                continue
            etype = entry.get("event_type", "unknown")
            agg[etype] = agg.get(etype, 0) + 1
            recent.append(entry)

        # Keep only the tail (most recent) of the recent list within
        # the prompt budget.
        recent = recent[-MAX_MANIFEST_SAMPLES_IN_PROMPT:]
        return recent, agg

    def _gather_handoff_excerpt(self) -> str:
        """
        Read the project's current handoff buffer and return its tail
        — context for the model about what was on Claude's mind at
        end-of-week. Empty string if no handoff or unreadable.

        Caps at ~6000 chars to stay polite on prompt budget.
        """
        handoff = PROJECT_ROOT / "docs" / "README_handoff.md"
        if not handoff.exists():
            return ""
        try:
            text = handoff.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        # Tail rather than head — recent content is usually more relevant.
        return text[-6000:]

    # -----------------------------------------------------------------
    # Prompt building
    # -----------------------------------------------------------------

    def _build_prompt(self, ctx: WeeklyContext) -> str:
        """
        Render the gathered context into a single string prompt suited
        to a 14B chat model.

        Style choices:

          * Section headers in the prompt mirror the section headers we
            want in the output, biasing the model to fill them in.
          * Raw signal is grouped under clear sub-headers and quoted
            literally (no paraphrase) so the model has unambiguous
            facts to cite.
          * The closing "instructions to the assistant" block is short
            and concrete; long instructions cause smaller models to
            wander.
        """
        # ---- header / metadata ---------------------------------------
        lines: List[str] = []
        lines.append(
            f"You are a careful project chronicler writing a weekly "
            f"progress summary for the project **{ctx.project_name}**."
        )
        lines.append("")
        lines.append(
            f"Week being summarized: **"
            f"{ctx.week.monday_start.strftime('%A %Y-%m-%d')} "
            f"through "
            f"{ctx.week.sunday_end.strftime('%A %Y-%m-%d')}**."
        )
        lines.append("")
        lines.append(
            f"Current branch: `{ctx.branch_name or 'unknown'}`. "
            f"Current instance: `{ctx.instance_id or 'unknown'}`."
        )
        lines.append("")

        # ---- raw signal: git commits ---------------------------------
        lines.append("---")
        lines.append("## Raw signal — git commits this week")
        lines.append("")
        if ctx.git_commits:
            for c in ctx.git_commits:
                lines.append(f"- `{c['hash']}` {c['iso']} "
                             f"({c['author']}) {c['subject']}")
        else:
            lines.append("_(no commits this week)_")
        lines.append("")

        # ---- raw signal: Anamnesis episodes --------------------------
        lines.append("## Raw signal — Anamnesis episodes ingested this week")
        lines.append("")
        if ctx.episodes:
            for ep in ctx.episodes:
                eid = ep.get("episode_id", "?")
                summ = (ep.get("summary") or "").strip().replace("\n", " ")
                if len(summ) > 400:
                    summ = summ[:400] + "..."
                lines.append(f"- **{eid}**: {summ}")
        else:
            lines.append("_(no Anamnesis episodes this week)_")
        lines.append("")

        # ---- raw signal: manifest aggregates -------------------------
        lines.append("## Raw signal — evidence-pipeline manifest events this week")
        lines.append("")
        if ctx.manifest_aggregates:
            for etype, count in sorted(ctx.manifest_aggregates.items(),
                                       key=lambda kv: -kv[1]):
                lines.append(f"- `{etype}`: {count}")
        else:
            lines.append("_(no manifest events this week)_")
        lines.append("")

        # ---- raw signal: handoff tail --------------------------------
        if ctx.handoff_excerpt:
            lines.append("## Raw signal — current handoff buffer (tail)")
            lines.append("")
            lines.append("```")
            lines.append(ctx.handoff_excerpt)
            lines.append("```")
            lines.append("")

        # ---- instructions --------------------------------------------
        lines.append("---")
        lines.append("## Your task")
        lines.append("")
        lines.append(
            "Produce a **markdown** weekly summary using EXACTLY the "
            "following section structure (do not invent new sections, "
            "do not omit any):"
        )
        lines.append("")
        lines.append("```markdown")
        lines.append(f"# Weekly Summary — "
                     f"{ctx.week.monday_start.strftime('%Y-%m-%d')} (Mon) "
                     f"to {ctx.week.sunday_end.strftime('%Y-%m-%d')} (Sun)")
        lines.append("")
        lines.append("> Auto-generated by WeeklySummarizerService "
                     f"(model={self.model}, backend={self.backend}).")
        lines.append("")
        lines.append("## What shipped")
        lines.append("(Concrete deliverables landed this week. Group "
                     "by feature, link commits by short hash where "
                     "useful. No fluff, no marketing-speak.)")
        lines.append("")
        lines.append("## What blocked")
        lines.append("(Open questions, dependencies on other people, "
                     "infrastructure issues. Empty section is fine if "
                     "nothing blocked.)")
        lines.append("")
        lines.append("## Decisions made")
        lines.append("(Architectural / scope / process decisions, with "
                     "the reason. Future-you should be able to recover "
                     "the WHY from this section alone.)")
        lines.append("")
        lines.append("## Queued for next")
        lines.append("(Specific, actionable. What the next session will "
                     "open with.)")
        lines.append("")
        lines.append("## Carried-over TODOs")
        lines.append("(Items from prior weeks that did NOT advance "
                     "this week. Be honest about what slipped.)")
        lines.append("```")
        lines.append("")
        lines.append("Constraints:")
        lines.append("- Use only facts present in the raw signal above.")
        lines.append("- If a section has no content, write `_(none this week)_` rather than padding.")
        lines.append("- Do not include the raw signal verbatim — synthesize it.")
        lines.append("- Output ONLY the markdown summary; no preamble, no commentary, no closing remarks.")
        return "\n".join(lines)

    # -----------------------------------------------------------------
    # Generation — stream chunks from Anamnesis and assemble
    # -----------------------------------------------------------------

    def _generate_markdown(self, prompt: str) -> str:
        """
        Stream the LLM response into a single string.

        We accumulate chunks rather than streaming-to-disk because the
        output is small (a few KB at most) and the markdown only makes
        sense as a complete document. If we ever target very long
        outputs, adapt this to write incrementally to a temp file and
        rename on success.
        """
        self.log.info("starting generation: model=%s backend=%s prompt_chars=%d",
                      self.model, self.backend, len(prompt))
        chunks: List[str] = []
        for chunk in self.anamnesis.chat_stream(
            message=prompt,
            model=self.model,
            backend=self.backend,
            top_k=0,                 # no extra RAG — we built the context ourselves
            per_chunk_read_timeout=PER_CHUNK_READ_TIMEOUT_SECONDS,
        ):
            chunks.append(chunk)
            # If the user asked us to stop mid-generation, drop the
            # connection cleanly. The ``with`` block in the client
            # handles HTTP-level cleanup.
            if self._stop.is_set():
                self.log.info("stop requested mid-generation; truncating")
                break
        result = "".join(chunks).strip()
        if not result:
            # Defensive: empty output likely means the model server hit
            # an error we didn't surface. Substitute a clear marker so
            # the file isn't silently empty and the operator notices.
            self.log.warning("generation returned empty string; "
                             "writing diagnostic placeholder")
            result = (
                "# Weekly Summary — generation failed\n\n"
                "_The LLM returned no content. The raw signal was "
                "gathered successfully but generation produced an "
                "empty response. Investigate the Anamnesis app "
                "logs (model={}, backend={}).\n_".format(
                    self.model, self.backend,
                )
            )
        return result

    # -----------------------------------------------------------------
    # Tiny helpers
    # -----------------------------------------------------------------

    def _read_current_git_branch(self) -> str:
        """Best-effort current-branch read; empty string on failure."""
        try:
            r = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=str(self.project_repo),
                capture_output=True,
                text=True,
                timeout=5,
            )
            return r.stdout.strip()
        except Exception:
            return ""

    @staticmethod
    def _guess_instance_id() -> str:
        """
        Return the project-specific instance ID per the intercom
        registry convention (machine-project), or fall back to
        ``socket.gethostname()`` if no env hint is set.

        We deliberately don't import intercom logic here — the registry
        lives on a different machine and SSH'ing for it during a
        weekly-summary run would be fragile. Env override is the clean
        injection point.
        """
        explicit = os.environ.get("CLAUDE_INSTANCE_ID")
        if explicit:
            return explicit
        return socket.gethostname()


# =========================================================================
# CLI entrypoint — for direct invocation from cron / manual runs
# =========================================================================

def main() -> int:
    """
    Run one weekly summary generation from the command line.

    Default is "summarize last week" (``week_offset=-1``), which is
    what the Monday-morning cron should call.

    Examples
    --------
    Last week (default cron behavior)::

        python -m services.evidence.weekly_summary

    Current (in-progress) week::

        python -m services.evidence.weekly_summary --offset 0

    Specific week containing a given date::

        python -m services.evidence.weekly_summary --anchor 2026-04-15

    Don't overwrite if already exists::

        python -m services.evidence.weekly_summary --no-force
    """
    import argparse
    import logging as _logging

    p = argparse.ArgumentParser(
        description="Generate a weekly project summary.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--offset", type=int, default=-1,
                   help="weeks before/after the anchor (default -1 = last week)")
    p.add_argument("--anchor",
                   help="ISO date inside the desired week (default = today)")
    p.add_argument("--project", default="0_MOBIUS.NVR",
                   help="Anamnesis project name to filter episodes by")
    p.add_argument("--model", default=DEFAULT_MODEL,
                   help=f"LLM model (default {DEFAULT_MODEL})")
    p.add_argument("--no-force", action="store_true",
                   help="skip if output file already exists")
    args = p.parse_args()

    _logging.basicConfig(
        level=os.environ.get("NVR_LOG_LEVEL", "INFO"),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    anchor: Optional[datetime] = None
    if args.anchor:
        anchor = datetime.fromisoformat(args.anchor).astimezone()

    svc = WeeklySummarizerService(
        week_anchor=anchor,
        week_offset=args.offset,
        project_name=args.project,
        model=args.model,
        force=not args.no_force,
    )
    svc.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
