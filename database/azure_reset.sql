-- !!! DESTRUCTIVE !!!  Deletes ALL MedTrace tables, the view, the trigger and ALL data in this database.
-- Use only to start again from zero. Run in the Azure portal Query editor, then restart the app
-- (it recreates the empty tables) and run:  python -m app.create_admin
DROP VIEW  IF EXISTS dbo.v_drug_safety_status;
DROP TABLE IF EXISTS dbo.audit_logs;
DROP TABLE IF EXISTS dbo.alerts;
DROP TABLE IF EXISTS dbo.verification_requests;
DROP TABLE IF EXISTS dbo.temperature_logs;   -- also drops its trigger
DROP TABLE IF EXISTS dbo.supply_events;
DROP TABLE IF EXISTS dbo.batches;
DROP TABLE IF EXISTS dbo.medicines;
DROP TABLE IF EXISTS dbo.users;
DROP TABLE IF EXISTS dbo.roles;
