CREATE TABLE IF NOT EXISTS supplier_products (
    id INTEGER PRIMARY KEY,
    supplier_id INTEGER NOT NULL REFERENCES suppliers(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    min_order_qty INTEGER NOT NULL CHECK (min_order_qty >= 0),
    max_capacity INTEGER NOT NULL CHECK (max_capacity >= 0),
    standard_lead_days INTEGER NOT NULL CHECK (standard_lead_days >= 0),
    UNIQUE(supplier_id, product_id)
);

CREATE INDEX IF NOT EXISTS idx_supplier_product ON supplier_products(product_id);
