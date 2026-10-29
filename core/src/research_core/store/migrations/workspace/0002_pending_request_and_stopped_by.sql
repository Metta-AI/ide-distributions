-- The request a Coworld's setup carries out when it finishes (amendments A4.8, A6.3), and the
-- watcher's mark on a Session it stopped on purpose (A5.6), both JSON/text on the existing rows.
ALTER TABLE coworlds ADD COLUMN pending_request TEXT;
ALTER TABLE sessions ADD COLUMN stopped_by TEXT;
