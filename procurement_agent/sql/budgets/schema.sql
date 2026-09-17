CREATE TABLE IF NOT EXISTS budgets (
    id INTEGER PRIMARY KEY,
    department_id INTEGER NOT NULL REFERENCES departments(id),
    fiscal_year INTEGER NOT NULL,
    total_amount REAL NOT NULL CHECK (total_amount >= 0),
    used_amount REAL NOT NULL CHECK (used_amount >= 0),
    approved_pending_amount REAL NOT NULL CHECK (approved_pending_amount >= 0),
    UNIQUE(department_id, fiscal_year)
);
