# SQL 参考示例

以下 SQL 为该角色的取数参考模板，帮助你了解常见场景下的查询写法与口径规范。
**不强制按模板执行**：可直接调用 `run_query` 工具自由编写 SQL；
也可调用 `run_template_sql` 工具直接运行某个模板（传入 template_id 与参数）。

## 年课续报+过程数据·参数化多维聚合 SQL（id: `sql_bam_process_renewal_by_dim`）

```sql
-- ============================================================
-- 年课续报 + 过程数据 · 参数化多维聚合 SQL
-- 底表：ads_bam_annual_process_class_hdf（班级组粒度，一行一个 user_class_group_id）
-- 指标口径引用：
--   [[mq_bam_fstlvl_unlock_cnt]] [[mq_bam_fstlvl_renewal_rate_d11]] [[mq_bam_fstlvl_dense_out_rate]]
--   [[mq_bam_finish_rate_t0]] [[mq_bam_finish_rate_t7]]
--   [[mq_bam_saikao_cover_rate]] [[mq_bam_level_enroll_rate]] [[mq_bam_level_pass_rate]]
-- 用途：按 term × 年课 × 运营类型（科特/非科特）× 学期类型 × 大区/城市/自定义维度分组
-- 输出：续报数据（首课解锁、11 日续报率、密集期调出率/人数）+ 过程数据（T0/T7 完课率）+ 赛考数据（覆盖率/招报率/通过率）
-- 率类均基于汇总分子分母重算，禁止行级率算术平均
-- ============================================================
WITH base AS (
    SELECT *
    FROM ads_bam_annual_process_class_hdf
    WHERE 1=1
      {% if latest_dt_only %}
      AND dt = (SELECT MAX(dt) FROM ads_bam_annual_process_class_hdf)
      {% else %}
      AND dt = '{{ dt }}'
      {% endif %}
      {% if term_name_list %}
      AND term_name IN ({% for v in term_name_list %}'{{ v }}'{% if not loop.last %}, {% endif %}{% endfor %})
      {% endif %}
      {% if annual_course_name_list %}
      AND annual_course_name IN ({% for v in annual_course_name_list %}'{{ v }}'{% if not loop.last %}, {% endif %}{% endfor %})
      {% endif %}
      {% if spec_type_list %}
      AND renewal_technology_spec_type IN ({% for v in spec_type_list %}'{{ v }}'{% if not loop.last %}, {% endif %}{% endfor %})
      {% endif %}
      {% if class_type_list %}
      AND renewal_class_type IN ({% for v in class_type_list %}'{{ v }}'{% if not loop.last %}, {% endif %}{% endfor %})
      {% endif %}
      {% if region_dept_name_list %}
      AND region_dept_name IN ({% for v in region_dept_name_list %}'{{ v }}'{% if not loop.last %}, {% endif %}{% endfor %})
      {% endif %}
      {% if base_dept_city_list %}
      AND base_dept_city IN ({% for v in base_dept_city_list %}'{{ v }}'{% if not loop.last %}, {% endif %}{% endfor %})
      {% endif %}
)
SELECT
    {% for c in group_by_cols %}{{ c }}{% if not loop.last %}, {% endif %}{% endfor %}
    -- ---- 续报数据 ----
    , SUM(fstlvl_unlock_cnt)                                                     AS 首课解锁人数
    , SUM(renewal_user_cnt)                                                      AS 续报开始时在班人数
    , SUM(user_renewal_day11)                                                    AS day11续报人数
    , SUM(user_renewal_day11) * 1.0 / NULLIF(SUM(fstlvl_unlock_cnt), 0)          AS "11日首课续报率"
    , 1 - SUM(renewal_user_cnt) * 1.0 / NULLIF(SUM(fstlvl_unlock_cnt), 0)        AS "首课-密集期调出率"
    , SUM(fstlvl_unlock_cnt) - SUM(renewal_user_cnt)                             AS "首课-密集期调出人数"
    -- ---- 过程数据（完课） ----
    , SUM(unlock_cnt)                                                            AS 解锁人次
    , SUM(day0_finish_cnt) * 1.0 / NULLIF(SUM(unlock_cnt), 0)                    AS T0完课率
    , SUM(day8_finish_cnt) * 1.0 / NULLIF(SUM(unlock_cnt), 0)                    AS T7完课率
    -- ---- 赛考数据 ----
    , SUM(saikao_enroll_cnt) * 1.0 / NULLIF(SUM(renewal_user_cnt), 0)            AS 赛考覆盖率
    , SUM(level_enroll_cnt)  * 1.0 / NULLIF(SUM(renewal_user_cnt), 0)            AS 赛考招报率
    , SUM(level_result_cnt)  * 1.0 / NULLIF(SUM(level_enroll_cnt), 0)            AS 赛考招报通过率
FROM base
GROUP BY {% for c in group_by_cols %}{{ c }}{% if not loop.last %}, {% endif %}{% endfor %}
ORDER BY 首课解锁人数 DESC

```
  参数：`:latest_dt_only`（是否仅取最新 dt 分区。默认 true（防多分区重复计数）；置 false 时需给出 dt。）, `:dt`（指定 dt 分区（VARCHAR，如 '20260824'）。仅在 latest_dt_only=false 时生效。）, `:term_name_list`（期次名筛选，如 ['2026寒01期','2025秋08期']。空数组表示不限。）, `:annual_course_name_list`（年课名筛选，如 ['趣味C1']。空数组表示不限。可选值示例：A01 / C1 / Python1 / S低 / 趣味C1（按期次实际存在的值为准）。）, `:spec_type_list`（运营类型（renewal_technology_spec_type）筛选，可选：'科特' / '非科特'。空数组表示不限。）, `:class_type_list`（学期类型（renewal_class_type）筛选。空数组表示不限。）, `:region_dept_name_list`（大区（基地）名筛选，如 ['独立四纵队','两湖区域化']。空数组表示不限。）, `:base_dept_city_list`（基地部门所在城市筛选。与 region_dept_name_list 联用可精确到 (大区, 城市) 组合。空数组表示不限。）, `:group_by_cols`（分组维度字段名列表。常用：region_dept_name（基地/大区）、base_dept_city（城市）、xuebu_name（学部）、counselor_group_name（班主任组）、counselor_id/counselor_name（班主任）、term_name、annual_course_name、renewal_technology_spec_type。）
