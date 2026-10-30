-- 0001_init.sql: the Coworld store (<coworld>/.state/store.db).
-- Design: Appendix B of docs/designs/ide-profile-refactor-2026-10-28-final.html; plan:
-- docs/plans/ide-profile-refactor/02-store.md (Task 2.1) and the review amendments.
-- The store's shape is fixed machinery: Store.open compares a file's schema with the result of
-- these migrations and refuses any difference. Contents are writable through the library only;
-- the change_log triggers at the end make every other write visible.

CREATE TABLE meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

-- Append-only: the AFTER INSERT change_log trigger below detects rows written outside the
-- library; these two triggers refuse any change to a row once written.
CREATE TABLE events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    actor TEXT NOT NULL,
    kind TEXT NOT NULL,
    subject TEXT NOT NULL,
    payload TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX events_kind ON events(kind, seq);
CREATE INDEX events_subject ON events(subject, seq);
CREATE TRIGGER events_append_only_update BEFORE UPDATE ON events BEGIN
    SELECT RAISE(ABORT, 'events is append-only');
END;
CREATE TRIGGER events_append_only_delete BEFORE DELETE ON events BEGIN
    SELECT RAISE(ABORT, 'events is append-only');
END;

-- One row per changed row, written by triggers with token NULL; Tx stamps its own rows with
-- its token before commit. A NULL token after commit is a write made outside the library.
CREATE TABLE change_log (
    id INTEGER PRIMARY KEY,
    at TEXT NOT NULL,
    tbl TEXT NOT NULL,
    row_id INTEGER,
    op TEXT NOT NULL,
    token TEXT
);
CREATE INDEX change_log_unmarked ON change_log(id) WHERE token IS NULL;

-- The watcher's bookkeeping of which events it handled (events itself never changes).
CREATE TABLE handled_events (
    seq INTEGER PRIMARY KEY REFERENCES events(seq),
    kind TEXT NOT NULL,
    run_id TEXT,
    session_id TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    handled_at TEXT,
    last_error TEXT
);

CREATE TABLE goals (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL CHECK(length(title) BETWEEN 1 AND 200),
    end_state TEXT NOT NULL,
    reason TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('active','parked','completed','abandoned')),
    status_reason TEXT,
    judgement TEXT,
    tags TEXT NOT NULL DEFAULT '[]',
    description TEXT NOT NULL DEFAULT '',
    where_it_stands TEXT NOT NULL DEFAULT '',
    updates_for_human TEXT NOT NULL DEFAULT '',
    created TEXT NOT NULL,
    created_by TEXT NOT NULL,
    updated TEXT NOT NULL,
    CHECK(status NOT IN ('completed','abandoned','parked')
          OR (status_reason IS NOT NULL AND status_reason <> ''))
);
CREATE INDEX goals_status ON goals(status);

-- goal_id is NULL only for maintenance Tasks (a broken capability, an Orchestrator change).
-- failure is the {what_failed, error, exit_code, tried, next_step} JSON (B.4).
CREATE TABLE tasks (
    id INTEGER PRIMARY KEY,
    goal_id INTEGER REFERENCES goals(id),
    type TEXT NOT NULL CHECK(type IN ('research','improvement','ideation','maintenance')),
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    reasoning TEXT NOT NULL DEFAULT '',
    for_human TEXT NOT NULL DEFAULT '',
    end_condition TEXT CHECK(end_condition IN ('deliverable','steps','effort')),
    end_detail TEXT,
    experiment_id INTEGER REFERENCES experiments(id),
    status TEXT NOT NULL
        CHECK(status IN ('proposed','unclaimed','in-progress','complete','abandoned')),
    priority TEXT NOT NULL DEFAULT 'normal' CHECK(priority IN ('normal','high')),
    proposed_by TEXT,
    approved_by TEXT,
    approved_at TEXT,
    outcome TEXT CHECK(outcome IN ('succeeded','failed','inconclusive','abandoned')),
    result TEXT,
    failure TEXT,
    last_error_seen TEXT,
    waiting_on TEXT,
    held_by TEXT,
    lease_epoch INTEGER NOT NULL DEFAULT 0,
    lease_renewed TEXT,
    run_id TEXT,
    worktree TEXT,
    branch TEXT,
    ideation_round INTEGER,
    checklist TEXT,
    merge TEXT,
    completed_commit TEXT,
    capability TEXT,
    created TEXT NOT NULL,
    created_by TEXT NOT NULL,
    updated TEXT NOT NULL,
    CHECK(goal_id IS NOT NULL OR type = 'maintenance'),
    CHECK(status <> 'abandoned' OR failure IS NOT NULL)
);
CREATE INDEX tasks_status ON tasks(status, priority, created);
CREATE INDEX tasks_goal ON tasks(goal_id, status);
CREATE INDEX tasks_held_by ON tasks(held_by);

CREATE TABLE task_deps (
    task_id INTEGER NOT NULL REFERENCES tasks(id),
    depends_on INTEGER NOT NULL REFERENCES tasks(id),
    PRIMARY KEY (task_id, depends_on),
    CHECK(task_id <> depends_on)
);
CREATE INDEX task_deps_depends_on ON task_deps(depends_on);

-- Sessions that worked a Task, with the lease epoch they claimed it at.
CREATE TABLE task_sessions (
    task_id INTEGER NOT NULL REFERENCES tasks(id),
    session_id TEXT NOT NULL,
    kind TEXT CHECK(kind IN ('task','change')),
    epoch INTEGER NOT NULL DEFAULT 0,
    started TEXT,
    PRIMARY KEY (task_id, session_id)
);

CREATE TABLE task_messages (
    id INTEGER PRIMARY KEY,
    task_id INTEGER NOT NULL REFERENCES tasks(id),
    direction TEXT NOT NULL CHECK(direction IN ('to_researcher','to_orchestrator')),
    kind TEXT NOT NULL CHECK(kind IN ('message','question','answer')),
    text TEXT NOT NULL,
    created TEXT,
    created_by TEXT,
    delivered TEXT
);
CREATE INDEX task_messages_task ON task_messages(task_id, direction, delivered);

-- artifact is the {kind, path, digest, size} JSON; uploaded is {policy_version_id, at}.
CREATE TABLE versions (
    id INTEGER PRIMARY KEY,
    summary TEXT NOT NULL,
    source TEXT NOT NULL CHECK(source IN ('built','adopted')),
    commit_sha TEXT,
    branch TEXT,
    artifact TEXT,
    task_id INTEGER REFERENCES tasks(id),
    experiment_id INTEGER REFERENCES experiments(id),
    uploaded TEXT,
    image TEXT,
    policy_name TEXT,
    created TEXT NOT NULL,
    created_by TEXT NOT NULL,
    CHECK(source <> 'built' OR (commit_sha IS NOT NULL AND artifact IS NOT NULL))
);
CREATE TABLE version_parents (
    version_id INTEGER NOT NULL REFERENCES versions(id),
    parent_id INTEGER NOT NULL REFERENCES versions(id),
    PRIMARY KEY (version_id, parent_id)
);

CREATE TABLE champion (
    league_id TEXT PRIMARY KEY,
    division_id TEXT,
    membership_id TEXT,
    version_id INTEGER REFERENCES versions(id),
    policy_version_id TEXT,
    state TEXT CHECK(state IN ('pending','processing','placed','rejected','champion','none')),
    since TEXT,
    approved_by TEXT,
    reason TEXT,
    synced TEXT
);
CREATE TABLE champion_history (
    id INTEGER PRIMARY KEY,
    league_id TEXT NOT NULL,
    policy_version_id TEXT,
    state TEXT,
    at TEXT,
    note TEXT
);
CREATE INDEX champion_history_league ON champion_history(league_id, id);

CREATE TABLE claims (
    id INTEGER PRIMARY KEY,
    summary TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL CHECK(status IN ('active','superseded','refuted')),
    superseded_by INTEGER REFERENCES claims(id),
    see_also TEXT NOT NULL DEFAULT '[]',
    created TEXT,
    created_by TEXT,
    updated TEXT
);
CREATE INDEX claims_status ON claims(status);
CREATE TABLE claim_evidence (
    id INTEGER PRIMARY KEY,
    claim_id INTEGER NOT NULL REFERENCES claims(id),
    type TEXT NOT NULL,
    ref TEXT NOT NULL,
    stance TEXT NOT NULL CHECK(stance IN ('supports','contradicts','qualifies')),
    note TEXT NOT NULL DEFAULT '',
    added TEXT,
    "by" TEXT
);
CREATE INDEX claim_evidence_claim ON claim_evidence(claim_id);
CREATE INDEX claim_evidence_ref ON claim_evidence(type, ref);
CREATE VIRTUAL TABLE claims_fts USING fts5(
    summary, body, tags, content='claims', content_rowid='id'
);
CREATE TRIGGER claims_fts_insert AFTER INSERT ON claims BEGIN
    INSERT INTO claims_fts(rowid, summary, body, tags)
    VALUES (NEW.id, NEW.summary, NEW.body, NEW.tags);
END;
CREATE TRIGGER claims_fts_delete AFTER DELETE ON claims BEGIN
    INSERT INTO claims_fts(claims_fts, rowid, summary, body, tags)
    VALUES ('delete', OLD.id, OLD.summary, OLD.body, OLD.tags);
END;
CREATE TRIGGER claims_fts_update AFTER UPDATE ON claims BEGIN
    INSERT INTO claims_fts(claims_fts, rowid, summary, body, tags)
    VALUES ('delete', OLD.id, OLD.summary, OLD.body, OLD.tags);
    INSERT INTO claims_fts(rowid, summary, body, tags)
    VALUES (NEW.id, NEW.summary, NEW.body, NEW.tags);
END;

-- Replays and outputs cited by claims, with the data to re-download them (B.7).
CREATE TABLE pins (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    ref TEXT NOT NULL,
    download TEXT,
    created TEXT,
    UNIQUE (kind, ref)
);

-- hypothesis, methods and results are the JSON sections of B.8; design_sha256 locks the first
-- two when the Experiment starts.
CREATE TABLE experiments (
    id INTEGER PRIMARY KEY,
    summary TEXT NOT NULL,
    goal_id INTEGER REFERENCES goals(id),
    task_id INTEGER REFERENCES tasks(id),
    tags TEXT DEFAULT '[]',
    status TEXT NOT NULL CHECK(status IN
        ('proposed','running','confirmed','refuted','inconclusive','abandoned')),
    hypothesis TEXT NOT NULL DEFAULT '{}',
    methods TEXT NOT NULL DEFAULT '{}',
    results TEXT NOT NULL DEFAULT '{}',
    related_claims TEXT DEFAULT '[]',
    related_experiments TEXT DEFAULT '[]',
    design_sha256 TEXT,
    created TEXT,
    closed TEXT,
    abandon_reason TEXT,
    created_by TEXT
);
CREATE INDEX experiments_status ON experiments(status);
CREATE INDEX experiments_goal ON experiments(goal_id);
CREATE TABLE experiment_runs (
    id INTEGER PRIMARY KEY,
    experiment_id INTEGER NOT NULL REFERENCES experiments(id),
    kind TEXT,
    platform_request_id TEXT,
    arm TEXT,
    seed INTEGER,
    validity TEXT NOT NULL DEFAULT 'valid',
    state TEXT,
    episodes_total INTEGER,
    episodes_completed INTEGER,
    results TEXT,
    created TEXT,
    updated TEXT
);
CREATE INDEX experiment_runs_experiment ON experiment_runs(experiment_id);
CREATE INDEX experiment_runs_request ON experiment_runs(platform_request_id);
CREATE VIRTUAL TABLE experiments_fts USING fts5(
    summary, hypothesis, methods, results, tags, content='experiments', content_rowid='id'
);
CREATE TRIGGER experiments_fts_insert AFTER INSERT ON experiments BEGIN
    INSERT INTO experiments_fts(rowid, summary, hypothesis, methods, results, tags)
    VALUES (NEW.id, NEW.summary, NEW.hypothesis, NEW.methods, NEW.results, NEW.tags);
END;
CREATE TRIGGER experiments_fts_delete AFTER DELETE ON experiments BEGIN
    INSERT INTO experiments_fts(experiments_fts, rowid, summary, hypothesis, methods, results, tags)
    VALUES ('delete', OLD.id, OLD.summary, OLD.hypothesis, OLD.methods, OLD.results, OLD.tags);
END;
CREATE TRIGGER experiments_fts_update AFTER UPDATE ON experiments BEGIN
    INSERT INTO experiments_fts(experiments_fts, rowid, summary, hypothesis, methods, results, tags)
    VALUES ('delete', OLD.id, OLD.summary, OLD.hypothesis, OLD.methods, OLD.results, OLD.tags);
    INSERT INTO experiments_fts(rowid, summary, hypothesis, methods, results, tags)
    VALUES (NEW.id, NEW.summary, NEW.hypothesis, NEW.methods, NEW.results, NEW.tags);
END;

CREATE TABLE directives (
    id INTEGER PRIMARY KEY,
    text TEXT NOT NULL,
    scope TEXT NOT NULL,
    source TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('active','retired')),
    created TEXT,
    created_by TEXT,
    retired TEXT
);
CREATE INDEX directives_scope ON directives(scope, status);

CREATE TABLE capabilities (
    name TEXT PRIMARY KEY,
    kind TEXT,
    status TEXT NOT NULL CHECK(status IN ('unknown','ok','broken')),
    last_check TEXT,
    output TEXT,
    maintenance_task_id INTEGER REFERENCES tasks(id),
    command TEXT
);

CREATE TABLE settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated TEXT,
    updated_by TEXT,
    cites TEXT
);

CREATE TABLE ui_state (
    conversation TEXT PRIMARY KEY,
    doc TEXT NOT NULL DEFAULT '{}',
    version INTEGER NOT NULL DEFAULT 0,
    updated TEXT
);

-- One row per external action, written before the platform call and resolved after (B.13).
CREATE TABLE intents (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL CHECK(kind IN ('upload','submit','xp','forum','wiki','run','champion')),
    subject TEXT,
    key TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('pending','done','failed')),
    result TEXT,
    created TEXT,
    resolved TEXT,
    created_by TEXT,
    UNIQUE (kind, key)
);
CREATE INDEX intents_state ON intents(state, kind);

-- change_log triggers: every record table, plus events on insert. meta and change_log
-- itself are not logged.
CREATE TRIGGER events_log_insert AFTER INSERT ON events BEGIN
    INSERT INTO change_log(at, tbl, row_id, op)
    VALUES (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'events', NEW.rowid, 'insert');
END;
CREATE TRIGGER handled_events_log_insert AFTER INSERT ON handled_events BEGIN
    INSERT INTO change_log(at, tbl, row_id, op)
    VALUES (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'handled_events', NEW.rowid, 'insert');
END;
CREATE TRIGGER handled_events_log_update AFTER UPDATE ON handled_events BEGIN
    INSERT INTO change_log(at, tbl, row_id, op)
    VALUES (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'handled_events', OLD.rowid, 'update');
END;
CREATE TRIGGER handled_events_log_delete AFTER DELETE ON handled_events BEGIN
    INSERT INTO change_log(at, tbl, row_id, op)
    VALUES (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'handled_events', OLD.rowid, 'delete');
END;
CREATE TRIGGER goals_log_insert AFTER INSERT ON goals BEGIN
    INSERT INTO change_log(at, tbl, row_id, op)
    VALUES (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'goals', NEW.rowid, 'insert');
END;
CREATE TRIGGER goals_log_update AFTER UPDATE ON goals BEGIN
    INSERT INTO change_log(at, tbl, row_id, op)
    VALUES (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'goals', OLD.rowid, 'update');
END;
CREATE TRIGGER goals_log_delete AFTER DELETE ON goals BEGIN
    INSERT INTO change_log(at, tbl, row_id, op)
    VALUES (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'goals', OLD.rowid, 'delete');
END;
CREATE TRIGGER tasks_log_insert AFTER INSERT ON tasks BEGIN
    INSERT INTO change_log(at, tbl, row_id, op)
    VALUES (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'tasks', NEW.rowid, 'insert');
END;
CREATE TRIGGER tasks_log_update AFTER UPDATE ON tasks BEGIN
    INSERT INTO change_log(at, tbl, row_id, op)
    VALUES (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'tasks', OLD.rowid, 'update');
END;
CREATE TRIGGER tasks_log_delete AFTER DELETE ON tasks BEGIN
    INSERT INTO change_log(at, tbl, row_id, op)
    VALUES (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'tasks', OLD.rowid, 'delete');
END;
CREATE TRIGGER task_deps_log_insert AFTER INSERT ON task_deps BEGIN
    INSERT INTO change_log(at, tbl, row_id, op)
    VALUES (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'task_deps', NEW.rowid, 'insert');
END;
CREATE TRIGGER task_deps_log_update AFTER UPDATE ON task_deps BEGIN
    INSERT INTO change_log(at, tbl, row_id, op)
    VALUES (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'task_deps', OLD.rowid, 'update');
END;
CREATE TRIGGER task_deps_log_delete AFTER DELETE ON task_deps BEGIN
    INSERT INTO change_log(at, tbl, row_id, op)
    VALUES (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'task_deps', OLD.rowid, 'delete');
END;
CREATE TRIGGER task_sessions_log_insert AFTER INSERT ON task_sessions BEGIN
    INSERT INTO change_log(at, tbl, row_id, op)
    VALUES (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'task_sessions', NEW.rowid, 'insert');
END;
CREATE TRIGGER task_sessions_log_update AFTER UPDATE ON task_sessions BEGIN
    INSERT INTO change_log(at, tbl, row_id, op)
    VALUES (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'task_sessions', OLD.rowid, 'update');
END;
CREATE TRIGGER task_sessions_log_delete AFTER DELETE ON task_sessions BEGIN
    INSERT INTO change_log(at, tbl, row_id, op)
    VALUES (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'task_sessions', OLD.rowid, 'delete');
END;
CREATE TRIGGER task_messages_log_insert AFTER INSERT ON task_messages BEGIN
    INSERT INTO change_log(at, tbl, row_id, op)
    VALUES (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'task_messages', NEW.rowid, 'insert');
END;
CREATE TRIGGER task_messages_log_update AFTER UPDATE ON task_messages BEGIN
    INSERT INTO change_log(at, tbl, row_id, op)
    VALUES (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'task_messages', OLD.rowid, 'update');
END;
CREATE TRIGGER task_messages_log_delete AFTER DELETE ON task_messages BEGIN
    INSERT INTO change_log(at, tbl, row_id, op)
    VALUES (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'task_messages', OLD.rowid, 'delete');
END;
CREATE TRIGGER versions_log_insert AFTER INSERT ON versions BEGIN
    INSERT INTO change_log(at, tbl, row_id, op)
    VALUES (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'versions', NEW.rowid, 'insert');
END;
CREATE TRIGGER versions_log_update AFTER UPDATE ON versions BEGIN
    INSERT INTO change_log(at, tbl, row_id, op)
    VALUES (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'versions', OLD.rowid, 'update');
END;
CREATE TRIGGER versions_log_delete AFTER DELETE ON versions BEGIN
    INSERT INTO change_log(at, tbl, row_id, op)
    VALUES (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'versions', OLD.rowid, 'delete');
END;
CREATE TRIGGER version_parents_log_insert AFTER INSERT ON version_parents BEGIN
    INSERT INTO change_log(at, tbl, row_id, op)
    VALUES (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'version_parents', NEW.rowid, 'insert');
END;
CREATE TRIGGER version_parents_log_update AFTER UPDATE ON version_parents BEGIN
    INSERT INTO change_log(at, tbl, row_id, op)
    VALUES (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'version_parents', OLD.rowid, 'update');
END;
CREATE TRIGGER version_parents_log_delete AFTER DELETE ON version_parents BEGIN
    INSERT INTO change_log(at, tbl, row_id, op)
    VALUES (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'version_parents', OLD.rowid, 'delete');
END;
CREATE TRIGGER champion_log_insert AFTER INSERT ON champion BEGIN
    INSERT INTO change_log(at, tbl, row_id, op)
    VALUES (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'champion', NEW.rowid, 'insert');
END;
CREATE TRIGGER champion_log_update AFTER UPDATE ON champion BEGIN
    INSERT INTO change_log(at, tbl, row_id, op)
    VALUES (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'champion', OLD.rowid, 'update');
END;
CREATE TRIGGER champion_log_delete AFTER DELETE ON champion BEGIN
    INSERT INTO change_log(at, tbl, row_id, op)
    VALUES (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'champion', OLD.rowid, 'delete');
END;
CREATE TRIGGER champion_history_log_insert AFTER INSERT ON champion_history BEGIN
    INSERT INTO change_log(at, tbl, row_id, op)
    VALUES (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'champion_history', NEW.rowid, 'insert');
END;
CREATE TRIGGER champion_history_log_update AFTER UPDATE ON champion_history BEGIN
    INSERT INTO change_log(at, tbl, row_id, op)
    VALUES (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'champion_history', OLD.rowid, 'update');
END;
CREATE TRIGGER champion_history_log_delete AFTER DELETE ON champion_history BEGIN
    INSERT INTO change_log(at, tbl, row_id, op)
    VALUES (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'champion_history', OLD.rowid, 'delete');
END;
CREATE TRIGGER claims_log_insert AFTER INSERT ON claims BEGIN
    INSERT INTO change_log(at, tbl, row_id, op)
    VALUES (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'claims', NEW.rowid, 'insert');
END;
CREATE TRIGGER claims_log_update AFTER UPDATE ON claims BEGIN
    INSERT INTO change_log(at, tbl, row_id, op)
    VALUES (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'claims', OLD.rowid, 'update');
END;
CREATE TRIGGER claims_log_delete AFTER DELETE ON claims BEGIN
    INSERT INTO change_log(at, tbl, row_id, op)
    VALUES (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'claims', OLD.rowid, 'delete');
END;
CREATE TRIGGER claim_evidence_log_insert AFTER INSERT ON claim_evidence BEGIN
    INSERT INTO change_log(at, tbl, row_id, op)
    VALUES (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'claim_evidence', NEW.rowid, 'insert');
END;
CREATE TRIGGER claim_evidence_log_update AFTER UPDATE ON claim_evidence BEGIN
    INSERT INTO change_log(at, tbl, row_id, op)
    VALUES (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'claim_evidence', OLD.rowid, 'update');
END;
CREATE TRIGGER claim_evidence_log_delete AFTER DELETE ON claim_evidence BEGIN
    INSERT INTO change_log(at, tbl, row_id, op)
    VALUES (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'claim_evidence', OLD.rowid, 'delete');
END;
CREATE TRIGGER pins_log_insert AFTER INSERT ON pins BEGIN
    INSERT INTO change_log(at, tbl, row_id, op)
    VALUES (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'pins', NEW.rowid, 'insert');
END;
CREATE TRIGGER pins_log_update AFTER UPDATE ON pins BEGIN
    INSERT INTO change_log(at, tbl, row_id, op)
    VALUES (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'pins', OLD.rowid, 'update');
END;
CREATE TRIGGER pins_log_delete AFTER DELETE ON pins BEGIN
    INSERT INTO change_log(at, tbl, row_id, op)
    VALUES (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'pins', OLD.rowid, 'delete');
END;
CREATE TRIGGER experiments_log_insert AFTER INSERT ON experiments BEGIN
    INSERT INTO change_log(at, tbl, row_id, op)
    VALUES (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'experiments', NEW.rowid, 'insert');
END;
CREATE TRIGGER experiments_log_update AFTER UPDATE ON experiments BEGIN
    INSERT INTO change_log(at, tbl, row_id, op)
    VALUES (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'experiments', OLD.rowid, 'update');
END;
CREATE TRIGGER experiments_log_delete AFTER DELETE ON experiments BEGIN
    INSERT INTO change_log(at, tbl, row_id, op)
    VALUES (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'experiments', OLD.rowid, 'delete');
END;
CREATE TRIGGER experiment_runs_log_insert AFTER INSERT ON experiment_runs BEGIN
    INSERT INTO change_log(at, tbl, row_id, op)
    VALUES (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'experiment_runs', NEW.rowid, 'insert');
END;
CREATE TRIGGER experiment_runs_log_update AFTER UPDATE ON experiment_runs BEGIN
    INSERT INTO change_log(at, tbl, row_id, op)
    VALUES (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'experiment_runs', OLD.rowid, 'update');
END;
CREATE TRIGGER experiment_runs_log_delete AFTER DELETE ON experiment_runs BEGIN
    INSERT INTO change_log(at, tbl, row_id, op)
    VALUES (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'experiment_runs', OLD.rowid, 'delete');
END;
CREATE TRIGGER directives_log_insert AFTER INSERT ON directives BEGIN
    INSERT INTO change_log(at, tbl, row_id, op)
    VALUES (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'directives', NEW.rowid, 'insert');
END;
CREATE TRIGGER directives_log_update AFTER UPDATE ON directives BEGIN
    INSERT INTO change_log(at, tbl, row_id, op)
    VALUES (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'directives', OLD.rowid, 'update');
END;
CREATE TRIGGER directives_log_delete AFTER DELETE ON directives BEGIN
    INSERT INTO change_log(at, tbl, row_id, op)
    VALUES (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'directives', OLD.rowid, 'delete');
END;
CREATE TRIGGER capabilities_log_insert AFTER INSERT ON capabilities BEGIN
    INSERT INTO change_log(at, tbl, row_id, op)
    VALUES (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'capabilities', NEW.rowid, 'insert');
END;
CREATE TRIGGER capabilities_log_update AFTER UPDATE ON capabilities BEGIN
    INSERT INTO change_log(at, tbl, row_id, op)
    VALUES (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'capabilities', OLD.rowid, 'update');
END;
CREATE TRIGGER capabilities_log_delete AFTER DELETE ON capabilities BEGIN
    INSERT INTO change_log(at, tbl, row_id, op)
    VALUES (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'capabilities', OLD.rowid, 'delete');
END;
CREATE TRIGGER settings_log_insert AFTER INSERT ON settings BEGIN
    INSERT INTO change_log(at, tbl, row_id, op)
    VALUES (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'settings', NEW.rowid, 'insert');
END;
CREATE TRIGGER settings_log_update AFTER UPDATE ON settings BEGIN
    INSERT INTO change_log(at, tbl, row_id, op)
    VALUES (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'settings', OLD.rowid, 'update');
END;
CREATE TRIGGER settings_log_delete AFTER DELETE ON settings BEGIN
    INSERT INTO change_log(at, tbl, row_id, op)
    VALUES (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'settings', OLD.rowid, 'delete');
END;
CREATE TRIGGER ui_state_log_insert AFTER INSERT ON ui_state BEGIN
    INSERT INTO change_log(at, tbl, row_id, op)
    VALUES (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'ui_state', NEW.rowid, 'insert');
END;
CREATE TRIGGER ui_state_log_update AFTER UPDATE ON ui_state BEGIN
    INSERT INTO change_log(at, tbl, row_id, op)
    VALUES (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'ui_state', OLD.rowid, 'update');
END;
CREATE TRIGGER ui_state_log_delete AFTER DELETE ON ui_state BEGIN
    INSERT INTO change_log(at, tbl, row_id, op)
    VALUES (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'ui_state', OLD.rowid, 'delete');
END;
CREATE TRIGGER intents_log_insert AFTER INSERT ON intents BEGIN
    INSERT INTO change_log(at, tbl, row_id, op)
    VALUES (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'intents', NEW.rowid, 'insert');
END;
CREATE TRIGGER intents_log_update AFTER UPDATE ON intents BEGIN
    INSERT INTO change_log(at, tbl, row_id, op)
    VALUES (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'intents', OLD.rowid, 'update');
END;
CREATE TRIGGER intents_log_delete AFTER DELETE ON intents BEGIN
    INSERT INTO change_log(at, tbl, row_id, op)
    VALUES (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'intents', OLD.rowid, 'delete');
END;

INSERT INTO meta(key, value) VALUES ('schema_kind', 'coworld');
INSERT INTO meta(key, value) VALUES ('created', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
