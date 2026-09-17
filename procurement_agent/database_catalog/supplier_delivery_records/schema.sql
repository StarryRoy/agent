CREATE TABLE IF NOT EXISTS supplier_delivery_records (
    id INTEGER PRIMARY KEY,
    supplier_id INTEGER NOT NULL REFERENCES suppliers(id),
    deliveries INTEGER NOT NULL,
    on_time_deliveries INTEGER NOT NULL,
    average_delay_days REAL NOT NULL,
    recorded_at TEXT NOT NULL,
    note TEXT
);
