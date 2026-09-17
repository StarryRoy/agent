CREATE TABLE IF NOT EXISTS purchase_history (
    id INTEGER PRIMARY KEY,
    supplier_id INTEGER NOT NULL REFERENCES suppliers(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    unit_price REAL NOT NULL CHECK (unit_price >= 0),
    ordered_at TEXT NOT NULL,
    promised_delivery_days INTEGER NOT NULL CHECK (promised_delivery_days >= 0),
    actual_delivery_days INTEGER NOT NULL CHECK (actual_delivery_days >= 0),
    quality_result TEXT NOT NULL CHECK (quality_result IN ('passed', 'passed_with_minor_issues', 'passed_with_rework', 'rejected'))
);

CREATE INDEX IF NOT EXISTS idx_history_product ON purchase_history(product_id);
