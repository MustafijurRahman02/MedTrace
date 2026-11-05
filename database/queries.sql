-- MedTrace demonstration queries for Azure SQL Database (T-SQL).
-- Paste into: Azure portal > your database > Query editor. Run them one block at a time.
-- Most queries return rows only after you have entered some data in the app (or loaded the optional demo data).

-- 1. Join + aggregate: batch overview (medicine + batch + counts)
SELECT b.batch_number, m.brand_name, m.manufacturer_name, b.expiry_date,
       COUNT(DISTINCT e.event_id) AS events, COUNT(DISTINCT t.log_id) AS readings
FROM dbo.batches b
JOIN dbo.medicines m ON m.medicine_id = b.medicine_id
LEFT JOIN dbo.supply_events e ON e.batch_id = b.batch_id
LEFT JOIN dbo.temperature_logs t ON t.batch_id = b.batch_id
GROUP BY b.batch_id, b.batch_number, m.brand_name, m.manufacturer_name, b.expiry_date;

-- 2. The view: rule-derived safety status for every batch
SELECT batch_number, safety_status, excursion_count, active_alerts FROM dbo.v_drug_safety_status;

-- 3. Journey timeline for one batch, with the user who recorded each step (change the batch id)
SELECT e.event_time, e.event_type, e.location_label, u.name AS recorded_by, r.role_name
FROM dbo.supply_events e
JOIN dbo.users u ON u.user_id = e.actor_user_id
JOIN dbo.roles r ON r.role_id = u.role_id
WHERE e.batch_id = 1 ORDER BY e.event_time;

-- 4. Temperature readings outside the configured range (batch override, else medicine default)
SELECT b.batch_number, t.recorded_at, t.temperature_value
FROM dbo.temperature_logs t
JOIN dbo.batches b ON b.batch_id = t.batch_id
JOIN dbo.medicines m ON m.medicine_id = b.medicine_id
WHERE t.temperature_value < COALESCE(b.min_temp, m.default_min_temp)
   OR t.temperature_value > COALESCE(b.max_temp, m.default_max_temp);

-- 5. Open alerts by severity, with the oldest one
SELECT severity, COUNT(*) AS open_alerts, MIN(created_at) AS oldest
FROM dbo.alerts WHERE status IN ('OPEN', 'UNDER_REVIEW', 'ESCALATED') GROUP BY severity;

-- 6. Verification outcomes per day
SELECT SUBSTRING(requested_at, 1, 10) AS day, result, COUNT(*) AS scans
FROM dbo.verification_requests
GROUP BY SUBSTRING(requested_at, 1, 10), result ORDER BY day DESC;

-- 7. Indexes (evidence for the viva) and a row-count check on every table
SELECT OBJECT_NAME(object_id) AS table_name, name AS index_name, type_desc, is_unique
FROM sys.indexes WHERE object_id IN (SELECT object_id FROM sys.tables) AND name IS NOT NULL
ORDER BY table_name, index_name;

SELECT t.name AS table_name, SUM(p.rows) AS row_count
FROM sys.tables t JOIN sys.partitions p ON p.object_id = t.object_id AND p.index_id IN (0, 1)
GROUP BY t.name ORDER BY t.name;

-- 8. Transaction demo: both rows commit together or neither does
BEGIN TRANSACTION;
INSERT INTO dbo.verification_requests (batch_id, verification_code_submitted, requested_at, result)
VALUES (1, 'MT-DEMO-TEST', CONVERT(VARCHAR(19), SYSUTCDATETIME(), 126) + 'Z', 'REGISTERED');
INSERT INTO dbo.audit_logs (action, entity_type, entity_id) VALUES ('VERIFY', 'verification_request', CAST(SCOPE_IDENTITY() AS NVARCHAR(40)));
ROLLBACK TRANSACTION;   -- change to COMMIT TRANSACTION to keep both rows (needs batch 1 to exist)

-- 9. Trigger demo: an out-of-range reading raises an alert automatically (needs a cold-chain batch; change the id)
-- INSERT INTO dbo.temperature_logs (batch_id, temperature_value, recorded_at, source_type)
-- VALUES (1, 14.0, CONVERT(VARCHAR(19), SYSUTCDATETIME(), 126) + 'Z', 'MANUAL');
-- SELECT * FROM dbo.alerts WHERE batch_id = 1 ORDER BY alert_id DESC;
-- SELECT TOP 3 * FROM dbo.audit_logs ORDER BY audit_id DESC;   -- shows the AUTO_ALERT row written by the trigger
