ALTER TABLE notices ADD COLUMN coworld_id TEXT;
CREATE INDEX notices_coworld_unread ON notices(coworld_id, read_at, id);
