-- 0006 flagged legacy admissions for resolution whatever their status, and closing an admission
-- that had already ended left the flag set, so a finished admission could hold Researcher
-- capacity and appear in blocked_executions forever. A terminal admission has nothing left to
-- resolve: the coordinator now clears the flag when it closes one, and this settles the rest.
UPDATE client_admissions SET resolution_required=0
 WHERE resolution_required=1 AND status IN ('completed','failed','interrupted','cancelled');
