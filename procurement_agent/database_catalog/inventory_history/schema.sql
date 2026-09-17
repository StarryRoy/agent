CREATE TABLE IF NOT EXISTS inventory_history (
    id INTEGER PRIMARY KEY,
    product_id INTEGER NOT NULL REFERENCES products(id),
    period TEXT NOT NULL,
    consumed_qty INTEGER NOT NULL CHECK (consumed_qty >= 0)
);
