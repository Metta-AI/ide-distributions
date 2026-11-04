-- A chat message whose run failed for a budget, rate-limit or provider reason waits and resumes
-- (research_core.run_failures). It stays 'queued' with resume_after set, which the snapshot
-- reports as 'waiting', so the status CHECK (and the task_assignments foreign key onto this
-- table) need no rebuild: the failure's class beside its error, the earliest time it resumes,
-- and how many resumes it has had (each resume uses the native key '<op_id>:resume:<n>').
ALTER TABLE client_admissions ADD COLUMN error_class TEXT;
ALTER TABLE client_admissions ADD COLUMN resume_after TEXT;
ALTER TABLE client_admissions ADD COLUMN resumes INTEGER NOT NULL DEFAULT 0;
-- A receipt stays immutable while its run can still report. The one exception is a hold, which
-- records that the run has failed and clears the receipt so the resume can record its own run.
DROP TRIGGER admission_receipt_immutable;
CREATE TRIGGER admission_receipt_immutable BEFORE UPDATE OF run_id ON client_admissions
WHEN OLD.run_id IS NOT NULL AND NEW.run_id IS NOT OLD.run_id
 AND NOT (NEW.run_id IS NULL AND NEW.status='queued' AND NEW.resume_after IS NOT NULL) BEGIN
    SELECT RAISE(ABORT,'execution receipt cannot change');
END;
