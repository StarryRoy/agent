INSERT OR IGNORE INTO supplier_quality_records(id, supplier_id, inspected_lots, passed_lots, severe_incidents, recorded_at, note) VALUES
  (1, 1, 80, 79, 0, '2026-08-31', '稳定'),
  (2, 2, 65, 62, 0, '2026-08-31', '轻微外观问题'),
  (3, 3, 40, 31, 3, '2026-08-31', '曾出现批次返工与拒收'),
  (4, 4, 72, 72, 0, '2026-08-31', '优秀'),
  (5, 5, 20, 12, 4, '2026-08-31', '已暂停合作');
