-- MedTrace schema for Azure SQL Database (T-SQL, 3NF).  Safe to run many times (idempotent).
-- Creates EMPTY tables only. The app runs this file automatically on start-up; you can also paste it
-- into the Azure portal Query editor (it works there too - the editor understands GO).
-- Timestamps are stored as ISO-8601 UTC text (e.g. 2026-10-26T09:30:00Z), dates as YYYY-MM-DD.

IF OBJECT_ID(N'dbo.roles', N'U') IS NULL
CREATE TABLE dbo.roles (
    role_id     INT           NOT NULL CONSTRAINT pk_roles PRIMARY KEY,
    role_name   NVARCHAR(30)  NOT NULL CONSTRAINT uq_roles_name UNIQUE,
    description NVARCHAR(200) NOT NULL
);
GO

IF OBJECT_ID(N'dbo.users', N'U') IS NULL
CREATE TABLE dbo.users (
    user_id       INT IDENTITY(1,1) NOT NULL CONSTRAINT pk_users PRIMARY KEY,
    name          NVARCHAR(100) NOT NULL,
    email         NVARCHAR(254) NOT NULL CONSTRAINT uq_users_email UNIQUE,
    password_hash NVARCHAR(255) NOT NULL,
    role_id       INT           NOT NULL CONSTRAINT fk_users_role REFERENCES dbo.roles(role_id),
    is_active     INT           NOT NULL CONSTRAINT df_users_active DEFAULT 1 CONSTRAINT ck_users_active CHECK (is_active IN (0, 1)),
    created_at    NVARCHAR(20)  NOT NULL CONSTRAINT df_users_created DEFAULT (CONVERT(VARCHAR(19), SYSUTCDATETIME(), 126) + 'Z')
);
GO

IF OBJECT_ID(N'dbo.medicines', N'U') IS NULL
CREATE TABLE dbo.medicines (
    medicine_id         INT IDENTITY(1,1) NOT NULL CONSTRAINT pk_medicines PRIMARY KEY,
    generic_name        NVARCHAR(150) NOT NULL,
    brand_name          NVARCHAR(150) NOT NULL,
    manufacturer_name   NVARCHAR(150) NOT NULL,
    requires_cold_chain INT   NOT NULL CONSTRAINT df_med_cold DEFAULT 0 CONSTRAINT ck_med_cold CHECK (requires_cold_chain IN (0, 1)),
    default_min_temp    FLOAT NULL,
    default_max_temp    FLOAT NULL,
    CONSTRAINT uq_med_brand_mfr UNIQUE (brand_name, manufacturer_name),
    CONSTRAINT ck_med_range CHECK ((default_min_temp IS NULL AND default_max_temp IS NULL)
        OR (default_min_temp IS NOT NULL AND default_max_temp IS NOT NULL AND default_min_temp < default_max_temp)),
    CONSTRAINT ck_med_cold_range CHECK (requires_cold_chain = 0 OR default_min_temp IS NOT NULL)
);
GO

IF OBJECT_ID(N'dbo.batches', N'U') IS NULL
CREATE TABLE dbo.batches (
    batch_id          INT IDENTITY(1,1) NOT NULL CONSTRAINT pk_batches PRIMARY KEY,
    medicine_id       INT           NOT NULL CONSTRAINT fk_batches_med REFERENCES dbo.medicines(medicine_id),
    batch_number      NVARCHAR(60)  NOT NULL,
    manufacture_date  NVARCHAR(10)  NOT NULL,
    expiry_date       NVARCHAR(10)  NOT NULL,
    verification_code NVARCHAR(40)  NOT NULL CONSTRAINT uq_batches_code UNIQUE,
    min_temp          FLOAT NULL,
    max_temp          FLOAT NULL,
    status            NVARCHAR(20)  NOT NULL CONSTRAINT df_batches_status DEFAULT 'ACTIVE'
                      CONSTRAINT ck_batches_status CHECK (status IN ('ACTIVE', 'QUARANTINED', 'RECALLED')),
    created_by        INT NULL CONSTRAINT fk_batches_user REFERENCES dbo.users(user_id),
    created_at        NVARCHAR(20)  NOT NULL CONSTRAINT df_batches_created DEFAULT (CONVERT(VARCHAR(19), SYSUTCDATETIME(), 126) + 'Z'),
    CONSTRAINT uq_batches_med_number UNIQUE (medicine_id, batch_number),
    CONSTRAINT ck_batches_dates CHECK (expiry_date > manufacture_date),
    CONSTRAINT ck_batches_range CHECK ((min_temp IS NULL AND max_temp IS NULL)
        OR (min_temp IS NOT NULL AND max_temp IS NOT NULL AND min_temp < max_temp))
);
GO

IF OBJECT_ID(N'dbo.supply_events', N'U') IS NULL
CREATE TABLE dbo.supply_events (
    event_id       INT IDENTITY(1,1) NOT NULL CONSTRAINT pk_events PRIMARY KEY,
    batch_id       INT NOT NULL CONSTRAINT fk_events_batch REFERENCES dbo.batches(batch_id),
    actor_user_id  INT NOT NULL CONSTRAINT fk_events_user  REFERENCES dbo.users(user_id),
    event_type     NVARCHAR(30)  NOT NULL CONSTRAINT ck_events_type CHECK (event_type IN
                   ('REGISTERED', 'DISPATCHED', 'RECEIVED_AT_DISTRIBUTION', 'RECEIVED_AT_PHARMACY', 'RETURNED')),
    location_label NVARCHAR(200) NOT NULL,
    event_time     NVARCHAR(20)  NOT NULL,
    notes          NVARCHAR(600) NULL
);
GO

IF OBJECT_ID(N'dbo.temperature_logs', N'U') IS NULL
CREATE TABLE dbo.temperature_logs (
    log_id            INT IDENTITY(1,1) NOT NULL CONSTRAINT pk_templogs PRIMARY KEY,
    batch_id          INT NOT NULL CONSTRAINT fk_temp_batch REFERENCES dbo.batches(batch_id),
    source_event_id   INT NULL CONSTRAINT fk_temp_event REFERENCES dbo.supply_events(event_id),
    temperature_value FLOAT NOT NULL CONSTRAINT ck_temp_value CHECK (temperature_value BETWEEN -90 AND 80),
    recorded_at       NVARCHAR(20) NOT NULL,
    source_type       NVARCHAR(20) NOT NULL CONSTRAINT ck_temp_source CHECK (source_type IN ('MANUAL', 'SIMULATED', 'CSV_IMPORT')),
    file_reference    NVARCHAR(300) NULL
);
GO

IF OBJECT_ID(N'dbo.verification_requests', N'U') IS NULL
CREATE TABLE dbo.verification_requests (
    request_id                  INT IDENTITY(1,1) NOT NULL CONSTRAINT pk_verif PRIMARY KEY,
    batch_id                    INT NULL CONSTRAINT fk_verif_batch REFERENCES dbo.batches(batch_id),
    verification_code_submitted NVARCHAR(60) NOT NULL,
    requester_user_id           INT NULL CONSTRAINT fk_verif_user REFERENCES dbo.users(user_id),
    location_label              NVARCHAR(200) NULL,
    requested_at                NVARCHAR(20) NOT NULL,
    result                      NVARCHAR(20) NOT NULL CONSTRAINT ck_verif_result CHECK (result IN ('REGISTERED', 'UNKNOWN', 'CONFLICT')),
    risk_reason                 NVARCHAR(600) NULL
);
GO

IF OBJECT_ID(N'dbo.alerts', N'U') IS NULL
CREATE TABLE dbo.alerts (
    alert_id          INT IDENTITY(1,1) NOT NULL CONSTRAINT pk_alerts PRIMARY KEY,
    batch_id          INT NULL CONSTRAINT fk_alerts_batch REFERENCES dbo.batches(batch_id),
    verification_code NVARCHAR(60) NULL,
    alert_type        NVARCHAR(30) NOT NULL CONSTRAINT ck_alerts_type CHECK (alert_type IN
                      ('UNKNOWN_CODE', 'CONFLICTING_RECORD', 'TEMP_EXCURSION', 'REPEATED_SCAN', 'LOCATION_ANOMALY', 'PHARMACY_CONCERN')),
    severity          NVARCHAR(10) NOT NULL CONSTRAINT ck_alerts_sev CHECK (severity IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')),
    reason            NVARCHAR(600) NOT NULL,
    status            NVARCHAR(20) NOT NULL CONSTRAINT df_alerts_status DEFAULT 'OPEN'
                      CONSTRAINT ck_alerts_status CHECK (status IN ('OPEN', 'UNDER_REVIEW', 'RESOLVED', 'ESCALATED')),
    created_at        NVARCHAR(20) NOT NULL CONSTRAINT df_alerts_created DEFAULT (CONVERT(VARCHAR(19), SYSUTCDATETIME(), 126) + 'Z'),
    reviewed_by       INT NULL CONSTRAINT fk_alerts_reviewer REFERENCES dbo.users(user_id),
    reviewed_at       NVARCHAR(20) NULL,
    review_note       NVARCHAR(600) NULL,
    CONSTRAINT ck_alerts_target CHECK (batch_id IS NOT NULL OR verification_code IS NOT NULL)
);
GO

IF OBJECT_ID(N'dbo.audit_logs', N'U') IS NULL
CREATE TABLE dbo.audit_logs (
    audit_id      INT IDENTITY(1,1) NOT NULL CONSTRAINT pk_audit PRIMARY KEY,
    actor_user_id INT NULL CONSTRAINT fk_audit_user REFERENCES dbo.users(user_id),
    action        NVARCHAR(60) NOT NULL,
    entity_type   NVARCHAR(40) NOT NULL,
    entity_id     NVARCHAR(40) NULL,
    [timestamp]   NVARCHAR(20) NOT NULL CONSTRAINT df_audit_ts DEFAULT (CONVERT(VARCHAR(19), SYSUTCDATETIME(), 126) + 'Z'),
    safe_metadata NVARCHAR(MAX) NULL
);
GO

-- Indexes for the common lookups
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'ix_batches_number'    AND object_id = OBJECT_ID('dbo.batches'))              CREATE INDEX ix_batches_number    ON dbo.batches(batch_number);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'ix_events_batch_time' AND object_id = OBJECT_ID('dbo.supply_events'))        CREATE INDEX ix_events_batch_time ON dbo.supply_events(batch_id, event_time);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'ix_temp_batch_time'   AND object_id = OBJECT_ID('dbo.temperature_logs'))     CREATE INDEX ix_temp_batch_time   ON dbo.temperature_logs(batch_id, recorded_at);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'ix_verif_code_time'   AND object_id = OBJECT_ID('dbo.verification_requests')) CREATE INDEX ix_verif_code_time   ON dbo.verification_requests(verification_code_submitted, requested_at);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'ix_verif_time'        AND object_id = OBJECT_ID('dbo.verification_requests')) CREATE INDEX ix_verif_time        ON dbo.verification_requests(requested_at);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'ix_alerts_status'     AND object_id = OBJECT_ID('dbo.alerts'))               CREATE INDEX ix_alerts_status     ON dbo.alerts(status, severity);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'ix_alerts_batch'      AND object_id = OBJECT_ID('dbo.alerts'))               CREATE INDEX ix_alerts_batch      ON dbo.alerts(batch_id, status);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'ix_audit_time'        AND object_id = OBJECT_ID('dbo.audit_logs'))           CREATE INDEX ix_audit_time        ON dbo.audit_logs([timestamp]);
GO

-- View: rule-derived safety status. Never a permanent "green": missing evidence gives NEEDS_REVIEW.
CREATE OR ALTER VIEW dbo.v_drug_safety_status AS
SELECT s.*,
  CASE
    WHEN s.batch_status IN ('RECALLED', 'QUARANTINED')       THEN 'FLAGGED'
    WHEN s.expiry_date < CONVERT(VARCHAR(10), SYSUTCDATETIME(), 23) THEN 'FLAGGED'
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
         (SELECT COUNT(*) FROM dbo.supply_events e WHERE e.batch_id = b.batch_id) AS event_count,
         (SELECT COUNT(*) FROM dbo.temperature_logs t WHERE t.batch_id = b.batch_id) AS reading_count,
         (SELECT COUNT(*) FROM dbo.temperature_logs t
           WHERE t.batch_id = b.batch_id
             AND (t.temperature_value < COALESCE(b.min_temp, m.default_min_temp)
               OR t.temperature_value > COALESCE(b.max_temp, m.default_max_temp))) AS excursion_count,
         (SELECT COUNT(*) FROM dbo.alerts a WHERE a.batch_id = b.batch_id
             AND a.status IN ('OPEN', 'UNDER_REVIEW', 'ESCALATED')) AS active_alerts,
         (SELECT COUNT(*) FROM dbo.alerts a WHERE a.batch_id = b.batch_id
             AND a.status IN ('OPEN', 'UNDER_REVIEW', 'ESCALATED')
             AND a.severity IN ('HIGH', 'CRITICAL')) AS active_serious_alerts
  FROM dbo.batches b JOIN dbo.medicines m ON m.medicine_id = b.medicine_id
) s;
GO

-- Trigger (the sp_FlagSuspiciousBatch rule): a reading outside the configured range raises ONE active
-- TEMP_EXCURSION alert per batch and writes an audit row. Set-based, so multi-row inserts are handled.
CREATE OR ALTER TRIGGER dbo.trg_flag_temperature_excursion ON dbo.temperature_logs
AFTER INSERT
AS
BEGIN
    SET NOCOUNT ON;
    DECLARE @new TABLE (alert_id INT, batch_id INT);

    WITH bad AS (
        SELECT i.batch_id, i.temperature_value AS t, i.recorded_at,
               COALESCE(b.min_temp, m.default_min_temp) AS lo,
               COALESCE(b.max_temp, m.default_max_temp) AS hi,
               ROW_NUMBER() OVER (PARTITION BY i.batch_id ORDER BY i.log_id) AS rn
        FROM inserted i
        JOIN dbo.batches b   ON b.batch_id = i.batch_id
        JOIN dbo.medicines m ON m.medicine_id = b.medicine_id
        WHERE i.temperature_value < COALESCE(b.min_temp, m.default_min_temp)
           OR i.temperature_value > COALESCE(b.max_temp, m.default_max_temp)
    )
    INSERT INTO dbo.alerts (batch_id, alert_type, severity, reason)
    OUTPUT inserted.alert_id, inserted.batch_id INTO @new
    SELECT bad.batch_id, 'TEMP_EXCURSION',
           CASE WHEN (CASE WHEN bad.lo - bad.t > bad.t - bad.hi THEN bad.lo - bad.t ELSE bad.t - bad.hi END) > 5
                THEN 'CRITICAL' ELSE 'HIGH' END,
           CONCAT('Recorded ', CONVERT(VARCHAR(12), CAST(ROUND(bad.t, 1) AS DECIMAL(6,1))),
                  ' C at ', bad.recorded_at, ' is outside the allowed range ',
                  CONVERT(VARCHAR(12), CAST(ROUND(bad.lo, 1) AS DECIMAL(6,1))), ' to ',
                  CONVERT(VARCHAR(12), CAST(ROUND(bad.hi, 1) AS DECIMAL(6,1))), ' C.')
    FROM bad
    WHERE bad.rn = 1
      AND NOT EXISTS (SELECT 1 FROM dbo.alerts a WHERE a.batch_id = bad.batch_id
                      AND a.alert_type = 'TEMP_EXCURSION' AND a.status IN ('OPEN', 'UNDER_REVIEW', 'ESCALATED'));

    INSERT INTO dbo.audit_logs (actor_user_id, action, entity_type, entity_id, safe_metadata)
    SELECT NULL, 'AUTO_ALERT', 'alert', CAST(n.alert_id AS NVARCHAR(40)),
           CONCAT('{"rule": "TEMP_EXCURSION", "batch_id": ', n.batch_id, '}')
    FROM @new n;
END;
GO
