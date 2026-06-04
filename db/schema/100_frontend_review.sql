-- Plan 7 — human review decisions (schema `frontend`). Keyed on advertiser_id
-- (master-plan §3.7); app_id may be NULL (web funnels). PK(advertiser_id, decided_at)
-- keeps a full decision history; the review queue excludes any advertiser with a row.
CREATE TABLE IF NOT EXISTS frontend.review_decisions (
    advertiser_id INTEGER NOT NULL REFERENCES public.advertisers(id),
    verdict       TEXT NOT NULL CHECK (verdict IN ('accept', 'reject', 'hold')),
    reviewer      TEXT NOT NULL,
    note          TEXT,
    decided_at    TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (advertiser_id, decided_at)
);
