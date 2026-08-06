"""DB helpers for public.pipeline_jobs — the continuous-enrich work queue (migration
152; spec docs/superpowers/specs/2026-07-10-pipeline-jobs-continuous-enrich-design.md).

Conn-first: every helper takes an open connection and the CALLER owns the txn
(same convention as db/generic_deep_runs.py, db/candidates.py). All SQL uses
text() with bound params; intervals use make_interval (no bare `%` anywhere,
though these run with real params so it's moot here — the migration is the
placeholder-sensitive path).
"""
from sqlalchemy import text


def enqueue_jobs(conn, kind: str, advertiser_ids) -> int:
    """Bulk-insert `kind` rows for `advertiser_ids` IN THE GIVEN ORDER (FIFO drain
    order = taste priority). `ON CONFLICT ... DO NOTHING` against the partial
    active-unique index (kind, advertiser_id) WHERE status IN ('queued','running')
    dedups any advertiser that already has a queued/running job OF THIS KIND — a
    different kind for the same advertiser is a distinct queue slot. Returns the
    count actually inserted (i.e. minus the deduped ones)."""
    ids = list(advertiser_ids)
    if not ids:
        return 0
    rows = conn.execute(text(
        "INSERT INTO public.pipeline_jobs (kind, advertiser_id) "
        "SELECT :kind, a.aid "
        "FROM unnest(CAST(:aids AS bigint[])) WITH ORDINALITY AS a(aid, ord) "
        "ORDER BY a.ord "
        "ON CONFLICT (kind, advertiser_id) WHERE status IN ('queued','running') "
        "DO NOTHING "
        "RETURNING id"),
        {"kind": kind, "aids": ids}).fetchall()
    return len(rows)


def enqueue_enrich(conn, advertiser_ids) -> int:
    """kind='enrich' wrapper — keeps every existing call site unchanged."""
    return enqueue_jobs(conn, "enrich", advertiser_ids)


def claim_next(conn, kind: str, lease_sec: int):
    """SKIP-LOCKED claim of the oldest DUE (run_after <= now) queued row of `kind`:
    flip it to running, attempts+1, claimed_at=now(), lease_expires_at=now()+lease.
    Returns (id, advertiser_id) or None when nothing is claimable."""
    row = conn.execute(text(
        "WITH nxt AS ("
        "  SELECT id FROM public.pipeline_jobs "
        "  WHERE kind = :kind AND status = 'queued' AND run_after <= now() "
        "  ORDER BY run_after, id "
        "  FOR UPDATE SKIP LOCKED "
        "  LIMIT 1"
        ") "
        "UPDATE public.pipeline_jobs j "
        "SET status = 'running', attempts = attempts + 1, claimed_at = now(), "
        "    lease_expires_at = now() + make_interval(secs => :lease) "
        "FROM nxt WHERE j.id = nxt.id "
        "RETURNING j.id, j.advertiser_id"),
        {"kind": kind, "lease": lease_sec}).fetchone()
    return (row[0], row[1]) if row is not None else None


def reap_expired(conn, kind: str) -> int:
    """Recover running rows whose lease expired (daemon crash / wedge): back to
    'queued' for a retry, or 'error' once attempts >= max_attempts. Clears the
    lease either way. Returns the number of rows reaped."""
    return conn.execute(text(
        "UPDATE public.pipeline_jobs "
        "SET status = CASE WHEN attempts >= max_attempts THEN 'error' ELSE 'queued' END, "
        "    lease_expires_at = NULL, "
        "    error_text = CASE WHEN attempts >= max_attempts "
        "                      THEN COALESCE(error_text, 'lease expired: max attempts') "
        "                      ELSE error_text END, "
        "    finished_at = CASE WHEN attempts >= max_attempts THEN now() ELSE finished_at END "
        "WHERE kind = :kind AND status = 'running' AND lease_expires_at < now()"),
        {"kind": kind}).rowcount


def set_job_status(conn, job_id: int, status: str, error: str | None = None) -> None:
    """Terminal (or intermediate) status write for one job. On a terminal status
    (done/error) it stamps finished_at and clears the lease."""
    conn.execute(text(
        "UPDATE public.pipeline_jobs "
        "SET status = :status, error_text = :error, "
        "    finished_at = CASE WHEN :status IN ('done','error') THEN now() ELSE finished_at END, "
        "    lease_expires_at = CASE WHEN :status IN ('done','error') THEN NULL ELSE lease_expires_at END "
        "WHERE id = :id"),
        {"status": status, "error": error, "id": job_id})


def set_job_payload_key(conn, job_id: int, key: str, value: str) -> None:
    """Write one string key into a job's payload jsonb. Used by add_page to
    publish its progress ('phase') and the triage verdict ('triage_note') for
    the API's status poll — the payload column already exists, so neither needs
    a schema change. jsonb_set over a COALESCE'd payload so a row whose payload
    is somehow NULL still takes the write. The key is bound as a jsonb PATH
    (never string-formatted into the SQL)."""
    conn.execute(text(
        "UPDATE public.pipeline_jobs "
        "SET payload = jsonb_set(COALESCE(payload, '{}'::jsonb), "
        "                        CAST(ARRAY[:key] AS text[]), "
        "                        to_jsonb(CAST(:value AS text)), true) "
        "WHERE id = :id"),
        {"key": key, "value": value, "id": job_id})


def set_job_phase(conn, job_id: int, phase: str) -> None:
    """Publish a short progress label for the status poll to narrate."""
    set_job_payload_key(conn, job_id, "phase", phase)


def set_job_note(conn, job_id: int, note: str) -> None:
    """Publish a user-facing note (e.g. a triage verdict) on the job."""
    set_job_payload_key(conn, job_id, "triage_note", note)


def purge_terminal(conn, older_than_days: int) -> int:
    """Delete terminal ('done' OR 'error') rows finished more than `older_than_days`
    days ago (bloat control for the aggressive-autovacuum churn table). Both terminal
    states must be purged: with re-enqueue, 'error' rows also accumulate. Returns rows
    deleted."""
    return conn.execute(text(
        "DELETE FROM public.pipeline_jobs "
        "WHERE status IN ('done','error') "
        "AND finished_at < now() - make_interval(days => :days)"),
        {"days": older_than_days}).rowcount
