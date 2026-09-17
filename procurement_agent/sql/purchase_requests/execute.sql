INSERT INTO purchase_requests(
    request_no, session_id, department_id, product_id, requested_qty, approved_qty,
    required_date, supplier_plan_json, unit_price, total_amount, status,
    approval_status, created_at
) VALUES(
    :request_no, :session_id, :department_id, :product_id, :requested_qty, :approved_qty,
    :required_date, :plan_json, :unit_price, :total_amount, 'approved',
    'approved', :created_at
);
