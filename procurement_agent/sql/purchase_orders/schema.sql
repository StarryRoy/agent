CREATE TABLE IF NOT EXISTS purchase_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_no TEXT NOT NULL UNIQUE,
    request_id INTEGER NOT NULL REFERENCES purchase_requests(id),
    supplier_id INTEGER NOT NULL REFERENCES suppliers(id),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    unit_price REAL NOT NULL CHECK (unit_price >= 0),
    total_amount REAL NOT NULL CHECK (total_amount >= 0),
    expected_delivery_date TEXT,
    status TEXT NOT NULL CHECK (status IN ('created', 'confirmed', 'in_transit', 'received', 'cancelled')),
    created_at TEXT NOT NULL
);
