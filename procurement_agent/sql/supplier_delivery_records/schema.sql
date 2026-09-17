CREATE TABLE IF NOT EXISTS supplier_delivery_records (
    id INTEGER PRIMARY KEY,
    supplier_id INTEGER NOT NULL REFERENCES suppliers(id),
    deliveries INTEGER NOT NULL CHECK (deliveries >= 0),
    on_time_deliveries INTEGER NOT NULL CHECK (on_time_deliveries >= 0 AND on_time_deliveries <= deliveries),
    average_delay_days REAL NOT NULL CHECK (average_delay_days >= 0),
    recorded_at TEXT NOT NULL,
    note TEXT
);
