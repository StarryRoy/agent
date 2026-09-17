INSERT OR IGNORE INTO purchase_history(id, supplier_id, product_id, quantity, unit_price, ordered_at, promised_delivery_days, actual_delivery_days, quality_result) VALUES
  (1, 1, 1, 420, 1450, '2026-02-15', 20, 19, 'passed'),
  (2, 1, 1, 300, 1460, '2026-05-20', 18, 18, 'passed'),
  (3, 2, 1, 260, 1390, '2026-04-10', 21, 25, 'passed_with_minor_issues'),
  (4, 2, 1, 280, 1400, '2026-07-08', 22, 23, 'passed'),
  (5, 3, 1, 500, 1250, '2026-01-12', 30, 44, 'rejected'),
  (6, 3, 1, 300, 1280, '2026-06-18', 32, 40, 'passed_with_rework'),
  (7, 4, 1, 200, 1680, '2026-03-11', 14, 13, 'passed');
