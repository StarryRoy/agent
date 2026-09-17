# inventory_history

- 表用途：按期间保存产品历史消耗；用于预测需求期间的消耗量。
- `id`：历史记录主键。
- `product_id`：关联 `products.id`；限定要汇总的产品。
- `period`：统计期间，格式为 `YYYY-MM`。
- `consumed_qty`：该期间实际消耗数量，非负整数；计算平均消耗时使用。
