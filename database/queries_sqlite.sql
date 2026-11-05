-- MedTrace demonstration queries (run against the seeded database). SQLite dialect.

-- 1. Join: batch detail page (medicine + batch + counts)
SELECT b.batch_number, m.brand_name, m.manufacturer_name, b.expiry_date,
       COUNT(DISTINCT e.event_id) AS events, COUNT(DISTINCT t.log_id) AS readings
FROM batches b
JOIN medicines m ON m.medicine_id = b.medicine_id
LEFT JOIN supply_events e ON e.batch_id = b.batch_id
LEFT JOIN temperature_logs t ON t.batch_id = b.batch_id
GROUP BY b.batch_id;

-- 2. View: rule-derived safety status for every batch
SELECT batch_number, safety_status, excursion_count, active_alerts FROM v_drug_safety_status;

-- 3. Journey timeline for one batch, with the user who recorded each step
SELECT e.event_time, e.event_type, e.location_label, u.name AS recorded_by, r.role_name
FROM supply_events e
JOIN users u ON u.user_id = e.actor_user_id
JOIN roles r ON r.role_id = u.role_id
WHERE e.batch_id = 1 ORDER BY e.event_time;

-- 4. Temperature readings outside the configured range (batch override, else medicine default)
SELECT b.batch_number, t.recorded_at, t.temperature_value
FROM temperature_logs t
JOIN batches b ON b.batch_id = t.batch_id
JOIN medicines m ON m.medicine_id = b.medicine_id
WHERE t.temperature_value < COALESCE(b.min_temp, m.default_min_temp)
   OR t.temperature_value > COALESCE(b.max_temp, m.default_max_temp);

-- 5. Open alerts by severity and age
SELECT severity, COUNT(*) AS open_alerts, MIN(created_at) AS oldest
FROM alerts WHERE status IN ('OPEN', 'UNDER_REVIEW', 'ESCALATED') GROUP BY severity;

-- 6. Verification outcomes per day
SELECT substr(requested_at, 1, 10) AS day, result, COUNT(*) AS scans
FROM verification_requests GROUP BY day, result ORDER BY day DESC;

-- 7. Index usage evidence (the plan should say SEARCH ... USING INDEX / COVERING INDEX)
EXPLAIN QUERY PLAN SELECT * FROM batches WHERE verification_code = 'MT-DEMO-0001';
EXPLAIN QUERY PLAN SELECT * FROM supply_events WHERE batch_id = 1 ORDER BY event_time;

-- 8. Transaction demo: both rows commit together or neither does
BEGIN;
INSERT INTO verification_requests (batch_id, verification_code_submitted, requested_at, result)
VALUES (1, 'MT-DEMO-0001', strftime('%Y-%m-%dT%H:%M:%SZ','now'), 'REGISTERED');
INSERT INTO audit_logs (action, entity_type, entity_id) VALUES ('VERIFY', 'verification_request', last_insert_rowid());
ROLLBACK;   -- change to COMMIT to keep both rows

-- 9. Trigger demo: inserting an out-of-range reading raises an alert automatically
-- INSERT INTO temperature_logs (batch_id, temperature_value, recorded_at, source_type)
-- VALUES (2, 14.0, strftime('%Y-%m-%dT%H:%M:%SZ','now'), 'MANUAL');
-- SELECT * FROM alerts WHERE batch_id = 2;
