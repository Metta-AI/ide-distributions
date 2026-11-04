ALTER TABLE client_admissions ADD COLUMN advanced_at TEXT;
-- Activate legacy quarantine when the coordinator becomes the execution owner.
UPDATE client_admissions SET resolution_required=1,error='legacy admission requires reconciliation'
 WHERE dispatch_started=1 AND run_id IS NULL AND auth_scope IS NULL;
