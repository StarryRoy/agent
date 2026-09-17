CREATE TABLE IF NOT EXISTS purchase_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_no TEXT NOT NULL UNIQUE,
    request_id INTEGER NOT NULL REFERENCES purchase_requests(id),
    supplier_id INTEGER NOT NULL REFERENCES suppliers(id),
    quantity INTEGER NOT NULL,
    unit_price REAL NOT NULL,
    total_amount REAL NOT NULL,
    expected_delivery_date TEXT,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
);
