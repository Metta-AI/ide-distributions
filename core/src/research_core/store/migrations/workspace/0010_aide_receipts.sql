-- Canonical retry identities are independent of the retired transport's envelope/context.
-- Backfill only durable client chat inputs; never invent a new operation identity.
CREATE TABLE aide_receipts (
    request_id TEXT PRIMARY KEY REFERENCES client_admissions(op_id),
    session_id TEXT NOT NULL,
    scope TEXT NOT NULL,
    messages TEXT NOT NULL
);
ALTER TABLE sessions ADD COLUMN pending_inputs TEXT NOT NULL DEFAULT '[]';
CREATE TRIGGER aide_receipts_log_insert AFTER INSERT ON aide_receipts BEGIN
    INSERT INTO change_log(at,tbl,row_id,op) VALUES (strftime('%Y-%m-%dT%H:%M:%fZ','now'),'aide_receipts',NEW.rowid,'insert');
END;
CREATE TRIGGER aide_receipts_log_update AFTER UPDATE ON aide_receipts BEGIN
    INSERT INTO change_log(at,tbl,row_id,op) VALUES (strftime('%Y-%m-%dT%H:%M:%fZ','now'),'aide_receipts',NEW.rowid,'update');
END;
CREATE TRIGGER aide_receipts_log_delete AFTER DELETE ON aide_receipts BEGIN
    INSERT INTO change_log(at,tbl,row_id,op) VALUES (strftime('%Y-%m-%dT%H:%M:%fZ','now'),'aide_receipts',OLD.rowid,'delete');
END;

CREATE TABLE aide_command_receipts (
    request_id TEXT PRIMARY KEY,
    method TEXT NOT NULL,
    params TEXT NOT NULL,
    result TEXT
);
CREATE TRIGGER aide_command_receipts_log_insert AFTER INSERT ON aide_command_receipts BEGIN
    INSERT INTO change_log(at,tbl,row_id,op) VALUES (strftime('%Y-%m-%dT%H:%M:%fZ','now'),'aide_command_receipts',NEW.rowid,'insert');
END;
CREATE TRIGGER aide_command_receipts_log_update AFTER UPDATE ON aide_command_receipts BEGIN
    INSERT INTO change_log(at,tbl,row_id,op) VALUES (strftime('%Y-%m-%dT%H:%M:%fZ','now'),'aide_command_receipts',NEW.rowid,'update');
END;
CREATE TRIGGER aide_command_receipts_log_delete AFTER DELETE ON aide_command_receipts BEGIN
    INSERT INTO change_log(at,tbl,row_id,op) VALUES (strftime('%Y-%m-%dT%H:%M:%fZ','now'),'aide_command_receipts',OLD.rowid,'delete');
END;
