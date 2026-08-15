-- PostgreSQL ETL template for EIS exports.
-- Raw files must be loaded into staging tables before running the INSERT blocks.

BEGIN;

CREATE SCHEMA IF NOT EXISTS eis;
CREATE SCHEMA IF NOT EXISTS eis_stg;

CREATE TABLE IF NOT EXISTS eis.load_batches (
    batch_id BIGSERIAL PRIMARY KEY,
    source_file TEXT NOT NULL,
    loaded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    source_sha256 TEXT,
    row_count INTEGER,
    status TEXT NOT NULL DEFAULT 'loaded'
        CHECK (status IN ('loaded','validated','failed'))
);

CREATE TABLE IF NOT EXISTS eis.load_errors (
    error_id BIGSERIAL PRIMARY KEY,
    batch_id BIGINT REFERENCES eis.load_batches(batch_id),
    entity_name TEXT NOT NULL,
    raw_key TEXT,
    error_code TEXT NOT NULL,
    error_message TEXT NOT NULL,
    raw_payload JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS eis.procurements (
    procurement_id TEXT PRIMARY KEY,
    ikz TEXT,
    law TEXT,
    procurement_method TEXT,
    customer_inn TEXT,
    customer_name TEXT,
    subject TEXT,
    okpd2 TEXT,
    published_at TIMESTAMPTZ,
    nmck NUMERIC(18,2),
    region TEXT,
    source_batch_id BIGINT REFERENCES eis.load_batches(batch_id),
    source_document_id TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS eis.participants (
    participant_inn TEXT PRIMARY KEY,
    participant_name TEXT,
    ogrn TEXT,
    legal_address TEXT,
    valid_from DATE,
    valid_to DATE,
    source_batch_id BIGINT REFERENCES eis.load_batches(batch_id),
    source_document_id TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (participant_inn ~ '^[0-9]{10}([0-9]{2})?$')
);

CREATE TABLE IF NOT EXISTS eis.participations (
    procurement_id TEXT NOT NULL REFERENCES eis.procurements(procurement_id),
    participant_inn TEXT NOT NULL REFERENCES eis.participants(participant_inn),
    role TEXT,
    admitted BOOLEAN,
    rank INTEGER,
    final_price NUMERIC(18,2),
    result TEXT,
    source_batch_id BIGINT REFERENCES eis.load_batches(batch_id),
    source_document_id TEXT,
    PRIMARY KEY (procurement_id, participant_inn)
);

CREATE TABLE IF NOT EXISTS eis.contracts (
    contract_id TEXT PRIMARY KEY,
    procurement_id TEXT REFERENCES eis.procurements(procurement_id),
    customer_inn TEXT,
    supplier_inn TEXT REFERENCES eis.participants(participant_inn),
    contract_date DATE,
    contract_price NUMERIC(18,2),
    status TEXT,
    execution_address TEXT,
    source_batch_id BIGINT REFERENCES eis.load_batches(batch_id),
    source_document_id TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS eis.bids (
    bid_id TEXT PRIMARY KEY,
    procurement_id TEXT NOT NULL REFERENCES eis.procurements(procurement_id),
    participant_inn TEXT NOT NULL REFERENCES eis.participants(participant_inn),
    submitted_at TIMESTAMPTZ,
    price NUMERIC(18,2),
    step_number INTEGER,
    stage TEXT,
    source_batch_id BIGINT REFERENCES eis.load_batches(batch_id),
    source_document_id TEXT
);

CREATE TABLE IF NOT EXISTS eis.documents (
    document_id TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    document_type TEXT,
    url TEXT,
    downloaded_at TIMESTAMPTZ,
    sha256 TEXT,
    evidence_level CHAR(1) CHECK (evidence_level IN ('A','B','C','D')),
    source_batch_id BIGINT REFERENCES eis.load_batches(batch_id)
);

CREATE INDEX IF NOT EXISTS ix_contracts_procurement ON eis.contracts(procurement_id);
CREATE INDEX IF NOT EXISTS ix_participations_participant ON eis.participations(participant_inn);
CREATE INDEX IF NOT EXISTS ix_bids_procurement_time ON eis.bids(procurement_id, submitted_at);

-- Staging tables intentionally keep source values as TEXT. Adjust columns to the actual EIS export.
CREATE TABLE IF NOT EXISTS eis_stg.procurements_raw (
    batch_id BIGINT NOT NULL,
    procurement_id TEXT,
    ikz TEXT,
    law TEXT,
    procurement_method TEXT,
    customer_inn TEXT,
    customer_name TEXT,
    subject TEXT,
    okpd2 TEXT,
    published_at TEXT,
    nmck TEXT,
    region TEXT,
    source_document_id TEXT,
    raw_payload JSONB
);

CREATE TABLE IF NOT EXISTS eis_stg.participants_raw (
    batch_id BIGINT NOT NULL,
    procurement_id TEXT,
    participant_inn TEXT,
    participant_name TEXT,
    ogrn TEXT,
    legal_address TEXT,
    role TEXT,
    admitted TEXT,
    rank TEXT,
    final_price TEXT,
    result TEXT,
    source_document_id TEXT,
    raw_payload JSONB
);

CREATE TABLE IF NOT EXISTS eis_stg.contracts_raw (
    batch_id BIGINT NOT NULL,
    contract_id TEXT,
    procurement_id TEXT,
    customer_inn TEXT,
    supplier_inn TEXT,
    contract_date TEXT,
    contract_price TEXT,
    status TEXT,
    execution_address TEXT,
    source_document_id TEXT,
    raw_payload JSONB
);

CREATE TABLE IF NOT EXISTS eis_stg.bids_raw (
    batch_id BIGINT NOT NULL,
    bid_id TEXT,
    procurement_id TEXT,
    participant_inn TEXT,
    submitted_at TEXT,
    price TEXT,
    step_number TEXT,
    stage TEXT,
    source_document_id TEXT,
    raw_payload JSONB
);

-- Reject malformed rows before loading. NULLIF and regexp checks prevent empty strings
-- and arbitrary text from becoming apparently valid financial or identity data.
INSERT INTO eis.load_errors(batch_id, entity_name, raw_key, error_code, error_message, raw_payload)
SELECT batch_id, 'procurements', procurement_id, 'BAD_KEY', 'procurement_id is empty', raw_payload
FROM eis_stg.procurements_raw
WHERE NULLIF(btrim(procurement_id), '') IS NULL;

INSERT INTO eis.load_errors(batch_id, entity_name, raw_key, error_code, error_message, raw_payload)
SELECT batch_id, 'participants', participant_inn, 'BAD_INN', 'participant INN is not 10 or 12 digits', raw_payload
FROM eis_stg.participants_raw
WHERE NULLIF(regexp_replace(participant_inn, '\\D', '', 'g'), '') IS NULL
   OR regexp_replace(participant_inn, '\\D', '', 'g') !~ '^[0-9]{10}([0-9]{2})?$';

-- Load procurements. DISTINCT ON keeps the latest source row for a key within a batch;
-- change ordering if the export provides a reliable source update timestamp.
INSERT INTO eis.procurements(
    procurement_id, ikz, law, procurement_method, customer_inn, customer_name,
    subject, okpd2, published_at, nmck, region, source_batch_id, source_document_id
)
SELECT DISTINCT ON (btrim(procurement_id))
    btrim(procurement_id), NULLIF(btrim(ikz), ''), NULLIF(btrim(law), ''),
    NULLIF(btrim(procurement_method), ''), NULLIF(regexp_replace(customer_inn, '\\D', '', 'g'), ''),
    NULLIF(btrim(customer_name), ''), NULLIF(btrim(subject), ''), NULLIF(btrim(okpd2), ''),
    CASE WHEN NULLIF(btrim(published_at), '') IS NULL THEN NULL
         ELSE NULLIF(btrim(published_at), '')::timestamptz END,
    CASE WHEN NULLIF(replace(replace(btrim(nmck), ' ', ''), ',', '.'), '') ~ '^[0-9]+(\\.[0-9]+)?$'
         THEN replace(replace(btrim(nmck), ' ', ''), ',', '.')::numeric(18,2) END,
    NULLIF(btrim(region), ''), batch_id, NULLIF(btrim(source_document_id), '')
FROM eis_stg.procurements_raw
WHERE NULLIF(btrim(procurement_id), '') IS NOT NULL
ORDER BY btrim(procurement_id), batch_id DESC
ON CONFLICT (procurement_id) DO UPDATE SET
    ikz = EXCLUDED.ikz,
    law = EXCLUDED.law,
    procurement_method = EXCLUDED.procurement_method,
    customer_inn = EXCLUDED.customer_inn,
    customer_name = EXCLUDED.customer_name,
    subject = EXCLUDED.subject,
    okpd2 = EXCLUDED.okpd2,
    published_at = EXCLUDED.published_at,
    nmck = EXCLUDED.nmck,
    region = EXCLUDED.region,
    source_batch_id = EXCLUDED.source_batch_id,
    source_document_id = EXCLUDED.source_document_id,
    updated_at = now();

-- Participants are loaded first because participation and contract tables reference them.
INSERT INTO eis.participants(
    participant_inn, participant_name, ogrn, legal_address,
    source_batch_id, source_document_id
)
SELECT DISTINCT ON (inn)
    inn, NULLIF(btrim(participant_name), ''), NULLIF(regexp_replace(ogrn, '\\D', '', 'g'), ''),
    NULLIF(btrim(legal_address), ''), batch_id, NULLIF(btrim(source_document_id), '')
FROM (
    SELECT batch_id, regexp_replace(participant_inn, '\\D', '', 'g') AS inn,
           participant_name, ogrn, legal_address, source_document_id
    FROM eis_stg.participants_raw
) s
WHERE inn ~ '^[0-9]{10}([0-9]{2})?$'
ORDER BY inn, batch_id DESC
ON CONFLICT (participant_inn) DO UPDATE SET
    participant_name = COALESCE(EXCLUDED.participant_name, eis.participants.participant_name),
    ogrn = COALESCE(EXCLUDED.ogrn, eis.participants.ogrn),
    legal_address = COALESCE(EXCLUDED.legal_address, eis.participants.legal_address),
    source_batch_id = EXCLUDED.source_batch_id,
    source_document_id = EXCLUDED.source_document_id,
    updated_at = now();

INSERT INTO eis.participations(
    procurement_id, participant_inn, role, admitted, rank, final_price,
    result, source_batch_id, source_document_id
)
SELECT DISTINCT ON (r.procurement_id, r.inn)
    btrim(r.procurement_id), r.inn, NULLIF(btrim(r.role), ''),
    CASE lower(btrim(r.admitted)) WHEN 'true' THEN TRUE WHEN 'да' THEN TRUE
         WHEN '1' THEN TRUE WHEN 'false' THEN FALSE WHEN 'нет' THEN FALSE
         WHEN '0' THEN FALSE END,
    CASE WHEN btrim(r.rank) ~ '^[0-9]+$' THEN btrim(r.rank)::integer END,
    CASE WHEN replace(replace(btrim(r.final_price), ' ', ''), ',', '.') ~ '^[0-9]+(\\.[0-9]+)?$'
         THEN replace(replace(btrim(r.final_price), ' ', ''), ',', '.')::numeric(18,2) END,
    NULLIF(btrim(r.result), ''), r.batch_id, NULLIF(btrim(r.source_document_id), '')
FROM (
    SELECT *, regexp_replace(participant_inn, '\\D', '', 'g') AS inn
    FROM eis_stg.participants_raw
) r
JOIN eis.procurements p ON p.procurement_id = btrim(r.procurement_id)
JOIN eis.participants x ON x.participant_inn = r.inn
WHERE r.inn ~ '^[0-9]{10}([0-9]{2})?$'
ORDER BY r.procurement_id, r.inn, r.batch_id DESC
ON CONFLICT (procurement_id, participant_inn) DO UPDATE SET
    role = EXCLUDED.role,
    admitted = EXCLUDED.admitted,
    rank = EXCLUDED.rank,
    final_price = EXCLUDED.final_price,
    result = EXCLUDED.result,
    source_batch_id = EXCLUDED.source_batch_id,
    source_document_id = EXCLUDED.source_document_id;

INSERT INTO eis.contracts(
    contract_id, procurement_id, customer_inn, supplier_inn, contract_date,
    contract_price, status, execution_address, source_batch_id, source_document_id
)
SELECT DISTINCT ON (btrim(r.contract_id))
    btrim(r.contract_id), NULLIF(btrim(r.procurement_id), ''),
    NULLIF(regexp_replace(r.customer_inn, '\\D', '', 'g'), ''),
    NULLIF(regexp_replace(r.supplier_inn, '\\D', '', 'g'), ''),
    CASE WHEN NULLIF(btrim(r.contract_date), '') IS NULL THEN NULL
         ELSE NULLIF(btrim(r.contract_date), '')::date END,
    CASE WHEN replace(replace(btrim(r.contract_price), ' ', ''), ',', '.') ~ '^[0-9]+(\\.[0-9]+)?$'
         THEN replace(replace(btrim(r.contract_price), ' ', ''), ',', '.')::numeric(18,2) END,
    NULLIF(btrim(r.status), ''), NULLIF(btrim(r.execution_address), ''),
    r.batch_id, NULLIF(btrim(r.source_document_id), '')
FROM eis_stg.contracts_raw r
LEFT JOIN eis.procurements p ON p.procurement_id = NULLIF(btrim(r.procurement_id), '')
LEFT JOIN eis.participants s ON s.participant_inn = NULLIF(regexp_replace(r.supplier_inn, '\\D', '', 'g'), '')
WHERE NULLIF(btrim(r.contract_id), '') IS NOT NULL
  AND (r.procurement_id IS NULL OR p.procurement_id IS NOT NULL)
  AND (r.supplier_inn IS NULL OR s.participant_inn IS NOT NULL)
ORDER BY btrim(r.contract_id), r.batch_id DESC
ON CONFLICT (contract_id) DO UPDATE SET
    procurement_id = EXCLUDED.procurement_id,
    customer_inn = EXCLUDED.customer_inn,
    supplier_inn = EXCLUDED.supplier_inn,
    contract_date = EXCLUDED.contract_date,
    contract_price = EXCLUDED.contract_price,
    status = EXCLUDED.status,
    execution_address = EXCLUDED.execution_address,
    source_batch_id = EXCLUDED.source_batch_id,
    source_document_id = EXCLUDED.source_document_id,
    updated_at = now();

INSERT INTO eis.bids(
    bid_id, procurement_id, participant_inn, submitted_at, price, step_number,
    stage, source_batch_id, source_document_id
)
SELECT DISTINCT ON (btrim(r.bid_id))
    btrim(r.bid_id), btrim(r.procurement_id),
    regexp_replace(r.participant_inn, '\\D', '', 'g'),
    CASE WHEN NULLIF(btrim(r.submitted_at), '') IS NULL THEN NULL
         ELSE NULLIF(btrim(r.submitted_at), '')::timestamptz END,
    CASE WHEN replace(replace(btrim(r.price), ' ', ''), ',', '.') ~ '^[0-9]+(\\.[0-9]+)?$'
         THEN replace(replace(btrim(r.price), ' ', ''), ',', '.')::numeric(18,2) END,
    CASE WHEN btrim(r.step_number) ~ '^[0-9]+$' THEN btrim(r.step_number)::integer END,
    NULLIF(btrim(r.stage), ''), r.batch_id, NULLIF(btrim(r.source_document_id), '')
FROM eis_stg.bids_raw r
JOIN eis.procurements p ON p.procurement_id = btrim(r.procurement_id)
JOIN eis.participants s ON s.participant_inn = regexp_replace(r.participant_inn, '\\D', '', 'g')
WHERE NULLIF(btrim(r.bid_id), '') IS NOT NULL
ORDER BY btrim(r.bid_id), r.batch_id DESC
ON CONFLICT (bid_id) DO UPDATE SET
    procurement_id = EXCLUDED.procurement_id,
    participant_inn = EXCLUDED.participant_inn,
    submitted_at = EXCLUDED.submitted_at,
    price = EXCLUDED.price,
    step_number = EXCLUDED.step_number,
    stage = EXCLUDED.stage,
    source_batch_id = EXCLUDED.source_batch_id,
    source_document_id = EXCLUDED.source_document_id;

COMMIT;

-- Post-load quality checks (run separately or include in a validation job):
-- SELECT procurement_id, COUNT(*) FROM eis.procurements GROUP BY procurement_id HAVING COUNT(*) > 1;
-- SELECT * FROM eis.contracts WHERE procurement_id IS NOT NULL AND procurement_id NOT IN (SELECT procurement_id FROM eis.procurements);
-- SELECT * FROM eis.bids WHERE submitted_at IS NULL OR price IS NULL;
-- SELECT * FROM eis.load_errors ORDER BY created_at DESC;
