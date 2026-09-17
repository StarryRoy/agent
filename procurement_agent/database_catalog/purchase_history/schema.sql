CREATE TABLE IF NOT EXISTS purchase_history (
    id INTEGER PRIMARY KEY,
    supplier_id INTEGER NOT NULL REFERENCES suppliers(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    quantity INTEGER NOT NULL,
    unit_price REAL NOT NULL,
    ordered_at TEXT NOT NULL,
    promised_delivery_days INTEGER NOT NULL,
    actual_delivery_days INTEGER NOT NULL,
    quality_result TEXT NOT NULL
);
