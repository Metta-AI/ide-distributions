-- A command's identity comes from code now: an identical call joins the session's newest command
-- whose finished result has not been returned to it yet, and task.assign checks the revision
-- the session last saw when the command was accepted (task_updated) instead of one the model copied.
ALTER TABLE transition_commands ADD COLUMN reply_seen_at TEXT;
ALTER TABLE transition_commands ADD COLUMN task_updated TEXT;
CREATE TABLE task_views (
    session_id TEXT NOT NULL,
    task_id INTEGER NOT NULL,
    updated TEXT NOT NULL,
    PRIMARY KEY(session_id,task_id)
);
CREATE TRIGGER task_views_log_insert AFTER INSERT ON task_views BEGIN
    INSERT INTO change_log(at,tbl,row_id,op) VALUES (strftime('%Y-%m-%dT%H:%M:%fZ','now'),'task_views',NEW.rowid,'insert');
END;
CREATE TRIGGER task_views_log_update AFTER UPDATE ON task_views BEGIN
    INSERT INTO change_log(at,tbl,row_id,op) VALUES (strftime('%Y-%m-%dT%H:%M:%fZ','now'),'task_views',NEW.rowid,'update');
END;
CREATE TRIGGER task_views_log_delete AFTER DELETE ON task_views BEGIN
    INSERT INTO change_log(at,tbl,row_id,op) VALUES (strftime('%Y-%m-%dT%H:%M:%fZ','now'),'task_views',OLD.rowid,'delete');
END;
