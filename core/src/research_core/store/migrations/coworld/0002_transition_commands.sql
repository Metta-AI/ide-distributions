-- Worker reports and Orchestrator decisions are requests to the workspace coordinator.
CREATE TABLE transition_commands (
    id TEXT PRIMARY KEY,
    tool TEXT NOT NULL,
    args TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('orchestrator','researcher')),
    actor TEXT NOT NULL,
    created_at TEXT NOT NULL,
    deadline TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    owner_instance TEXT,
    result TEXT,
    completed_at TEXT,
    CHECK((result IS NULL) = (completed_at IS NULL))
);
CREATE TRIGGER transition_commands_log_insert AFTER INSERT ON transition_commands BEGIN
    INSERT INTO change_log(at,tbl,row_id,op) VALUES (strftime('%Y-%m-%dT%H:%M:%fZ','now'),'transition_commands',NEW.rowid,'insert');
END;
CREATE TRIGGER transition_commands_log_update AFTER UPDATE ON transition_commands BEGIN
    INSERT INTO change_log(at,tbl,row_id,op) VALUES (strftime('%Y-%m-%dT%H:%M:%fZ','now'),'transition_commands',NEW.rowid,'update');
END;
CREATE TRIGGER transition_commands_log_delete AFTER DELETE ON transition_commands BEGIN
    INSERT INTO change_log(at,tbl,row_id,op) VALUES (strftime('%Y-%m-%dT%H:%M:%fZ','now'),'transition_commands',OLD.rowid,'delete');
END;
