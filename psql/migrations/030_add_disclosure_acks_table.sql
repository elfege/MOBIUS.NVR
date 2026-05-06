-- 030_add_disclosure_acks_table.sql
--
-- Persistent, queryable record of legal-disclosure acknowledgments.
--
-- Why this table exists
-- =====================
-- Prior to this migration the only record of a disclosure ack lived in
-- /litigation/MANIFEST.jsonl (an append-only hash-chained file). The
-- manifest is the chain-of-custody anchor and remains the legal
-- "tamper-evident" record — but it is not queryable from SQL, not
-- joinable with the users table, and not visible to the UI without a
-- full file scan.
--
-- For the disclosure to bear practical legal value (and to drive UI
-- state — "show this user the box as already-checked because they
-- previously acked the same text") we need a relational row per ack,
-- written transactionally alongside the manifest append.
--
-- The two records are complementary:
--   * MANIFEST.jsonl   — tamper-evident, hash-chained, court-presentable.
--   * evidence_disclosure_acks — fast, joinable, idempotent UI state.
--
-- Both reference each other via manifest_id + manifest_hash so a
-- challenger in court can verify that the DB row matches a manifest
-- entry that itself participates in the chain.
--
-- Append-only by convention
-- =========================
-- We do NOT issue UPDATE/DELETE on this table from any code path.
-- Re-acks (user re-checks the box after the disclosure version bumps,
-- or after switching jurisdiction) become a NEW row. The latest row
-- wins for UI state; older rows remain as a complete audit trail.

BEGIN;

CREATE TABLE IF NOT EXISTS evidence_disclosure_acks (
    id                       BIGSERIAL   PRIMARY KEY,

    -- Who acked. ``user_id`` is whatever stable identifier Flask-Login
    -- exposes (.id, fall back to .username, fall back to "unknown").
    -- Stored as TEXT because the upstream auth backend is opaque to
    -- this schema — it could be an integer PK, a UUID, or a username.
    user_id                  TEXT        NOT NULL,

    -- Network context at ack time. ``client_ip`` honors X-Forwarded-For
    -- when the app is reverse-proxied (nginx-edge). Both fields are
    -- best-effort; "unknown" is permitted.
    client_ip                TEXT        NOT NULL DEFAULT 'unknown',
    user_agent               TEXT        NOT NULL DEFAULT 'unknown',

    -- What was acked. ``jurisdiction`` matches a key in the JS
    -- DISCLOSURES table (US-NY / US-1PARTY / US-2PARTY / OTHER, or any
    -- future addition). ``disclosure_version`` is bumped whenever the
    -- canonical text body changes; older acks remain pinned to their
    -- version. ``disclosure_text_sha256`` is the literal hash of the
    -- text bytes the user saw — proof that this DB row corresponds to
    -- a specific historical text, not a renamed-but-rewritten one.
    jurisdiction             TEXT        NOT NULL,
    disclosure_version       INTEGER     NOT NULL,
    disclosure_text_sha256   TEXT        NOT NULL,

    -- Cross-reference back to the chain-of-custody manifest. ``manifest_id``
    -- is the line number in /litigation/MANIFEST.jsonl. ``manifest_hash``
    -- is that line's ``this_hash`` (SHA-256 of the canonicalized entry
    -- including its previous_hash). With both fields, an auditor can
    -- pull line N of the manifest, recompute its hash, and confirm it
    -- matches this DB row — proving the manifest line is genuine.
    manifest_id              BIGINT      NOT NULL UNIQUE,
    manifest_hash            TEXT        NOT NULL,

    acked_at                 TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE evidence_disclosure_acks IS
    'Append-only DB record of legal-disclosure acknowledgments. Each row '
    'pairs with a manifest_acked entry in /litigation/MANIFEST.jsonl. '
    'Latest row per user_id+jurisdiction wins for UI state.';

-- Indexes:
--   1. (user_id, acked_at DESC) — for the "latest ack for this user"
--      lookup that the status endpoint performs on every page load.
--   2. (jurisdiction, disclosure_version) — for future audit queries
--      ("how many users have acked the US-NY v2 text?").
CREATE INDEX IF NOT EXISTS evidence_disclosure_acks_user_idx
    ON evidence_disclosure_acks(user_id, acked_at DESC);

CREATE INDEX IF NOT EXISTS evidence_disclosure_acks_text_idx
    ON evidence_disclosure_acks(jurisdiction, disclosure_version);

GRANT SELECT, INSERT ON evidence_disclosure_acks TO nvr_api;
GRANT USAGE ON SEQUENCE evidence_disclosure_acks_id_seq TO nvr_api;

-- Read-only access for the anonymous PostgREST role so a future
-- read-only consumer (audit dashboard, etc.) can query without
-- mutation rights. No INSERT — acks must go through Flask so we
-- can pin them to the manifest hash.
GRANT SELECT ON evidence_disclosure_acks TO nvr_anon;

COMMIT;
