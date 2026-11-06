-- AIDE's per-request event log. Each row is one AIDE 1.0.0 ChatEvent that the runtime's single
-- consumer of the request's runs appended before pushing it; chat.events reads only these rows.
-- ``run_id`` and ``source_seq`` name the last gateway event folded into the row: where a
-- restarted consumer resumes. ``seq`` follows its attempt's last row and ``finished`` closes the
-- log; rows are never changed or removed, so they live exactly as long as their admission.
CREATE TABLE request_events (
    request_id TEXT NOT NULL REFERENCES client_admissions(op_id),
    attempt INTEGER NOT NULL CHECK(attempt >= 0),
    seq INTEGER NOT NULL CHECK(seq >= 1),
    kind TEXT NOT NULL,
    event TEXT NOT NULL,
    run_id TEXT,
    source_seq INTEGER,
    UNIQUE(request_id, attempt, seq)
);
CREATE TRIGGER request_events_contiguous BEFORE INSERT ON request_events
WHEN NEW.seq != 1 + (SELECT COALESCE(MAX(seq), 0) FROM request_events
    WHERE request_id = NEW.request_id AND attempt = NEW.attempt) BEGIN
    SELECT RAISE(ABORT, 'a request event takes the next seq of its attempt');
END;
CREATE TRIGGER request_events_closed BEFORE INSERT ON request_events
WHEN EXISTS(SELECT 1 FROM request_events WHERE request_id = NEW.request_id AND kind = 'finished')
BEGIN
    SELECT RAISE(ABORT, 'finished is the last request event');
END;
CREATE TRIGGER request_events_append_only_update BEFORE UPDATE ON request_events BEGIN
    SELECT RAISE(ABORT, 'request_events is append-only');
END;
CREATE TRIGGER request_events_append_only_delete BEFORE DELETE ON request_events BEGIN
    SELECT RAISE(ABORT, 'request_events is append-only');
END;
CREATE TRIGGER request_events_log_insert AFTER INSERT ON request_events BEGIN
    INSERT INTO change_log(at, tbl, row_id, op)
    VALUES (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'request_events', NEW.rowid, 'insert');
END;
