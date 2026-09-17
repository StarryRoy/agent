CREATE TABLE IF NOT EXISTS supplier_products (
    id INTEGER PRIMARY KEY,
    supplier_id INTEGER NOT NULL REFERENCES suppliers(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    min_order_qty INTEGER NOT NULL,
    max_capacity INTEGER NOT NULL,
    standard_lead_days INTEGER NOT NULL,
    UNIQUE(supplier_id, product_id)
);
