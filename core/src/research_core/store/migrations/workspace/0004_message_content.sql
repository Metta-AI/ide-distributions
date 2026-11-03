-- Keep already-admitted text inputs and context during the typed-message cutover.
-- Mark only this migration's writes; pre-existing untracked changes remain visible.
CREATE TEMP TABLE message_migration_cursor AS SELECT COALESCE(MAX(id), 0) AS id FROM change_log;
UPDATE client_admissions SET payload = json_object(
    'input', payload, 'instructions', NULL, 'context', json_extract(request, '$.context')
) WHERE kind = 'message';
UPDATE change_log SET token = 'migration:0004_message_content'
WHERE tbl = 'client_admissions' AND id > (SELECT id FROM message_migration_cursor);
DROP TABLE message_migration_cursor;
