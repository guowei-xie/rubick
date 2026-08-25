# SQL 参考示例

以下 SQL 为该角色的取数参考模板，帮助你了解常见场景下的查询写法与口径规范。
**不强制按模板执行**：可直接调用 `run_query` 工具自由编写 SQL；
也可调用 `run_template_sql` 工具直接运行某个模板（传入 template_id 与参数）。

## 体验课市场侧追标 SQL（计划招生学期口径）（id: `sql_l1_goal_tracking_market`）

```sql
-- ============================================================
-- 口径：计划招生学期（市场侧追标）
--   * term_name 取自 l.intend_term_name（计划入班学期）
--   * 含全部目标组合（不按 is_target 过滤）
--   * 与 sql_l1_goal_tracking_ops 互补：后者按实际进班学期 + is_target=1
-- 参数化：
--   * term_year_prefix_list (list[str], 默认 ['2026寒','2026春','2026暑','2026秋','2027寒'])
--     控制 LEFT(l.intend_term_name, 5) IN (...) 的学期前缀范围
--     默认值保留现有当年自然年完整周期，零行为变化；按需传入 ['2025春','2026春'] 做同比
--     占位语法采用 Jinja2；运营 apply 前按平台 sql_template 引擎语法等价替换
-- ============================================================
WITH l1v2_info AS (
    SELECT
        l.intend_term_name AS term_name,
        l.goal_mkt_group,
        l.goal_channel_type,
        l.sku,
        a.analysis_class_channel_tag,
        SUM(l.enroll) AS enroll,
        SUM(l.renewal_lst) AS renewal_lst,
        SUM(l.inrenewal_renewal_gmv) AS inrenewal_renewal_gmv
    FROM ads_eda_l1_v2_business_regular_df l
    LEFT JOIN dim_analysis_class_channel_df a
        ON l.analysis_class_tag = a.analysis_class_tag
        AND l.goal_mkt_group = a.goal_mkt_group
        AND l.goal_channel_type = a.goal_channel_type
        AND l.sku = a.sku
        AND a.dt = strftime(CURRENT_DATE - INTERVAL 1 DAY, '%Y%m%d')
    WHERE
        l.dt = strftime(CURRENT_DATE - INTERVAL 1 DAY, '%Y%m%d')
        AND (l.goal_class_tag NOT LIKE '%硬件体验课%' AND l.goal_class_tag NOT LIKE '%新机器人体验课%' AND l.goal_class_tag NOT LIKE '%电销召回%' AND l.goal_class_tag NOT LIKE '%其它测试%')
        AND LEFT(l.intend_term_name, 5) IN ({% for p in term_year_prefix_list %}'{{ p }}'{% if not loop.last %}, {% endif %}{% endfor %})
    GROUP BY
        l.intend_term_name,
        l.goal_mkt_group,
        l.goal_channel_type,
        l.sku,
        a.analysis_class_channel_tag
),
goal_info AS (
    SELECT
        term_name,
        analysis_class_channel_tag,
        sku,
        goal_mkt_group,
        goal_channel_type,
        SUM(enroll_target) AS enroll_target,
        SUM(renewal_target) AS renewal_target,
        SUM(gmv_target) AS gmv_target
    FROM man_l1_s_goal_small_detail
    GROUP BY
        term_name,
        analysis_class_channel_tag,
        sku,
        goal_mkt_group,
        goal_channel_type
)
SELECT
    COALESCE(l.term_name, g.term_name) AS term_name,
    COALESCE(l.analysis_class_channel_tag, g.analysis_class_channel_tag) AS analysis_class_channel_tag,
    COALESCE(l.sku, g.sku) AS sku,
    COALESCE(l.goal_mkt_group, g.goal_mkt_group) AS goal_mkt_group,
    COALESCE(l.goal_channel_type, g.goal_channel_type) AS goal_channel_type,
    l.enroll,
    l.renewal_lst,
    l.inrenewal_renewal_gmv,
    g.enroll_target,
    g.renewal_target,
    g.gmv_target
FROM l1v2_info l
LEFT JOIN goal_info g
    ON l.term_name = g.term_name
    AND l.analysis_class_channel_tag = g.analysis_class_channel_tag
    AND l.sku = g.sku
    AND l.goal_mkt_group = g.goal_mkt_group
    AND l.goal_channel_type = g.goal_channel_type

UNION ALL

SELECT
    COALESCE(l.term_name, g.term_name) AS term_name,
    COALESCE(l.analysis_class_channel_tag, g.analysis_class_channel_tag) AS analysis_class_channel_tag,
    COALESCE(l.sku, g.sku) AS sku,
    COALESCE(l.goal_mkt_group, g.goal_mkt_group) AS goal_mkt_group,
    COALESCE(l.goal_channel_type, g.goal_channel_type) AS goal_channel_type,
    l.enroll,
    l.renewal_lst,
    l.inrenewal_renewal_gmv,
    g.enroll_target,
    g.renewal_target,
    g.gmv_target
FROM l1v2_info l
RIGHT JOIN goal_info g
    ON l.term_name = g.term_name
    AND l.analysis_class_channel_tag = g.analysis_class_channel_tag
    AND l.sku = g.sku
    AND l.goal_mkt_group = g.goal_mkt_group
    AND l.goal_channel_type = g.goal_channel_type
WHERE l.term_name IS NULL
```
  参数：`:term_year_prefix_list`（学期前缀列表（如 '2026春'），用于 LEFT(l.intend_term_name, 5) IN (...) 范围；默认值保留当前自然年完整周期，按需传入 ['2025春','2026春'] 做同比。）

## 体验课运营侧追标 SQL（实际进班学期口径, is_target=1）（id: `sql_l1_goal_tracking_ops`）

```sql
-- ============================================================
-- 口径：实际进班学期（运营侧追标）
--   * term_name 取自 l.term（实际进班学期）
--   * 仅保留 is_target=1 的目标组合
--   * 与 sql_l1_goal_tracking_market 互补：后者按计划招生学期、含全部组合
-- 参数化：
--   * term_year_prefix_list (list[str], 默认 ['2026春'])
--     控制 LEFT(l.term, 5) IN (...) 的学期前缀范围；支持同比/跨年对比
--     占位语法采用 Jinja2；运营 apply 前按平台 sql_template 引擎语法等价替换
-- ============================================================
WITH l1v2_info AS (
    SELECT
        l.term AS term_name,
        l.goal_mkt_group,
        l.goal_channel_type,
        l.sku,
        l.analysis_class_tag,
        a.analysis_class_channel_tag,
        SUM(l.enroll) AS enroll,
        SUM(l.renewal_lst) AS renewal_lst,
        SUM(l.inrenewal_renewal_gmv) AS inrenewal_renewal_gmv
    FROM ads_eda_l1_v2_business_regular_df l
    LEFT JOIN dim_analysis_class_channel_df a
        ON l.analysis_class_tag = a.analysis_class_tag
        AND l.goal_mkt_group = a.goal_mkt_group
        AND l.goal_channel_type = a.goal_channel_type
        AND l.sku = a.sku
        AND a.dt = strftime(CURRENT_DATE - INTERVAL 1 DAY, '%Y%m%d')
    WHERE
        l.dt = strftime(CURRENT_DATE - INTERVAL 1 DAY, '%Y%m%d')
        AND l.goal_class_tag NOT IN ('硬件体验课','新机器人体验课')
        AND l.is_target = '1'
        AND LEFT(l.term, 5) IN ({% for p in term_year_prefix_list %}'{{ p }}'{% if not loop.last %}, {% endif %}{% endfor %})
    GROUP BY
        l.term,
        l.goal_mkt_group,
        l.goal_channel_type,
        l.sku,
        l.analysis_class_tag,
        a.analysis_class_channel_tag
),
goal_info AS (
    SELECT
        term_name,
        analysis_class_channel_tag,
        sku,
        goal_mkt_group,
        goal_channel_type,
        SUM(enroll_target) AS enroll_target,
        SUM(renewal_target) AS renewal_target,
        SUM(gmv_target) AS gmv_target
    FROM man_l1_s_goal_small_detail
    GROUP BY
        term_name,
        analysis_class_channel_tag,
        sku,
        goal_mkt_group,
        goal_channel_type
)
SELECT
    COALESCE(l.term_name, g.term_name) AS term_name,
    COALESCE(l.analysis_class_channel_tag, g.analysis_class_channel_tag) AS analysis_class_channel_tag,
    l.analysis_class_tag,
    COALESCE(l.sku, g.sku) AS sku,
    COALESCE(l.goal_mkt_group, g.goal_mkt_group) AS goal_mkt_group,
    COALESCE(l.goal_channel_type, g.goal_channel_type) AS goal_channel_type,
    l.enroll,
    l.renewal_lst,
    l.inrenewal_renewal_gmv,
    g.enroll_target,
    g.renewal_target,
    g.gmv_target
FROM l1v2_info l
LEFT JOIN goal_info g
    ON l.term_name = g.term_name
    AND l.analysis_class_channel_tag = g.analysis_class_channel_tag
    AND l.sku = g.sku
    AND l.goal_mkt_group = g.goal_mkt_group
    AND l.goal_channel_type = g.goal_channel_type

UNION ALL

SELECT
    COALESCE(l.term_name, g.term_name) AS term_name,
    COALESCE(l.analysis_class_channel_tag, g.analysis_class_channel_tag) AS analysis_class_channel_tag,
    l.analysis_class_tag,
    COALESCE(l.sku, g.sku) AS sku,
    COALESCE(l.goal_mkt_group, g.goal_mkt_group) AS goal_mkt_group,
    COALESCE(l.goal_channel_type, g.goal_channel_type) AS goal_channel_type,
    l.enroll,
    l.renewal_lst,
    l.inrenewal_renewal_gmv,
    g.enroll_target,
    g.renewal_target,
    g.gmv_target
FROM l1v2_info l
RIGHT JOIN goal_info g
    ON l.term_name = g.term_name
    AND l.analysis_class_channel_tag = g.analysis_class_channel_tag
    AND l.sku = g.sku
    AND l.goal_mkt_group = g.goal_mkt_group
    AND l.goal_channel_type = g.goal_channel_type
WHERE l.term_name IS NULL
```
  参数：`:term_year_prefix_list`（学期前缀列表（如 '2026春'），用于 LEFT(l.term, 5) IN (...) 范围；支持同比/跨年；建议最大 4 个学期，避免一次扫描跨过多分区。）

## 市场实际宽表派生 SQL（订单粒度→经营聚合）（id: `sql_l1_market_actual`）

```sql
select
    user_group as "用户群体"
    ,case
        when regexp_matches(goal_class_tag, '占位') or (is_inclass = 0 and is_smart_match = 1) then '智配'
        else goal_parent_operation_type_name
    end as "业务线"
    ,sku
    ,case
        when regexp_matches(goal_class_tag, '占位') or (is_inclass = 0 and is_smart_match = 1) then '智配未进班'
        when regexp_matches(goal_class_tag, '应用商店') then '应用商店单独运营'
        else goal_class_tag
    end as "定标班型"
    ,case
        when analysis_class_channel_tag in ('思维召回', '科特召回') then '召回'
        when analysis_class_channel_tag in ('硬件课') then '硬件体验课'
        when analysis_class_channel_tag in ('思维转介绍', '科特转介绍') then '转介绍'
        when analysis_class_channel_tag in ('思维转介绍MOT') then '转介绍MOT'
        when regexp_matches(analysis_class_channel_tag, '0元4.5-应用商店') then '0元4.5'
        when regexp_matches(goal_channel_type, '信息流-4.5') and analysis_class_channel_tag = '9.9常规' then '0元4.5'
        when regexp_matches(goal_channel_type, '进校-4.5') and analysis_class_channel_tag = '0元4.5' then '0元常规'
        else analysis_class_channel_tag
    end as "大班型_渠道来源"
    ,case when goal_mkt_group = '创新增长' then '内容营销' else COALESCE(goal_mkt_group, '其它') end as "业务核算组"
    ,goal_channel_type as "定标渠道类型"
    ,COALESCE(goal_operation_group, '0元团队') as "运营团队"
    ,intend_term_name as "学期期次"
    ,COALESCE(term_name, intend_term_name) as "运营学期期次"
    ,is_smart_match as "是否智配原始流量"
    ,pay_date as "支付日期"
    ,channel_group_name as "渠道组名称"
    ,new_class_tag_name as "班级标签名称"
    ,pay_grade as "支付年级"
    ,city_level as "城市线级"
    ,count(distinct order_no) as "实际招生量"
    ,count(distinct case when is_inclass = 0 or regexp_matches(goal_class_tag, '占位') then order_no else null end) as "实际未进班量"
    ,count(distinct order_no) - count(distinct case when is_inclass = 0 or regexp_matches(goal_class_tag, '占位') then order_no else null end) as "实际进班量"
    ,sum(renewal_t0) as "首日续报量"
    ,sum(renewal_lst) as "期内续报量"
    ,sum(renewal_acc) as "累计续报量"
    ,sum(inrenewal_renewal_gmv) as "期内gmv"
    ,sum(renewal_gmv) as "累计gmv"
    ,sum(wx_add) as "微信添加量"
    ,sum(attend1_t6) as "首课到课量"
    ,sum(finish1_t6) as "首课完课量"
    ,sum(attend4_t6) as "4课到课量"
    ,sum(finish4_t6) as "4课完课量"
    ,sum(wx_add) / count(distinct order_no) as "微信添加率"
    ,sum(attend1_t6) / count(distinct order_no) as "首课到课率"
    ,sum(finish1_t6) / count(distinct order_no) as "首课完课率"
    ,sum(attend4_t6) / count(distinct order_no) as "4课到课率"
    ,sum(finish4_t6) / count(distinct order_no) as "4课完课率"
    ,case when sum(finish4_t6) = 0 then 0 else sum(renewal_lst) / sum(finish4_t6) end as "完课转化率"
from ba_eda_l1_v2_info_order_hdf
where dt = strftime(CURRENT_DATE - INTERVAL 1 DAY, '%Y%m%d')
    and regexp_matches(intend_term_name, '2025(寒|春|暑|秋)|2026(寒|春|暑|秋)|2027寒')
    and not regexp_matches(goal_class_tag, '电销召回|其它测试|硬件体验课')
    and not regexp_matches(intend_term_name, '2026春20期')
group by all
```

## L1 多维指标看板探查 SQL（参数化多维聚合）（id: `sql_l1_multidim_indicator_explore`）

```sql
-- ============================================================
-- L1 多维指标看板探查 SQL
-- 对应 R Shiny ads_l1_eda_multidimensional_indicators_df_tab
-- 行/订单粒度宽表：ads_eda_l1_v2_multidimensional_indicators_df
-- 字段词典 / 默认锚点：[[mth_l1_multidim_indicator_dictionary]]
-- 率公式口径：[[mth_l1_eda_rate_formulas]]
-- ============================================================
WITH base AS (
    SELECT *
    FROM ads_eda_l1_v2_multidimensional_indicators_df
    WHERE 1=1
      {% if latest_dt_only %}
      AND dt = (SELECT MAX(dt) FROM ads_eda_l1_v2_multidimensional_indicators_df)
      {% endif %}
      AND {{ date_col }} >= '{{ start_date }}'
      AND {{ date_col }} <= '{{ end_date }}'
      AND user_group IN ({% for v in user_group_list %}'{{ v }}'{% if not loop.last %}, {% endif %}{% endfor %})
      AND business_line IN ({% for v in business_line_list %}'{{ v }}'{% if not loop.last %}, {% endif %}{% endfor %})
      AND goal_operation_group IN ({% for v in goal_operation_group_list %}'{{ v }}'{% if not loop.last %}, {% endif %}{% endfor %})
      AND is_target IN ({% for v in is_target_list %}'{{ v }}'{% if not loop.last %}, {% endif %}{% endfor %})
      {% if sku_filter %}
      AND sku IN ({% for v in sku_filter %}'{{ v }}'{% if not loop.last %}, {% endif %}{% endfor %})
      {% endif %}
      {% if goal_class_tag_filter %}
      AND goal_class_tag IN ({% for v in goal_class_tag_filter %}'{{ v }}'{% if not loop.last %}, {% endif %}{% endfor %})
      {% endif %}
)
SELECT
    {% for c in group_by_cols %}{{ c }}{% if not loop.last %}, {% endif %}{% endfor %}
    -- ---- 量指标 ----
    , COUNT(DISTINCT renewal_staff_id)              AS ct数
    , SUM(enroll)                                   AS 招生人数
    , SUM(wx_add)                                   AS 加微人数_累计
    , SUM(wx_add_t3)                                AS 加微人数_t3
    , SUM(wx_add_ahead_unlocked1)                   AS 加微人数_首课前
    , SUM(attend1_t6)                               AS 到1课人数_t6
    , SUM(attend_last_t6)                           AS 到末课人数_t6
    , SUM(finish1_t6)                               AS 完1课人数_t6
    , SUM(finish_last_t6)                           AS 完末课人数_t6
    , SUM(renewal_acc)                              AS 累计续报人数
    , SUM(renewal_t0)                               AS 首日续报人数
    , SUM(renewal_lst)                              AS 末日续报人数
    , SUM(inrenewal_renewal_gmv)                    AS 期内gmv
    , SUM(renewal_gmv)                              AS 累计gmv
    , SUM(refund_cnt)                               AS 年课退费人数
    , SUM(refund_before_fullvl_cnt)                 AS 年课全额期退费人数
    , SUM(refund_gmv)                               AS 年课退费gmv
    , SUM(direct_sale_cnt)                          AS 直售人数
    , SUM(class_teacher_hit_order_cnt)              AS 撞单人数
    -- ---- 率指标（先聚合后相除；口径 mth_l1_eda_rate_formulas） ----
    , SUM(wx_add)                  * 1.0 / NULLIF(SUM(enroll), 0)         AS 加微率
    , SUM(wx_add_t3)               * 1.0 / NULLIF(SUM(enroll), 0)         AS 三日内加微率
    , SUM(wx_add_ahead_unlocked1)  * 1.0 / NULLIF(SUM(enroll), 0)         AS 首课前加微率
    , SUM(attend1_t6)              * 1.0 / NULLIF(SUM(enroll), 0)         AS 首到率
    , SUM(attend1_t6)              * 1.0 / NULLIF(SUM(wx_add), 0)         AS 加微首到率
    , SUM(finish_last_t6)          * 1.0 / NULLIF(SUM(attend1_t6), 0)     AS 留存率
    , SUM(finish_last_t6)          * 1.0 / NULLIF(SUM(enroll), 0)         AS 末课完课率
    , SUM(renewal_acc)             * 1.0 / NULLIF(SUM(finish_last_t6), 0) AS 累计完转率
    , SUM(renewal_acc)             * 1.0 / NULLIF(SUM(enroll), 0)         AS 累计续报率
    , SUM(renewal_t0)              * 1.0 / NULLIF(SUM(enroll), 0)         AS 首日续报率
    , SUM(renewal_lst)             * 1.0 / NULLIF(SUM(enroll), 0)         AS 末日续报率
    , SUM(refund_cnt)              * 1.0 / NULLIF(SUM(renewal_acc), 0)    AS 退费率
    , SUM(refund_before_fullvl_cnt)* 1.0 / NULLIF(SUM(renewal_acc), 0)    AS 全额期退费率
    , SUM(direct_sale_cnt)         * 1.0 / NULLIF(SUM(renewal_lst), 0)    AS 直售占比
    , SUM(class_teacher_hit_order_cnt)*1.0 / NULLIF(SUM(renewal_lst), 0)  AS 撞单占比
FROM base
GROUP BY {% for c in group_by_cols %}{{ c }}{% if not loop.last %}, {% endif %}{% endfor %}
ORDER BY {% for c in group_by_cols %}{{ c }}{% if not loop.last %}, {% endif %}{% endfor %}

```
  参数：`:date_col`（时间口径轴；可选 'pay_date' / 'term_start_date' / 'term_renewal_start_date' / 'term_renewal_end_date'。默认 term_start_date（与 [[mth_l1_multidim_indicator_dictionary]] §五一致）。）, `:start_date`（时间窗起始日期（YYYY-MM-DD）。）, `:end_date`（时间窗结束日期（YYYY-MM-DD）。）, `:user_group_list`（用户群体筛选；默认 ['思维']。）, `:business_line_list`（业务线筛选；默认 ['9.9']。）, `:goal_operation_group_list`（定标团队（销售团队）筛选；默认 ['9元团队']。）, `:is_target_list`（标内/标外学期；默认 ['1','0']（两个都选）。）, `:sku_filter`（SKU 过滤；空数组表示不限（等价 R 看板的 '全部'）。）, `:goal_class_tag_filter`（定标班型过滤；空数组表示不限。）, `:group_by_cols`（交叉维度字段名列表；可选字段见 [[mth_l1_multidim_indicator_dictionary]] §一。）, `:latest_dt_only`（是否仅取最新 dt 分区（防多分区重复计数）；默认 true。）

## 运营实际宽表派生 SQL（订单粒度→运营聚合，实际进班学期口径 is_target=1）（id: `sql_l1_operations_actual`）

```sql
select
    user_group as "用户群体"
    ,case
        when regexp_matches(goal_class_tag, '占位') or (is_inclass = 0 and is_smart_match = 1) then '智配'
        else goal_parent_operation_type_name
    end as "业务线"
    ,sku
    ,case
        when regexp_matches(goal_class_tag, '占位') or (is_inclass = 0 and is_smart_match = 1) then '智配未进班'
        when regexp_matches(goal_class_tag, '应用商店') then '应用商店单独运营'
        else goal_class_tag
    end as "定标班型"
    ,case
        when analysis_class_channel_tag in ('思维召回', '科特召回') then '召回'
        when analysis_class_channel_tag in ('思维转介绍', '科特转介绍') then '转介绍'
        when analysis_class_channel_tag in ('思维转介绍MOT') then '转介绍MOT'
        when regexp_matches(analysis_class_channel_tag, '0元4.5-应用商店') then '0元4.5'
        else analysis_class_channel_tag
    end as "大班型_渠道来源"
    ,case when goal_mkt_group = '创新增长' then '内容营销' else COALESCE(goal_mkt_group, '其它') end as "业务核算组"
    ,goal_channel_type as "定标渠道类型"
    ,case
        when regexp_matches(goal_class_tag, '占位') or (is_inclass = 0 and is_smart_match = 1) then '未分配团队'
        else COALESCE(goal_operation_group, '0元团队')
    end as "运营团队"
    ,intend_term_name as "学期期次"
    ,COALESCE(term_name, intend_term_name) as "运营学期期次"
    ,is_smart_match as "是否智配原始流量"
    ,renewal_staff_id as "续报老师id"
    ,term_id
    ,counselor_id
    ,pay_date as "支付日期"
    ,new_class_tag_name as "班级标签名称"
    ,term_start_date as "开课日期"
    ,count(distinct order_no) as "实际招生量"
    ,count(distinct case when is_inclass = 0 or regexp_matches(goal_class_tag, '占位') then order_no else null end) as "实际未进班量"
    ,count(distinct order_no) - count(distinct case when is_inclass = 0 or regexp_matches(goal_class_tag, '占位') then order_no else null end) as "实际进班量"
    ,sum(renewal_t0) as "首日续报量"
    ,sum(renewal_lst) as "期内续报量"
    ,sum(renewal_acc) as "累计续报量"
    ,sum(inrenewal_renewal_gmv) as "期内gmv"
    ,sum(renewal_gmv) as "累计gmv"
    ,sum(wx_add) as "微信添加量"
    ,sum(attend1_t6) as "首课到课量"
    ,sum(finish1_t6) as "首课完课量"
    ,sum(attend4_t6) as "4课到课量"
    ,sum(finish4_t6) as "4课完课量"
    ,sum(wx_add) / count(distinct order_no) as "微信添加率"
    ,sum(attend1_t6) / count(distinct order_no) as "首课到课率"
    ,sum(finish1_t6) / count(distinct order_no) as "首课完课率"
    ,sum(attend4_t6) / count(distinct order_no) as "4课到课率"
    ,sum(finish4_t6) / count(distinct order_no) as "4课完课率"
    ,case when sum(finish4_t6) = 0 then 0 else sum(renewal_lst) / sum(finish4_t6) end as "完课转化率"
from ba_eda_l1_v2_info_order_hdf
where dt = strftime(CURRENT_DATE - INTERVAL 1 DAY, '%Y%m%d')
    and regexp_matches(COALESCE(term_name, intend_term_name), '2025(寒|春|暑|秋)|2026(寒|春|暑|秋)|2027寒')
    and not regexp_matches(COALESCE(intend_term_name, ''), '2026春20期')
    and is_target = 1
    and not regexp_matches(analysis_class_tag, '其它|其它测试|硬件课|一段')
group by all

```

## 运营销售带班角色派生 SQL（续报老师×运营学期→新人期数/带班类型）（id: `sql_l1_operations_sales_role`）

```sql
select
    c.counselor_id
    ,c.renewal_staff_id
    ,c.term as "运营学期期次"
    ,c.term_start_date as "开课日期"
    ,c.user_group as "用户群体"
    ,c.goal_parent_operation_type_name as "业务线"
    ,c.goal_class_tag as "定标班型"
    ,c.sku
    ,c.sales_rk
    ,case
        when c.sales_rk = 1 then '新人1期'
        when c.sales_rk = 2 then '新人2期'
        when c.sales_rk = 3 then '新人3期'
        when c.sales_rk >= 4 then '成熟骨干'
        else '其它'
    end as "销售人员类型"
    ,case
        when c.sales_rk = 1 then '首次带班'
        when c.user_group = c.pre_user_group and c.goal_parent_operation_type_name = c.pre_goal_parent_operation_type_name and c.goal_class_tag = c.pre_goal_class_tag and c.sku = c.pre_sku then '持续带班'
        when c.user_group = c.pre_user_group and c.goal_parent_operation_type_name = c.pre_goal_parent_operation_type_name and c.goal_class_tag = c.pre_goal_class_tag and c.sku <> c.pre_sku then '仅换品类'
        when (c.user_group <> c.pre_user_group or c.goal_parent_operation_type_name <> c.pre_goal_parent_operation_type_name or c.goal_class_tag <> c.pre_goal_class_tag) and c.sku = c.pre_sku then '仅换班型'
        when (c.user_group <> c.pre_user_group or c.goal_parent_operation_type_name <> c.pre_goal_parent_operation_type_name or c.goal_class_tag <> c.pre_goal_class_tag) and c.sku <> c.pre_sku then '班品双换'
        else '其它'
    end as "销售带班类型"
from
(
    select
        b.counselor_id
        ,b.renewal_staff_id
        ,b.term
        ,b.term_start_date
        ,b.user_group
        ,b.goal_parent_operation_type_name
        ,b.goal_class_tag
        ,b.sku
        ,row_number() over (partition by renewal_staff_id order by term_start_date) as sales_rk
        ,lag(user_group, 1, null) over (partition by renewal_staff_id order by term_start_date) as pre_user_group
        ,lag(goal_parent_operation_type_name, 1, null) over (partition by renewal_staff_id order by term_start_date) as pre_goal_parent_operation_type_name
        ,lag(goal_class_tag, 1, null) over (partition by renewal_staff_id order by term_start_date) as pre_goal_class_tag
        ,lag(sku, 1, null) over (partition by renewal_staff_id order by term_start_date) as pre_sku
    from
    (
        select
            a.*
            ,row_number() over (partition by renewal_staff_id, term order by sales_user_cnt desc) as rk
        from
        (
            select
                counselor_id
                ,renewal_staff_id
                ,COALESCE(term_name, intend_term_name) as term
                ,term_start_date
                ,user_group
                ,goal_parent_operation_type_name
                ,goal_class_tag
                ,sku
                ,count(distinct order_no) as sales_user_cnt
            from ba_eda_l1_v2_info_order_hdf
            where dt = strftime(CURRENT_DATE - INTERVAL 1 DAY, '%Y%m%d')
                and regexp_matches(COALESCE(term_name, intend_term_name), '2025(寒|春|暑|秋)|2026(寒|春|暑|秋)|2027寒')
                and not regexp_matches(COALESCE(intend_term_name, ''), '2026春20期')
                and not regexp_matches(goal_class_tag, '电销召回|蜀都新兵营')
            group by all
        ) a
    ) b
    where b.rk = 1
) c

```
