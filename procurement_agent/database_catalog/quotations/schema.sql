CREATE TABLE IF NOT EXISTS quotations (
    id INTEGER PRIMARY KEY,
    supplier_id INTEGER NOT NULL REFERENCES suppliers(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    unit_price REAL NOT NULL,
    currency TEXT NOT NULL DEFAULT 'CNY',
    min_qty INTEGER NOT NULL,
    available_qty INTEGER NOT NULL,
    lead_time_days INTEGER NOT NULL,
    quoted_at TEXT NOT NULL,
    valid_until TEXT NOT NULL,
    status TEXT NOT NULL
);
