INSERT OR IGNORE INTO supplier_delivery_records(id, supplier_id, deliveries, on_time_deliveries, average_delay_days, recorded_at, note) VALUES
  (1, 1, 60, 58, 0.4, '2026-08-31', '交付稳定'),
  (2, 2, 52, 46, 1.8, '2026-08-31', '旺季偶有延迟'),
  (3, 3, 38, 25, 7.5, '2026-08-31', '长延迟异常'),
  (4, 4, 48, 48, 0.1, '2026-08-31', '准时率高'),
  (5, 5, 12, 6, 8.0, '2026-08-31', '严重履约异常');
