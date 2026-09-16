PRAGMA foreign_keys = OFF;

ALTER TABLE workflow_items RENAME TO workflow_items_v1;

CREATE TABLE workflow_items (
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

INSERT INTO workflow_items(
    item_id, job_id, ordinal, source_path, source_path_hash, extension,
    size_bytes, source_sha256, status, output_names_json, audit_issue_count
)
SELECT
    item_id, job_id, ordinal, source_path, source_path_hash, extension,
    size_bytes, source_sha256, status, output_names_json, audit_issue_count
FROM workflow_items_v1;

DROP TABLE workflow_items_v1;
CREATE INDEX IF NOT EXISTS idx_workflow_items_job ON workflow_items(job_id, ordinal);

PRAGMA foreign_keys = ON;
