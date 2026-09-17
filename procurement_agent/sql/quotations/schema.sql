CREATE TABLE IF NOT EXISTS quotations (
    id INTEGER PRIMARY KEY,
    supplier_id INTEGER NOT NULL REFERENCES suppliers(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    unit_price REAL NOT NULL CHECK (unit_price >= 0),
    currency TEXT NOT NULL DEFAULT 'CNY',
    min_qty INTEGER NOT NULL CHECK (min_qty >= 0),
    available_qty INTEGER NOT NULL CHECK (available_qty >= 0),
    lead_time_days INTEGER NOT NULL CHECK (lead_time_days >= 0),
    quoted_at TEXT NOT NULL,
    valid_until TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('valid', 'expired', 'revoked'))
);

CREATE INDEX IF NOT EXISTS idx_quote_product ON quotations(product_id, status);
