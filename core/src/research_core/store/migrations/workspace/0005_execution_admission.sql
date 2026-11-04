-- One workspace inventory for every native start, including wakes without a Coworld.
ALTER TABLE client_admissions ADD COLUMN profile TEXT NOT NULL DEFAULT 'orchestrator';
ALTER TABLE client_admissions ADD COLUMN instructions TEXT;
ALTER TABLE client_admissions ADD COLUMN delivery_mode TEXT NOT NULL DEFAULT 'start';
ALTER TABLE client_admissions ADD COLUMN target_run_id TEXT;
ALTER TABLE client_admissions ADD COLUMN origin TEXT NOT NULL DEFAULT 'client';
ALTER TABLE client_admissions ADD COLUMN native_key TEXT;
ALTER TABLE client_admissions ADD COLUMN auth_scope TEXT;
ALTER TABLE client_admissions ADD COLUMN runtime_id TEXT;
ALTER TABLE client_admissions ADD COLUMN execution_uncertain INTEGER NOT NULL DEFAULT 0;
ALTER TABLE client_admissions ADD COLUMN admitted_at TEXT;
ALTER TABLE client_admissions ADD COLUMN retry_until TEXT;
ALTER TABLE client_admissions ADD COLUMN delivery_ids TEXT NOT NULL DEFAULT '[]';
ALTER TABLE client_admissions ADD COLUMN resolution_required INTEGER NOT NULL DEFAULT 0;
CREATE UNIQUE INDEX execution_native_key ON client_admissions(profile,native_key) WHERE native_key IS NOT NULL;
CREATE TABLE task_assignments (
    coworld_id TEXT NOT NULL,
    task_id INTEGER NOT NULL,
    op_id TEXT NOT NULL UNIQUE REFERENCES client_admissions(op_id),
    session_id TEXT NOT NULL,
    profile TEXT NOT NULL,
    task_updated TEXT NOT NULL,
    rationale TEXT NOT NULL,
    context TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'held' CHECK(state IN ('held','release_requested','needs_resolution')),
    PRIMARY KEY(coworld_id,task_id)
);
CREATE TRIGGER task_assignments_log_insert AFTER INSERT ON task_assignments BEGIN
    INSERT INTO change_log(at,tbl,row_id,op) VALUES (strftime('%Y-%m-%dT%H:%M:%fZ','now'),'task_assignments',NEW.rowid,'insert');
END;
-- A legacy start with no receipt cannot be replayed without its original auth scope/horizon.
UPDATE client_admissions SET resolution_required=1,error='legacy admission requires reconciliation'
 WHERE dispatch_started=1 AND run_id IS NULL;

CREATE TRIGGER task_assignments_log_update AFTER UPDATE ON task_assignments BEGIN
    INSERT INTO change_log(at,tbl,row_id,op) VALUES (strftime('%Y-%m-%dT%H:%M:%fZ','now'),'task_assignments',NEW.rowid,'update');
END;
CREATE TRIGGER task_assignments_log_delete AFTER DELETE ON task_assignments BEGIN
    INSERT INTO change_log(at,tbl,row_id,op) VALUES (strftime('%Y-%m-%dT%H:%M:%fZ','now'),'task_assignments',OLD.rowid,'delete');
END;
