CREATE TABLE IF NOT EXISTS operation_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT,
    entity_type TEXT NOT NULL,
    entity_id INTEGER,
    action TEXT NOT NULL,
    result TEXT NOT NULL,
    details_json TEXT,
    created_at TEXT NOT NULL
);
