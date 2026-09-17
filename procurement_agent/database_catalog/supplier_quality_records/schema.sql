CREATE TABLE IF NOT EXISTS supplier_quality_records (
    id INTEGER PRIMARY KEY,
    supplier_id INTEGER NOT NULL REFERENCES suppliers(id),
    inspected_lots INTEGER NOT NULL,
    passed_lots INTEGER NOT NULL,
    severe_incidents INTEGER NOT NULL,
    recorded_at TEXT NOT NULL,
    note TEXT
);
