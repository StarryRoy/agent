CREATE TABLE IF NOT EXISTS supplier_quality_records (
    id INTEGER PRIMARY KEY,
    supplier_id INTEGER NOT NULL REFERENCES suppliers(id),
    inspected_lots INTEGER NOT NULL CHECK (inspected_lots >= 0),
    passed_lots INTEGER NOT NULL CHECK (passed_lots >= 0 AND passed_lots <= inspected_lots),
    severe_incidents INTEGER NOT NULL CHECK (severe_incidents >= 0),
    recorded_at TEXT NOT NULL,
    note TEXT
);
