# 可见数据表

## ba_ocas_fact_order_cost_hdf

OCAS 订单成本事实表（订单粒度，一行一笔 order_no，全表约 591 万行，order_no 全局唯一无重复）：L1 体验课订单级「收入 × 分摊成本」宽表，覆盖 2025-01-01 至今支付订单，是 UE / 毛利 / 分渠道分团队成本效率分析的主事实表。

## 维度组
- 订单/支付：order_no、user_id、pay_date（支付日期）、real_pay（实付金额）
- 期次：term_id / term_season / term_name 及 term_enroll_start_date / term_start_date / term_renewal_start_date / term_renewal_end_date
- 班型与目标：is_tmp_class（智配占位班）、new_class_tag_name、analysis_class_tag（分析班型）、goal_class_tag（定标班型）、is_target（VARCHAR '1'标内/'0'标外）、sku / finance_sku、user_group、goal_parent_operation_type_name
- 团队与渠道：goal_operation_group（目标运营组，「销售团队」口径用此列）、goal_mkt_group、goal_channel_type、channel_type_name / channel_subtype_name / channel_group_* / channel_id / channel_desc / channel_account（KOL 名）、is_smart_match（VARCHAR）
- 续报与退费结果：renewal_counselor_group_city、renewal_order_no / min_renewal_time（首次续报年课订单）、renewal_gmv（累计续报 GMV）、renewal_lst（末日续报，BIGINT 0/1）、renewal_acc（全局累计是否续报，VARCHAR）、refund_gmv、refund_cnt
- 成本：匿名成本列 c_1 ~ c_26 与 cost_total（均 DECIMAL(20,6)，单位元，订单级分摊值）

## 成本列语义（必须配合字典表）
c_1~c_26 的列注释只有「成本项N」，中文语义在字典表 ba_ocas_fact_cost_columns_hdf（col_id → cost_category）。当前映射：c_1=CT基薪、c_2=CT提成、c_3=JM成本、c_4=ST提成、c_5=其他运营成本、c_6=客服成本、c_7=核桃币成本、c_8=盒子成本、c_9=管理基薪、c_10=管理提成、c_11=线下成本、c_12=线下活动成本、c_13=转介绍激励、c_14=返佣成本、c_15=(未进班)线上成本、c_16=BD成本、c_17=ST基薪、c_18=专项成本、c_19=公益课成本、c_20=其他市场成本、c_21=店铺抽佣成本、c_22=激励提成、c_23=管理激励提成、c_24=发文经费、c_25=店铺抽佣、c_26=市场其他成本。映射可能随字典表构建批次（build_run_id）演进，回答用户前建议实时查一次字典表而不是硬编码。字典表另有 c_27（CT基薪·未带班）/ c_28（实物成本）及 cost_ops_total / cost_market_total / cost_unclassified_total 汇总项，本表暂无对应列。

## 时间口径陷阱（高频误用点）
- 分区字段 dt 数据异常：约 80% 为 NULL、其余为随机小数字符串（如 '3.0722'），**不可用于任何时间过滤或分区裁剪**
- perf_month 注释为「绩效月份」但实际取值仅 '0'/'1'（VARCHAR 标记），**不是月份**，不能按它分月
- 时间过滤唯一可靠字段：pay_date（VARCHAR 'YYYY-MM-DD'，支付口径）或期次日期字段（term_* 口径）

## 典型用法
- 订单 UE / 毛利：收入侧 real_pay + renewal_gmv，成本侧 cost_total（或 c_N 分项），退费侧 refund_gmv；比率类先聚合再相除
- 分渠道/分团队成本效率：按 goal_operation_group / channel_* / analysis_class_tag 分组 sum 成本与收入
- 成本结构拆解：sum(c_N) 各分项占 sum(cost_total) 比例（注意部分订单分项可为 0 或负值冲销）

## 类型约定
日期/时间字段全部 VARCHAR；金额 DECIMAL(20,6)；is_tmp_class / renewal_lst / refund_cnt 为 BIGINT（过滤写 =1 不加引号）；is_smart_match / is_target / renewal_acc 为 VARCHAR（过滤须带引号 '1'），与 ba_eda_l1_v2_info_order_hdf 的 is_smart_match(BIGINT) 口径不同，勿照搬。粒度与 ads_eda_l1_v2_business_regular_df（聚合宽表）不同，成本类诉求走本表，常规经营指标走聚合宽表。

| 字段 | 类型 | 中文名 | 口径 |
|---|---|---|---|
| order_no | VARCHAR | 订单编号 |  |
| user_id | VARCHAR | 用户id |  |
| pay_date | VARCHAR | 订单支付日期 |  |
| real_pay | DECIMAL(20,6) | 订单实付金额 |  |
| term_id | VARCHAR | 学期id |  |
| term_season | VARCHAR | 学季 |  |
| term_name | VARCHAR | 学期名称 |  |
| term_enroll_start_date | VARCHAR | 学期招生开始日期 |  |
| term_start_date | VARCHAR | 学期开始日期 |  |
| term_renewal_start_date | VARCHAR | 学期续报开始日期 |  |
| term_renewal_end_date | VARCHAR | 学期续报结束日期 |  |
| user_class_group_id | VARCHAR | 班级id |  |
| is_tmp_class | BIGINT | 是否智配占位班级:1是0否 |  |
| new_class_tag_name | VARCHAR | 新班级标签名称 |  |
| analysis_class_tag | VARCHAR | 班型(分析用) |  |
| goal_class_tag | VARCHAR | 定标班型 |  |
| is_target | VARCHAR | 是否标内学期:1标内0标外 |  |
| sku | VARCHAR | 定标SKU |  |
| finance_sku | VARCHAR | 财务SKU(财务核算CAC用) |  |
| goal_parent_operation_type_name | VARCHAR | 定标业务线 |  |
| user_group | VARCHAR | 用户群体 |  |
| business_team_tag_id | VARCHAR | 承接团队标签id |  |
| goal_operation_group | VARCHAR | 定标团队 |  |
| goal_mkt_group | VARCHAR | 定标核算组 |  |
| goal_channel_type | VARCHAR | 定标渠道类型 |  |
| channel_type_name | VARCHAR | 渠道类型 |  |
| channel_subtype_name | VARCHAR | 渠道子类型 |  |
| channel_group_id | VARCHAR | 渠道组id |  |
| channel_group_name | VARCHAR | 渠道组名称 |  |
| channel_group_tag_id_names | VARCHAR | 渠道组标签id-name,如:1-0元,2-1元,3-6.8元 |  |
| channel_id | VARCHAR | 子渠道id |  |
| channel_desc | VARCHAR | 子渠道描述 |  |
| channel_account | VARCHAR | 子渠道描述对应的kol名 |  |
| is_smart_match | VARCHAR | 是否智能匹配订单 |  |
| renewal_counselor_group_city | VARCHAR | 续报老师组基地 |  |
| renewal_order_no | VARCHAR | 首次续报订单号(年课订单) |  |
| min_renewal_time | VARCHAR | 首次续报时间(年课订单) |  |
| renewal_gmv | DECIMAL(20,6) | 累计续报gmv |  |
| renewal_lst | BIGINT | 末日续报 |  |
| renewal_acc | VARCHAR | 是否续报(全局累计续报) |  |
| refund_gmv | DECIMAL(20,6) | 退费金额 |  |
| refund_cnt | BIGINT | 退费人数 |  |
| perf_month | VARCHAR | 绩效月份 |  |
| c_1 | DECIMAL(20,6) | 成本项1 |  |
| c_2 | DECIMAL(20,6) | 成本项2 |  |
| c_3 | DECIMAL(20,6) | 成本项3 |  |
| c_4 | DECIMAL(20,6) | 成本项4 |  |
| c_5 | DECIMAL(20,6) | 成本项5 |  |
| c_6 | DECIMAL(20,6) | 成本项6 |  |
| c_7 | DECIMAL(20,6) | 成本项7 |  |
| c_8 | DECIMAL(20,6) | 成本项8 |  |
| c_9 | DECIMAL(20,6) | 成本项9 |  |
| c_10 | DECIMAL(20,6) | 成本项10 |  |
| c_11 | DECIMAL(20,6) | 成本项11 |  |
| c_12 | DECIMAL(20,6) | 成本项12 |  |
| c_13 | DECIMAL(20,6) | 成本项13 |  |
| c_14 | DECIMAL(20,6) | 成本项14 |  |
| c_15 | DECIMAL(20,6) | 成本项15 |  |
| c_16 | DECIMAL(20,6) | 成本项16 |  |
| c_17 | DECIMAL(20,6) | 成本项17 |  |
| c_18 | DECIMAL(20,6) | 成本项18 |  |
| c_19 | DECIMAL(20,6) | 成本项19 |  |
| c_20 | DECIMAL(20,6) | 成本项20 |  |
| c_21 | DECIMAL(20,6) | 成本项21 |  |
| c_22 | DECIMAL(20,6) | 成本项22 |  |
| c_23 | DECIMAL(20,6) | 成本项23 |  |
| c_24 | DECIMAL(20,6) | 成本项24 |  |
| c_25 | DECIMAL(20,6) | 成本项25 |  |
| c_26 | DECIMAL(20,6) | 成本项26 |  |
| cost_total | DECIMAL(20,6) | 总成本 |  |
| dt | VARCHAR | 分区字段 |  |

## ba_ocas_fact_cost_columns_hdf

OCAS 成本列字典表（col_id 粒度，32 行）：解释 ba_ocas_fact_order_cost_hdf 匿名成本列（c_1~c_26、cost_total 等）业务语义的维表。字段：col_id（成本列ID，与事实表列名对应）、cost_side（成本侧）、cost_category（成本类别中文名，**取成本项中文名用这一列**）、label、sort_order、is_governed、build_run_id（构建批次）。

## 字段错位警告（当前数据实况，取数必读）
按列注释 label=标签名称、sort_order=排序序号、is_governed=是否受管控(1是0否)，但当前批次（build_run_id=72）数据实际错位：
- label 列装的是**排序序号**（数字字符串，如 '28'）——排序用 CAST(label AS INT)
- sort_order 列装的是 **0/1 治理标记**（BIGINT）——判断是否受管控项用 sort_order=1
- is_governed 全为 NULL，不可用
若后续批次修复错位，以实时查询结果为准；引用本表前建议先抽样确认列语义。

## 内容构成
- c_1~c_28 共 28 个分项成本 ID（其中 c_27=CT基薪·未带班、c_28=实物成本在事实表中暂无对应列）
- 4 个汇总类 col_id：cost_total（总成本）、cost_ops_total（运营成本）、cost_market_total（市场成本）、cost_unclassified_total（未分类成本），仅 cost_total 在事实表有对应列
- 治理口径：sort_order=1（受管控）的分项为治理内成本，=0 的（c_15~c_23 区段为主）为治理外/专项类

## 典型用法
把事实表 c_N 列 UNPIVOT 成长表后按 col_id JOIN 本表取 cost_category，做成本结构分析；或回答「c_N 是什么」时直查本表。本表为全量小表，无分区，直接全表读即可。

| 字段 | 类型 | 中文名 | 口径 |
|---|---|---|---|
| col_id | VARCHAR | 成本列ID |  |
| cost_side | VARCHAR | 成本侧 |  |
| cost_category | VARCHAR | 成本类别 |  |
| label | VARCHAR | 标签名称 |  |
| sort_order | BIGINT | 排序序号 |  |
| is_governed | BIGINT | 是否受管控:1是0否 |  |
| build_run_id | BIGINT | 构建运行ID |  |

