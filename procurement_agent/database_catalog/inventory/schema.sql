CREATE TABLE IF NOT EXISTS inventory (
    product_id INTEGER PRIMARY KEY REFERENCES products(id),
    current_qty INTEGER NOT NULL CHECK (current_qty >= 0),
    locked_qty INTEGER NOT NULL CHECK (locked_qty >= 0),
    in_transit_qty INTEGER NOT NULL CHECK (in_transit_qty >= 0),
    safety_stock INTEGER NOT NULL CHECK (safety_stock >= 0),
    updated_at TEXT NOT NULL
);
