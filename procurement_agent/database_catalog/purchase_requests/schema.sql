CREATE TABLE IF NOT EXISTS purchase_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_no TEXT NOT NULL UNIQUE,
    session_id TEXT NOT NULL DEFAULT '',
    department_id INTEGER NOT NULL REFERENCES departments(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    requested_qty INTEGER NOT NULL,
    approved_qty INTEGER NOT NULL,
    required_date TEXT,
    supplier_plan_json TEXT NOT NULL,
    unit_price REAL NOT NULL,
    total_amount REAL NOT NULL,
    status TEXT NOT NULL,
    approval_status TEXT NOT NULL,
    created_at TEXT NOT NULL
);
