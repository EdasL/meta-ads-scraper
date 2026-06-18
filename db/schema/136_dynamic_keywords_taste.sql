-- Taste-driven mined keywords (source='liked-mining') land as status='pending'
-- (sweep ignores it) until a human promotes them to 'trial'. rationale + the
-- inspired-by advertiser ids are shown in the review gate.
ALTER TABLE discovery.dynamic_keywords ADD COLUMN IF NOT EXISTS rationale TEXT;
ALTER TABLE discovery.dynamic_keywords ADD COLUMN IF NOT EXISTS inspired_by BIGINT[];
ALTER TABLE discovery.dynamic_keywords DROP CONSTRAINT IF EXISTS dynamic_keywords_status_check;
ALTER TABLE discovery.dynamic_keywords ADD CONSTRAINT dynamic_keywords_status_check
    CHECK (status = ANY (ARRAY['pending','trial','active','retired']));
