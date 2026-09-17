CREATE TABLE IF NOT EXISTS purchase_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_no TEXT NOT NULL UNIQUE,
    session_id TEXT NOT NULL DEFAULT '',
    department_id INTEGER NOT NULL REFERENCES departments(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    requested_qty INTEGER NOT NULL CHECK (requested_qty > 0),
    approved_qty INTEGER NOT NULL CHECK (approved_qty > 0),
    required_date TEXT,
    supplier_plan_json TEXT NOT NULL,
    unit_price REAL NOT NULL CHECK (unit_price >= 0),
    total_amount REAL NOT NULL CHECK (total_amount >= 0),
    status TEXT NOT NULL CHECK (status IN ('draft', 'approved', 'ordered', 'cancelled')),
    approval_status TEXT NOT NULL CHECK (approval_status IN ('pending', 'approved', 'rejected')),
    created_at TEXT NOT NULL
);

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
