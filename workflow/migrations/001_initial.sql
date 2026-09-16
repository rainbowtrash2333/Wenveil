CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS workflow_jobs (
    job_id TEXT PRIMARY KEY,
    operation TEXT NOT NULL CHECK (operation IN ('process', 'restore')),
    status TEXT NOT NULL,
    workflow_version TEXT NOT NULL,
    config_hash TEXT,
    steps_json TEXT NOT NULL,
    request_json TEXT NOT NULL,
    output_dir TEXT NOT NULL,
    log_path TEXT NOT NULL,
    created_at TEXT NOT NULL,
    started_at TEXT,
    updated_at TEXT NOT NULL,
    finished_at TEXT,
    heartbeat_at TEXT,
    resume_count INTEGER NOT NULL DEFAULT 0,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    lease_owner TEXT,
    last_error_code TEXT,
    last_error_summary TEXT,
    attention_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS workflow_items (
    item_id TEXT NOT NULL,
    job_id TEXT NOT NULL REFERENCES workflow_jobs(job_id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL,
    source_path TEXT NOT NULL,
    source_path_hash TEXT NOT NULL,
    extension TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    source_sha256 TEXT,
    status TEXT NOT NULL,
    output_names_json TEXT NOT NULL DEFAULT '[]',
    audit_issue_count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (job_id, item_id),
    UNIQUE (job_id, ordinal)
);

CREATE TABLE IF NOT EXISTS workflow_stages (
    stage_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES workflow_jobs(job_id) ON DELETE CASCADE,
    item_id TEXT NOT NULL DEFAULT '',
    stage_name TEXT NOT NULL,
    status TEXT NOT NULL,
    progress REAL NOT NULL DEFAULT 0,
    attempt INTEGER NOT NULL DEFAULT 0,
    started_at TEXT,
    finished_at TEXT,
    heartbeat_at TEXT,
    artifact_id TEXT,
    error_code TEXT,
    error_summary TEXT,
    UNIQUE (job_id, item_id, stage_name)
);

CREATE TABLE IF NOT EXISTS workflow_artifacts (
    artifact_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES workflow_jobs(job_id) ON DELETE CASCADE,
    item_id TEXT,
    kind TEXT NOT NULL,
    path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    visible INTEGER NOT NULL DEFAULT 0,
    retained INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS workflow_events (
    event_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES workflow_jobs(job_id) ON DELETE CASCADE,
    item_id TEXT,
    stage_id TEXT,
    sequence INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    status TEXT,
    progress REAL,
    safe_summary TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (job_id, sequence)
);

CREATE INDEX IF NOT EXISTS idx_workflow_jobs_status ON workflow_jobs(status, updated_at);
CREATE INDEX IF NOT EXISTS idx_workflow_items_job ON workflow_items(job_id, ordinal);
CREATE INDEX IF NOT EXISTS idx_workflow_stages_job ON workflow_stages(job_id, item_id, stage_name);
CREATE INDEX IF NOT EXISTS idx_workflow_artifacts_job ON workflow_artifacts(job_id, item_id, kind);
CREATE INDEX IF NOT EXISTS idx_workflow_events_job ON workflow_events(job_id, sequence);
