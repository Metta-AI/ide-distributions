-- Assignment identity survives retries; execution identity does not.
ALTER TABLE task_assignments ADD COLUMN current_attempt_id TEXT REFERENCES client_admissions(op_id);
UPDATE task_assignments SET current_attempt_id=COALESCE(
    (SELECT a.op_id FROM client_admissions a WHERE a.session_id=task_assignments.session_id
     AND a.delivery_mode='start' ORDER BY a.dispatch_started DESC,a.rowid DESC LIMIT 1), op_id
);
CREATE TRIGGER task_assignment_initial_attempt AFTER INSERT ON task_assignments
WHEN NEW.current_attempt_id IS NULL BEGIN
    UPDATE task_assignments SET current_attempt_id=NEW.op_id WHERE op_id=NEW.op_id;
END;
ALTER TABLE client_admissions ADD COLUMN disposition TEXT;
ALTER TABLE client_admissions ADD COLUMN successor_id TEXT REFERENCES client_admissions(op_id);
ALTER TABLE client_admissions ADD COLUMN owner_instance TEXT;
UPDATE client_admissions SET disposition='legacy handoff acknowledged' WHERE advanced_at IS NOT NULL;
-- A delayed response may attach evidence, but cannot revive a finished attempt.
CREATE TRIGGER admission_terminal_absorbing BEFORE UPDATE OF status ON client_admissions
WHEN OLD.status IN ('completed','failed','interrupted','cancelled')
 AND NEW.status != OLD.status BEGIN
    SELECT RAISE(ABORT,'terminal execution cannot return to running');
END;
CREATE TRIGGER admission_receipt_immutable BEFORE UPDATE OF run_id ON client_admissions
WHEN OLD.run_id IS NOT NULL AND NEW.run_id IS NOT OLD.run_id BEGIN
    SELECT RAISE(ABORT,'execution receipt cannot change');
END;
CREATE TRIGGER admission_handoff_explicit BEFORE UPDATE OF advanced_at ON client_admissions
WHEN NEW.advanced_at IS NOT NULL AND NEW.disposition IS NULL BEGIN
    SELECT RAISE(ABORT,'acknowledged handoff needs a disposition');
END;
ALTER TABLE client_admissions ADD COLUMN failure_counted INTEGER NOT NULL DEFAULT 0 CHECK(failure_counted IN (0,1));
CREATE UNIQUE INDEX assignment_current_attempt ON task_assignments(current_attempt_id);
-- Cross-store commands whose effect is wholly in the workspace acknowledge that effect here.
CREATE TABLE transition_effects (
    command_id TEXT PRIMARY KEY,
    result TEXT NOT NULL,
    committed_at TEXT NOT NULL
);
CREATE TRIGGER transition_effects_log_insert AFTER INSERT ON transition_effects BEGIN
    INSERT INTO change_log(at,tbl,row_id,op) VALUES (strftime('%Y-%m-%dT%H:%M:%fZ','now'),'transition_effects',NEW.rowid,'insert');
END;
CREATE TRIGGER transition_effects_log_update AFTER UPDATE ON transition_effects BEGIN
    INSERT INTO change_log(at,tbl,row_id,op) VALUES (strftime('%Y-%m-%dT%H:%M:%fZ','now'),'transition_effects',NEW.rowid,'update');
END;
CREATE TRIGGER transition_effects_log_delete AFTER DELETE ON transition_effects BEGIN
    INSERT INTO change_log(at,tbl,row_id,op) VALUES (strftime('%Y-%m-%dT%H:%M:%fZ','now'),'transition_effects',OLD.rowid,'delete');
END;
ALTER TABLE task_assignments ADD COLUMN recovery_deadline TEXT;
ALTER TABLE client_admissions ADD COLUMN delivery_failures INTEGER NOT NULL DEFAULT 0;
ALTER TABLE client_admissions ADD COLUMN retry_at TEXT;
