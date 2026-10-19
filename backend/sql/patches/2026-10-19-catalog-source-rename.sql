-- 存量归一: cat_datasource.source 从历史值 'datasage' 改为 'datacatalog'
-- 背景: 物理目录同步来源改名（DataSage -> DataCatalog）。新写入行已是 'datacatalog'，
--       本补丁只处理存量行，避免按 source 统计/筛选时同时出现两种值。
-- 幂等: 可重复执行；仅影响 source 列，不动 external_id 与同步时间。
-- 新库无需执行（种子与默认值均为 'datacatalog'）。

UPDATE cat_datasource
SET source = 'datacatalog'
WHERE source = 'datasage';

-- ↓↓↓ 回滚（谨慎）↓↓↓
-- 回滚会把所有同步来源行（含更名后新同步的行）改回旧值，仅在整体回退版本时执行。
-- UPDATE cat_datasource SET source = 'datasage' WHERE source = 'datacatalog' AND external_id IS NOT NULL;
