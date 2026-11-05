-- MedTrace schema (SQLite dialect, 3NF). Synthetic/demo data only.
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS roles (
    role_id     INTEGER PRIMARY KEY,
    role_name   TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    user_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    email         TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role_id       INTEGER NOT NULL REFERENCES roles(role_id),
    is_active     INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS medicines (
    medicine_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    generic_name        TEXT NOT NULL,
    brand_name          TEXT NOT NULL,
    manufacturer_name   TEXT NOT NULL,
    requires_cold_chain INTEGER NOT NULL DEFAULT 0 CHECK (requires_cold_chain IN (0, 1)),
    default_min_temp    REAL,
    default_max_temp    REAL,
    UNIQUE (brand_name, manufacturer_name),
    CHECK ((default_min_temp IS NULL AND default_max_temp IS NULL)
           OR (default_min_temp IS NOT NULL AND default_max_temp IS NOT NULL
               AND default_min_temp < default_max_temp)),
    CHECK (requires_cold_chain = 0 OR default_min_temp IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS batches (
    batch_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    medicine_id       INTEGER NOT NULL REFERENCES medicines(medicine_id),
    batch_number      TEXT NOT NULL,
    manufacture_date  TEXT NOT NULL,
    expiry_date       TEXT NOT NULL,
    verification_code TEXT NOT NULL UNIQUE,
    min_temp          REAL,            -- optional per-batch override of the medicine default
    max_temp          REAL,
    status            TEXT NOT NULL DEFAULT 'ACTIVE'
                      CHECK (status IN ('ACTIVE', 'QUARANTINED', 'RECALLED')),
    created_by        INTEGER REFERENCES users(user_id),
    created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE (medicine_id, batch_number),
    CHECK (expiry_date > manufacture_date),
    CHECK ((min_temp IS NULL AND max_temp IS NULL)
           OR (min_temp IS NOT NULL AND max_temp IS NOT NULL AND min_temp < max_temp))
);

CREATE TABLE IF NOT EXISTS supply_events (
    event_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id       INTEGER NOT NULL REFERENCES batches(batch_id),
    actor_user_id  INTEGER NOT NULL REFERENCES users(user_id),
    event_type     TEXT NOT NULL CHECK (event_type IN
                   ('REGISTERED', 'DISPATCHED', 'RECEIVED_AT_DISTRIBUTION',
                    'RECEIVED_AT_PHARMACY', 'RETURNED')),
    location_label TEXT NOT NULL,
    event_time     TEXT NOT NULL,
    notes          TEXT
);

CREATE TABLE IF NOT EXISTS temperature_logs (
    log_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id          INTEGER NOT NULL REFERENCES batches(batch_id),
    source_event_id   INTEGER REFERENCES supply_events(event_id),
    temperature_value REAL NOT NULL CHECK (temperature_value BETWEEN -90 AND 80),
    recorded_at       TEXT NOT NULL,
    source_type       TEXT NOT NULL CHECK (source_type IN ('MANUAL', 'SIMULATED', 'CSV_IMPORT')),
    file_reference    TEXT
);

CREATE TABLE IF NOT EXISTS verification_requests (
    request_id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id                    INTEGER REFERENCES batches(batch_id),
    verification_code_submitted TEXT NOT NULL,
    requester_user_id           INTEGER REFERENCES users(user_id),
    location_label              TEXT,
    requested_at                TEXT NOT NULL,
    result                      TEXT NOT NULL CHECK (result IN ('REGISTERED', 'UNKNOWN', 'CONFLICT')),
    risk_reason                 TEXT
);

CREATE TABLE IF NOT EXISTS alerts (
    alert_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id          INTEGER REFERENCES batches(batch_id),
    verification_code TEXT,            -- used when the submitted code matched no batch
    alert_type        TEXT NOT NULL CHECK (alert_type IN
                      ('UNKNOWN_CODE', 'CONFLICTING_RECORD', 'TEMP_EXCURSION',
                       'REPEATED_SCAN', 'LOCATION_ANOMALY', 'PHARMACY_CONCERN')),
    severity          TEXT NOT NULL CHECK (severity IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')),
    reason            TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'OPEN'
                      CHECK (status IN ('OPEN', 'UNDER_REVIEW', 'RESOLVED', 'ESCALATED')),
    created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    reviewed_by       INTEGER REFERENCES users(user_id),
    reviewed_at       TEXT,
    review_note       TEXT,
    CHECK (batch_id IS NOT NULL OR verification_code IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS audit_logs (
    audit_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_user_id INTEGER REFERENCES users(user_id),
    action        TEXT NOT NULL,
    entity_type   TEXT NOT NULL,
    entity_id     TEXT,
    timestamp     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    safe_metadata TEXT
);

-- Indexes for the common lookups
CREATE INDEX IF NOT EXISTS ix_batches_number    ON batches(batch_number);
CREATE INDEX IF NOT EXISTS ix_events_batch_time ON supply_events(batch_id, event_time);
CREATE INDEX IF NOT EXISTS ix_temp_batch_time   ON temperature_logs(batch_id, recorded_at);
CREATE INDEX IF NOT EXISTS ix_verif_code_time   ON verification_requests(verification_code_submitted, requested_at);
CREATE INDEX IF NOT EXISTS ix_verif_time        ON verification_requests(requested_at);
CREATE INDEX IF NOT EXISTS ix_alerts_status     ON alerts(status, severity);
CREATE INDEX IF NOT EXISTS ix_audit_time        ON audit_logs(timestamp);

-- View: rule-derived safety status. Never a permanent "green":
-- missing evidence produces NEEDS_REVIEW.
DROP VIEW IF EXISTS v_drug_safety_status;
CREATE VIEW v_drug_safety_status AS
SELECT s.*,
  CASE
    WHEN s.batch_status IN ('RECALLED', 'QUARANTINED')       THEN 'FLAGGED'
    WHEN s.expiry_date < date('now')                         THEN 'FLAGGED'
    WHEN s.active_serious_alerts > 0                         THEN 'FLAGGED'
    WHEN s.active_alerts > 0                                 THEN 'NEEDS_REVIEW'
    WHEN s.event_count < 2                                   THEN 'NEEDS_REVIEW'
    WHEN s.requires_cold_chain = 1 AND s.reading_count = 0   THEN 'NEEDS_REVIEW'
    ELSE 'RECORDS_CONSISTENT'
  END AS safety_status
FROM (
  SELECT b.batch_id, b.batch_number, b.verification_code, b.status AS batch_status,
         b.manufacture_date, b.expiry_date, b.created_at,
         m.medicine_id, m.brand_name, m.generic_name, m.manufacturer_name, m.requires_cold_chain,
         COALESCE(b.min_temp, m.default_min_temp) AS min_temp,
         COALESCE(b.max_temp, m.default_max_temp) AS max_temp,
         (SELECT COUNT(*) FROM supply_events e WHERE e.batch_id = b.batch_id) AS event_count,
         (SELECT COUNT(*) FROM temperature_logs t WHERE t.batch_id = b.batch_id) AS reading_count,
         (SELECT COUNT(*) FROM temperature_logs t
           WHERE t.batch_id = b.batch_id
             AND (t.temperature_value < COALESCE(b.min_temp, m.default_min_temp)
               OR t.temperature_value > COALESCE(b.max_temp, m.default_max_temp))) AS excursion_count,
         (SELECT COUNT(*) FROM alerts a WHERE a.batch_id = b.batch_id
             AND a.status IN ('OPEN', 'UNDER_REVIEW', 'ESCALATED')) AS active_alerts,
         (SELECT COUNT(*) FROM alerts a WHERE a.batch_id = b.batch_id
             AND a.status IN ('OPEN', 'UNDER_REVIEW', 'ESCALATED')
             AND a.severity IN ('HIGH', 'CRITICAL')) AS active_serious_alerts
  FROM batches b JOIN medicines m ON m.medicine_id = b.medicine_id
) s;

-- Trigger (SQLite equivalent of sp_FlagSuspiciousBatch): a reading outside the configured
-- range raises one active TEMP_EXCURSION alert per batch and writes an audit row.
DROP TRIGGER IF EXISTS trg_flag_temperature_excursion;
CREATE TRIGGER trg_flag_temperature_excursion
AFTER INSERT ON temperature_logs
WHEN EXISTS (SELECT 1 FROM batches b JOIN medicines m ON m.medicine_id = b.medicine_id
             WHERE b.batch_id = NEW.batch_id
               AND (NEW.temperature_value < COALESCE(b.min_temp, m.default_min_temp)
                 OR NEW.temperature_value > COALESCE(b.max_temp, m.default_max_temp)))
 AND NOT EXISTS (SELECT 1 FROM alerts a WHERE a.batch_id = NEW.batch_id
                 AND a.alert_type = 'TEMP_EXCURSION'
                 AND a.status IN ('OPEN', 'UNDER_REVIEW', 'ESCALATED'))
BEGIN
    INSERT INTO alerts (batch_id, alert_type, severity, reason)
    SELECT NEW.batch_id, 'TEMP_EXCURSION',
           CASE WHEN MAX(COALESCE(b.min_temp, m.default_min_temp) - NEW.temperature_value,
                         NEW.temperature_value - COALESCE(b.max_temp, m.default_max_temp)) > 5
                THEN 'CRITICAL' ELSE 'HIGH' END,
           printf('Recorded %.1f C at %s is outside the allowed range %.1f to %.1f C.',
                  NEW.temperature_value, NEW.recorded_at,
                  COALESCE(b.min_temp, m.default_min_temp),
                  COALESCE(b.max_temp, m.default_max_temp))
    FROM batches b JOIN medicines m ON m.medicine_id = b.medicine_id
    WHERE b.batch_id = NEW.batch_id;

    INSERT INTO audit_logs (actor_user_id, action, entity_type, entity_id, safe_metadata)
    VALUES (NULL, 'AUTO_ALERT', 'alert', last_insert_rowid(),
            json_object('rule', 'TEMP_EXCURSION', 'batch_id', NEW.batch_id));
END;
