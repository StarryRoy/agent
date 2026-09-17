CREATE TABLE IF NOT EXISTS budgets (
    id INTEGER PRIMARY KEY,
    department_id INTEGER NOT NULL REFERENCES departments(id),
    fiscal_year INTEGER NOT NULL,
    total_amount REAL NOT NULL,
    used_amount REAL NOT NULL,
    approved_pending_amount REAL NOT NULL,
    UNIQUE(department_id, fiscal_year)
);
