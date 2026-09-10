"""Procurement database bootstrap and deterministic demonstration data."""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY,
    sku TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    unit TEXT NOT NULL,
    quality_standard TEXT
);

CREATE TABLE IF NOT EXISTS inventory (
    product_id INTEGER PRIMARY KEY REFERENCES products(id),
    current_qty INTEGER NOT NULL CHECK (current_qty >= 0),
    locked_qty INTEGER NOT NULL CHECK (locked_qty >= 0),
    in_transit_qty INTEGER NOT NULL CHECK (in_transit_qty >= 0),
    safety_stock INTEGER NOT NULL CHECK (safety_stock >= 0),
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS inventory_history (
    id INTEGER PRIMARY KEY,
    product_id INTEGER NOT NULL REFERENCES products(id),
    period TEXT NOT NULL,
    consumed_qty INTEGER NOT NULL CHECK (consumed_qty >= 0)
);

CREATE TABLE IF NOT EXISTS departments (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS budgets (
    id INTEGER PRIMARY KEY,
    department_id INTEGER NOT NULL REFERENCES departments(id),
    fiscal_year INTEGER NOT NULL,
    total_amount REAL NOT NULL,
    used_amount REAL NOT NULL,
    approved_pending_amount REAL NOT NULL,
    UNIQUE(department_id, fiscal_year)
);

CREATE TABLE IF NOT EXISTS suppliers (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    status TEXT NOT NULL,
    cooperation_status TEXT NOT NULL,
    risk_level TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS supplier_products (
    id INTEGER PRIMARY KEY,
    supplier_id INTEGER NOT NULL REFERENCES suppliers(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    min_order_qty INTEGER NOT NULL,
    max_capacity INTEGER NOT NULL,
    standard_lead_days INTEGER NOT NULL,
    UNIQUE(supplier_id, product_id)
);

CREATE TABLE IF NOT EXISTS quotations (
    id INTEGER PRIMARY KEY,
    supplier_id INTEGER NOT NULL REFERENCES suppliers(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    unit_price REAL NOT NULL,
    currency TEXT NOT NULL DEFAULT 'CNY',
    min_qty INTEGER NOT NULL,
    available_qty INTEGER NOT NULL,
    lead_time_days INTEGER NOT NULL,
    quoted_at TEXT NOT NULL,
    valid_until TEXT NOT NULL,
    status TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS purchase_history (
    id INTEGER PRIMARY KEY,
    supplier_id INTEGER NOT NULL REFERENCES suppliers(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    quantity INTEGER NOT NULL,
    unit_price REAL NOT NULL,
    ordered_at TEXT NOT NULL,
    promised_delivery_days INTEGER NOT NULL,
    actual_delivery_days INTEGER NOT NULL,
    quality_result TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS supplier_quality_records (
    id INTEGER PRIMARY KEY,
    supplier_id INTEGER NOT NULL REFERENCES suppliers(id),
    inspected_lots INTEGER NOT NULL,
    passed_lots INTEGER NOT NULL,
    severe_incidents INTEGER NOT NULL,
    recorded_at TEXT NOT NULL,
    note TEXT
);

CREATE TABLE IF NOT EXISTS supplier_delivery_records (
    id INTEGER PRIMARY KEY,
    supplier_id INTEGER NOT NULL REFERENCES suppliers(id),
    deliveries INTEGER NOT NULL,
    on_time_deliveries INTEGER NOT NULL,
    average_delay_days REAL NOT NULL,
    recorded_at TEXT NOT NULL,
    note TEXT
);

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

CREATE TABLE IF NOT EXISTS purchase_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_no TEXT NOT NULL UNIQUE,
    request_id INTEGER NOT NULL REFERENCES purchase_requests(id),
    supplier_id INTEGER NOT NULL REFERENCES suppliers(id),
    quantity INTEGER NOT NULL,
    unit_price REAL NOT NULL,
    total_amount REAL NOT NULL,
    expected_delivery_date TEXT,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS operation_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT,
    entity_type TEXT NOT NULL,
    entity_id INTEGER,
    action TEXT NOT NULL,
    result TEXT NOT NULL,
    details_json TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_quote_product ON quotations(product_id, status);
CREATE INDEX IF NOT EXISTS idx_supplier_product ON supplier_products(product_id);
CREATE INDEX IF NOT EXISTS idx_history_product ON purchase_history(product_id);

DROP TRIGGER IF EXISTS validate_purchase_execution;
CREATE TRIGGER validate_purchase_execution
BEFORE INSERT ON purchase_requests
WHEN NEW.status = 'approved' AND NEW.approval_status = 'approved'
BEGIN
    SELECT CASE
        WHEN json_valid(NEW.supplier_plan_json) = 0
        THEN RAISE(ABORT, 'invalid supplier plan json')
    END;
    SELECT CASE
        WHEN COALESCE(json_array_length(NEW.supplier_plan_json, '$.allocations'), 0) = 0
        THEN RAISE(ABORT, 'supplier plan has no allocations')
    END;
    SELECT CASE
        WHEN COALESCE((
            SELECT SUM(CAST(json_extract(value, '$.quantity') AS INTEGER))
            FROM json_each(NEW.supplier_plan_json, '$.allocations')
        ), 0) <> NEW.approved_qty
        THEN RAISE(ABORT, 'allocation quantity mismatch')
    END;
    SELECT CASE
        WHEN ABS(COALESCE((
            SELECT SUM(
                CAST(json_extract(value, '$.quantity') AS INTEGER)
                * CAST(json_extract(value, '$.unit_price') AS REAL)
            )
            FROM json_each(NEW.supplier_plan_json, '$.allocations')
        ), 0) - NEW.total_amount) > 0.01
        THEN RAISE(ABORT, 'allocation amount mismatch')
    END;
    SELECT CASE
        WHEN NOT EXISTS (
            SELECT 1 FROM budgets
            WHERE department_id = NEW.department_id
              AND fiscal_year = CAST(substr(NEW.created_at, 1, 4) AS INTEGER)
              AND total_amount - used_amount - approved_pending_amount >= NEW.total_amount
        )
        THEN RAISE(ABORT, 'insufficient budget at execution')
    END;
END;

DROP TRIGGER IF EXISTS commit_purchase_execution;
CREATE TRIGGER commit_purchase_execution
AFTER INSERT ON purchase_requests
WHEN NEW.status = 'approved' AND NEW.approval_status = 'approved'
BEGIN
    INSERT INTO purchase_orders(
        order_no, request_id, supplier_id, quantity, unit_price, total_amount,
        expected_delivery_date, status, created_at
    )
    SELECT
        replace(NEW.request_no, 'PR-', 'PO-') || '-' || (CAST(key AS INTEGER) + 1),
        NEW.id,
        CAST(json_extract(value, '$.supplier_id') AS INTEGER),
        CAST(json_extract(value, '$.quantity') AS INTEGER),
        CAST(json_extract(value, '$.unit_price') AS REAL),
        CAST(json_extract(value, '$.quantity') AS INTEGER)
            * CAST(json_extract(value, '$.unit_price') AS REAL),
        date(NEW.created_at, '+' || CAST(json_extract(value, '$.lead_time_days') AS INTEGER) || ' days'),
        'created',
        NEW.created_at
    FROM json_each(NEW.supplier_plan_json, '$.allocations');

    UPDATE budgets
    SET approved_pending_amount = approved_pending_amount + NEW.total_amount
    WHERE department_id = NEW.department_id
      AND fiscal_year = CAST(substr(NEW.created_at, 1, 4) AS INTEGER)
      AND total_amount - used_amount - approved_pending_amount >= NEW.total_amount;

    UPDATE purchase_requests SET status = 'ordered' WHERE id = NEW.id;

    INSERT INTO operation_logs(
        session_id, entity_type, entity_id, action, result, details_json, created_at
    ) VALUES(
        NEW.session_id,
        'purchase_request',
        NEW.id,
        'approve_and_execute',
        'success',
        json_object(
            'request_no', NEW.request_no,
            'approval_status', NEW.approval_status,
            'order_count', json_array_length(NEW.supplier_plan_json, '$.allocations'),
            'total_amount', NEW.total_amount
        ),
        NEW.created_at
    );

    SELECT CASE
        WHEN COALESCE(json_extract(NEW.supplier_plan_json, '$.__force_failure'), 0) = 1
        THEN RAISE(ABORT, 'scripted failure after all trigger steps')
    END;
END;
"""


SEED_SQL = """
INSERT OR IGNORE INTO products(id, sku, name, category, unit, quality_standard) VALUES
  (1, 'DEV-TAB-STD', '标准工业平板设备', '设备', '台', '企业级，三年质保，批次合格率不低于95%'),
  (2, 'LAP-BIZ-14', '商务笔记本电脑', '设备', '台', '企业级，三年上门服务'),
  (3, 'MON-27-4K', '27英寸4K显示器', '设备', '台', '坏点A级标准');

INSERT OR IGNORE INTO inventory(product_id, current_qty, locked_qty, in_transit_qty, safety_stock, updated_at) VALUES
  (1, 180, 30, 40, 60, '2026-09-10'),
  (2, 85, 20, 0, 25, '2026-09-10'),
  (3, 360, 40, 60, 50, '2026-09-10');

INSERT OR IGNORE INTO inventory_history(id, product_id, period, consumed_qty) VALUES
  (1, 1, '2026-03', 68), (2, 1, '2026-04', 74), (3, 1, '2026-05', 80),
  (4, 1, '2026-06', 72), (5, 1, '2026-07', 79), (6, 1, '2026-08', 77),
  (7, 2, '2026-06', 18), (8, 2, '2026-07', 20), (9, 2, '2026-08', 22),
  (10, 3, '2026-06', 25), (11, 3, '2026-07', 30), (12, 3, '2026-08', 28);

INSERT OR IGNORE INTO departments(id, code, name) VALUES
  (1, 'IT', '信息技术部'), (2, 'OPS', '运营部'), (3, 'RND', '研发部');

INSERT OR IGNORE INTO budgets(id, department_id, fiscal_year, total_amount, used_amount, approved_pending_amount) VALUES
  (1, 1, 2026, 2000000, 920000, 180000),
  (2, 2, 2026, 1200000, 870000, 120000),
  (3, 3, 2026, 3500000, 1800000, 350000);

INSERT OR IGNORE INTO suppliers(id, code, name, status, cooperation_status, risk_level) VALUES
  (1, 'SUP-A', '华东智造有限公司', 'active', 'strategic', 'low'),
  (2, 'SUP-B', '新锐科技股份有限公司', 'active', 'approved', 'medium'),
  (3, 'SUP-C', '远航设备贸易有限公司', 'active', 'probation', 'high'),
  (4, 'SUP-D', '稳达工业系统有限公司', 'active', 'approved', 'low'),
  (5, 'SUP-X', '问题供应商有限公司', 'suspended', 'blocked', 'critical');

INSERT OR IGNORE INTO supplier_products(id, supplier_id, product_id, min_order_qty, max_capacity, standard_lead_days) VALUES
  (1, 1, 1, 100, 650, 18), (2, 2, 1, 50, 300, 22),
  (3, 3, 1, 100, 550, 35), (4, 4, 1, 50, 500, 14),
  (5, 5, 1, 10, 800, 10), (6, 1, 2, 20, 300, 16),
  (7, 2, 2, 20, 250, 20), (8, 4, 3, 30, 600, 12);

INSERT OR IGNORE INTO quotations(id, supplier_id, product_id, unit_price, currency, min_qty, available_qty, lead_time_days, quoted_at, valid_until, status) VALUES
  (1, 1, 1, 1480, 'CNY', 100, 650, 18, '2026-09-01', '2026-12-31', 'valid'),
  (2, 2, 1, 1420, 'CNY', 50, 300, 22, '2026-09-02', '2026-11-30', 'valid'),
  (3, 3, 1, 1320, 'CNY', 100, 550, 35, '2026-09-01', '2026-10-31', 'valid'),
  (4, 4, 1, 1710, 'CNY', 50, 500, 14, '2026-09-03', '2026-12-31', 'valid'),
  (5, 5, 1, 1180, 'CNY', 10, 800, 10, '2026-09-03', '2026-12-31', 'valid'),
  (6, 1, 2, 6100, 'CNY', 20, 300, 16, '2026-09-01', '2026-12-31', 'valid'),
  (7, 2, 2, 5750, 'CNY', 20, 250, 20, '2026-09-01', '2026-12-31', 'valid'),
  (8, 4, 3, 2350, 'CNY', 30, 600, 12, '2026-09-01', '2026-12-31', 'valid');

INSERT OR IGNORE INTO purchase_history(id, supplier_id, product_id, quantity, unit_price, ordered_at, promised_delivery_days, actual_delivery_days, quality_result) VALUES
  (1, 1, 1, 420, 1450, '2026-02-15', 20, 19, 'passed'),
  (2, 1, 1, 300, 1460, '2026-05-20', 18, 18, 'passed'),
  (3, 2, 1, 260, 1390, '2026-04-10', 21, 25, 'passed_with_minor_issues'),
  (4, 2, 1, 280, 1400, '2026-07-08', 22, 23, 'passed'),
  (5, 3, 1, 500, 1250, '2026-01-12', 30, 44, 'rejected'),
  (6, 3, 1, 300, 1280, '2026-06-18', 32, 40, 'passed_with_rework'),
  (7, 4, 1, 200, 1680, '2026-03-11', 14, 13, 'passed');

INSERT OR IGNORE INTO supplier_quality_records(id, supplier_id, inspected_lots, passed_lots, severe_incidents, recorded_at, note) VALUES
  (1, 1, 80, 79, 0, '2026-08-31', '稳定'),
  (2, 2, 65, 62, 0, '2026-08-31', '轻微外观问题'),
  (3, 3, 40, 31, 3, '2026-08-31', '曾出现批次返工与拒收'),
  (4, 4, 72, 72, 0, '2026-08-31', '优秀'),
  (5, 5, 20, 12, 4, '2026-08-31', '已暂停合作');

INSERT OR IGNORE INTO supplier_delivery_records(id, supplier_id, deliveries, on_time_deliveries, average_delay_days, recorded_at, note) VALUES
  (1, 1, 60, 58, 0.4, '2026-08-31', '交付稳定'),
  (2, 2, 52, 46, 1.8, '2026-08-31', '旺季偶有延迟'),
  (3, 3, 38, 25, 7.5, '2026-08-31', '长延迟异常'),
  (4, 4, 48, 48, 0.1, '2026-08-31', '准时率高'),
  (5, 5, 12, 6, 8.0, '2026-08-31', '严重履约异常');
"""


def initialize_database(path: str | Path, *, reset: bool = False) -> Path:
    """Create the business schema and idempotently load representative data."""

    resolved = Path(path).resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    if reset and resolved.exists():
        resolved.unlink()
    connection = sqlite3.connect(resolved)
    try:
        try:
            connection.executescript(SCHEMA_SQL)
        except sqlite3.OperationalError as exc:
            columns = {
                str(row[1]) for row in connection.execute("PRAGMA table_info(purchase_requests)")
            }
            if "session_id" not in columns and "session_id" in str(exc):
                connection.execute(
                    "ALTER TABLE purchase_requests ADD COLUMN session_id TEXT NOT NULL DEFAULT ''"
                )
                connection.executescript(SCHEMA_SQL)
            else:
                raise
        columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(purchase_requests)")
        }
        if "session_id" not in columns:
            connection.execute(
                "ALTER TABLE purchase_requests ADD COLUMN session_id TEXT NOT NULL DEFAULT ''"
            )
            connection.executescript(SCHEMA_SQL)
        connection.executescript(SEED_SQL)
        connection.commit()
    finally:
        connection.close()
    return resolved
