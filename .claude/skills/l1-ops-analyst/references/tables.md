# 可见数据表

## ads_eda_l1_v2_business_regular_df

L1 体验课业务宽表 v2：按 term × sku × 渠道 × 分析班维度聚合，含招生 / 进班 / 续报 / GMV / 退费 / 智配 / 邀请等核心经营指标。日期字段为 VARCHAR（如 dt 分区、term_*_date），金额字段为 DECIMAL(20,6)。L1 经营分析的主事实表。

## 业务口径与同义词映射（l1-ops-analyst 场景默认）

### 班型与项目（智配相关）
- 「智配低人效班型」= 同时满足：`is_smart_match = '1'` AND `analysis_class_tag = '智配项目'` AND `goal_class_tag = '智配低人效单独运营'`
- 仅说「智配项目 / 智配班型」未提『低人效』时，按 `is_smart_match = '1'` AND `analysis_class_tag = '智配项目'` 处理，不强制 goal_class_tag 限制
- `is_smart_match` 为 VARCHAR `'1'/'0'`（不是 BIGINT），过滤时务必带引号

### 销售团队 / 运营组
- 「销售团队 / 团队」字段 → `goal_operation_group`（中文名『目标运营组』）
- 不要使用 `goal_mkt_group`（市场组）或 `channel_group_name`（渠道组）作为销售团队字段

### 招生量与续报口径
- 「招生量 / 实际招生量 / 进班数 / 进班人数」 → `sum(enroll)`
- 「期内续报量 / 末日续报量」 → `sum(renewal_lst)`
- 「期内续报率（主用）」= `sum(renewal_lst) / sum(enroll)`，**必须先聚合再相除**；不能对行级 `renewal_lst/enroll` 再做平均
- 涉及『最近 N 期续报率』时默认遵循 [[mth_l1_recent_n_terms_default_filter]] 的期次过滤；含未成熟期次时改走 [[mth_l1_recent_n_terms_maturity_hint]] 的观察口径

### 渠道字段消歧
- 「大班型_渠道来源」**不在本表**，需 JOIN `dim_analysis_class_channel_df` 取 `analysis_class_channel_tag`
- `channel_type_name`、`channel_group_name`、`goal_class_tag` 不能当作「大班型_渠道来源」使用

| 字段 | 类型 | 中文名 | 口径 |
|---|---|---|---|
| term | VARCHAR | 学期名称 |  |
| term_id | BIGINT | 学期id |  |
| pay_date | VARCHAR | 支付日期 |  |
| term_enroll_start_date | VARCHAR | 学期开始招生日期 |  |
| term_enroll_end_date | VARCHAR | 学期招生结束日期 |  |
| term_start_date | VARCHAR | 学期开始日期 |  |
| term_end_date | VARCHAR | 学期结束日期 |  |
| term_renewal_start_date | VARCHAR | 学期续报开始日期 |  |
| term_renewal_end_date | VARCHAR | 学期续报结束日期 |  |
| is_target | VARCHAR | 是否标内学期1是0否 |  |
| user_group | VARCHAR | 用户组 |  |
| sku | VARCHAR | 定标SKU |  |
| subject_sku | VARCHAR | 学科SKU(routine用) |  |
| business_line | VARCHAR | 业务线 |  |
| class_group_tag_name | VARCHAR | 人群标签名称 |  |
| goal_class_tag | VARCHAR | 目标班型 |  |
| goal_operation_group | VARCHAR | 目标运营组 |  |
| goal_mkt_group | VARCHAR | 目标市场组 |  |
| goal_channel_type | VARCHAR | 目标渠道类型 |  |
| channel_type_name | VARCHAR | 渠道类型 |  |
| channel_subtype_name | VARCHAR | 渠道子类型 |  |
| channel_group_name | VARCHAR | 渠道组名称 |  |
| renewal_counselor_group_city | VARCHAR | 续报老师所在组基地 |  |
| pay_grade | VARCHAR | 支付链接上带的年级,对应关系:1一年级、2二年级、3三年级、4四年级、5五年级、6六年级、7幼儿中班、8幼儿大班、11初一、12初二、13初三、14Python入门 |  |
| package_grade | VARCHAR | 年级等级-编程:高年级和低年级,数学:目前是一年级/二年级/三年级 |  |
| diff_term_start | VARCHAR | 囤量(学期开始-支付时间) |  |
| parents_city | VARCHAR | 城市(手机号解析) |  |
| city_level | VARCHAR | 手机号对应的城市等级 |  |
| platform | VARCHAR | 上课设备 |  |
| is_repeat_order | VARCHAR | 是否重复购买 |  |
| income_level | VARCHAR | 收入水平(该字段已废弃,为NULL) |  |
| inviter_super_category_name | VARCHAR | 转介绍运营方向 |  |
| inviter_annual_course_name | VARCHAR | 转介绍年课课程类型 |  |
| inviter_detail_name | VARCHAR | 转介绍年课详细名称 |  |
| inviter_parents_city_level | VARCHAR | 转介绍家长城市等级 |  |
| inviter_parents_city | VARCHAR | 转介绍家长城市 |  |
| inviter_is_same_city_level | VARCHAR | 转介绍是否同城市等级 |  |
| inviter_is_same_city | VARCHAR | 转介绍是否同城市 |  |
| inviter_is_annual_renewal_period | VARCHAR | 转介绍是否续报周期 |  |
| inviter_invite_stage | VARCHAR | 转介绍邀请阶段状态 |  |
| inviter_up_type | VARCHAR | 转介绍用户上传类型 |  |
| inviter_referral_channel_type | VARCHAR | 转介绍渠道类型 |  |
| inviter_life_cycle | VARCHAR | 转介绍邀请生命周期 |  |
| b2c_school_city | VARCHAR | BTC学校所在城市 |  |
| b2c_school_district | VARCHAR | BTC学校所在区县 |  |
| city_score | VARCHAR | 城市得分 |  |
| b2c_city_score | VARCHAR | BTC学校所在城市得分 |  |
| unlocked_last_time | VARCHAR | 末课解锁时间 |  |
| is_inviter_recall | VARCHAR | 是否转介绍召回 |  |
| channel_account | VARCHAR | 子渠道描述对应的kol名 |  |
| termid_enrollment_count | BIGINT | 该学期id招生计划人数 |  |
| new_class_tag_name | VARCHAR | 新班级标签名称 |  |
| refund_stage | VARCHAR | 退费状态(年课订单) |  |
| channel_desc | VARCHAR | 子渠道描述,同name |  |
| org_term | VARCHAR | 召回原始学期 |  |
| org_sku | VARCHAR | 召回原始SKU |  |
| org_business_line | VARCHAR | 召回原始业务线 |  |
| is_smart_match | VARCHAR | 是否智能匹配订单:1是0否 |  |
| analysis_class_tag | VARCHAR | 班型(分析用) |  |
| is_inclass | BIGINT | 是否进班:1是0否 |  |
| stock_id | BIGINT | 库存ID |  |
| batch_id | BIGINT | 批次ID |  |
| intend_term_id | BIGINT | 计划进班学期id |  |
| intend_term_name | VARCHAR | 计划进班学期 |  |
| enroll | BIGINT | 进班人数 |  |
| wx_add_ahead_unlocked1 | BIGINT | 首课解锁前加微人数 |  |
| wx_add | BIGINT | 累计加微人数 |  |
| wx_add_t3 | BIGINT | 3日内加微人数 |  |
| attend0_ahead_unlocked1 | BIGINT | 首课解锁前准备课到课人数 |  |
| attend1_t6 | BIGINT | 1课到课人数 |  |
| attend2_t6 | BIGINT | 2课到课人数 |  |
| attend3_t6 | BIGINT | 3课到课人数 |  |
| attend4_t6 | BIGINT | 4课到课人数 |  |
| attend5_t6 | BIGINT | 5课到课人数 |  |
| attend_last_t6 | BIGINT | 末课到课人数 |  |
| finish1_t6 | BIGINT | 1课完课人数 |  |
| finish2_t6 | BIGINT | 2课完课人数 |  |
| finish3_t6 | BIGINT | 3课完课人数 |  |
| finish4_t6 | BIGINT | 4课完课人数 |  |
| finish5_t6 | BIGINT | 5课完课人数 |  |
| finish_last_t6 | BIGINT | 末课完课人数 |  |
| renewal_acc | BIGINT | 累计续报人数 |  |
| renewal_t0 | BIGINT | 1日续报人数 |  |
| renewal_t1 | BIGINT | 2日续报人数 |  |
| renewal_t2 | BIGINT | 3日续报人数 |  |
| renewal_t3 | BIGINT | 4日续报人数 |  |
| renewal_lst | BIGINT | 末日续报人数 |  |
| inrenewal_renewal_gmv | DECIMAL(20,6) | 续报期内gmv |  |
| renewal_gmv | DECIMAL(20,6) | 累计gmv |  |
| apply_refund | BIGINT | 退费人数 |  |
| dt | VARCHAR | 分区字段 |  |

## ads_eda_l1_business_counselor_df

L1 业务·班主任维度宽表：按 term × sku × counselor / renewal_staff 聚合，含 goal_operation_group / is_target / super_term_tag 等目标标签字段；用于 L1 体验课的班主任与续报员人效、续报率诊断分析。日期字段为 VARCHAR。

| 字段 | 类型 | 中文名 | 口径 |
|---|---|---|---|
| term_id | BIGINT | 学期id |  |
| term | VARCHAR | 学期名称 |  |
| term_enroll_start_date | VARCHAR | 学期招生开始日期 |  |
| term_start_date | VARCHAR | 学期开始日期 |  |
| term_start_month | VARCHAR | 学期开始月份 |  |
| term_renewal_start_date | VARCHAR | 学期续报开始日期 |  |
| term_renewal_start_month | VARCHAR | 学期续报开始月份 |  |
| term_renewal_end_date | VARCHAR | 学期续报结束日期 |  |
| term_renewal_end_month | VARCHAR | 学期续报结束月份 |  |
| user_group | VARCHAR | 用户群体 |  |
| sku | VARCHAR | SKU |  |
| subject_sku | VARCHAR | 学科SKU(routine用) |  |
| business_line | VARCHAR | 定标业务线 |  |
| is_target | VARCHAR | 是否标内学期:1标内0标外 |  |
| goal_class_tag | VARCHAR | 定标班型 |  |
| goal_operation_group | VARCHAR | 定标团队 |  |
| user_class_group_id | BIGINT | 该订单最后进入的班级id |  |
| user_class_group_name | VARCHAR | 班级名称 |  |
| package_grade | VARCHAR | 高低年级 |  |
| super_term_tag | VARCHAR | 班型 |  |
| counselor_id | BIGINT | 老师id |  |
| counselor_name | VARCHAR | 老师名称 |  |
| counselor_real_name | VARCHAR | 老师真名 |  |
| renewal_staff_id | BIGINT | 续报老师员工id |  |
| renewal_staff_name | VARCHAR | 续报班级老师真名 |  |
| renewal_staff_employee_no | VARCHAR | 续报老师工号 |  |
| renewal_counselor_group_id | VARCHAR | 续报老师组id |  |
| renewal_counselor_group_name | VARCHAR | 续报老师组名称 |  |
| renewal_counselor_group_city | VARCHAR | 续报老师组基地 |  |
| is_new_renewal_staff | BIGINT | 续报老师是否新人:1是0否 |  |
| is_new_class_ct | BIGINT | 本期次老师的班型是否与上期次一致:0否1是 |  |
| lag_class_group_tag | VARCHAR | 上期次老师的班型 |  |
| is_new_class_ct_business | BIGINT | 本期次老师的班型是否与上期次一致:0否1是(经营口径) |  |
| lag_class_group_tag_business | VARCHAR | 上期次老师的班型:CONCAT(user_group,goal_parent_operation_type_name,sku,goal_class_tag)(经营口径) |  |
| new_class_tag_name | VARCHAR | 新班级标签名称 |  |
| enroll | BIGINT | 进班人数 |  |
| wx_add_ahead_unlocked1 | BIGINT | 首课解锁前加微人数 |  |
| wx_add | BIGINT | 加微人数 |  |
| wx_add_t3 | BIGINT | 3日内加微人数 |  |
| attend0_ahead_unlocked1 | BIGINT | 首课解锁前准备课到课人数 |  |
| attend1_t6 | BIGINT | 1课到课人数 |  |
| attend2_t6 | BIGINT | 2课到课人数 |  |
| attend3_t6 | BIGINT | 3课到课人数 |  |
| attend4_t6 | BIGINT | 4课到课人数 |  |
| attend5_t6 | BIGINT | 5课到课人数 |  |
| attend_last_t6 | BIGINT | 末课到课人数 |  |
| finish1_t6 | BIGINT | 1课完课人数 |  |
| finish2_t6 | BIGINT | 2课完课人数 |  |
| finish3_t6 | BIGINT | 3课完课人数 |  |
| finish4_t6 | BIGINT | 4课完课人数 |  |
| finish5_t6 | BIGINT | 5课完课人数 |  |
| finish_last_t6 | BIGINT | 末课完课人数,末课解锁后7日内完课 |  |
| renewal_acc | BIGINT | 续报(全局累计续报)人数,全局累计续报 |  |
| renewal_t0 | BIGINT | 首日续报人数 |  |
| renewal_t1 | BIGINT | 2日续报人数 |  |
| renewal_t2 | BIGINT | 3日续报人数 |  |
| renewal_t3 | BIGINT | 4日续报人数 |  |
| renewal_lst | BIGINT | 末日续报人数 |  |
| inrenewal_renewal_gmv | DECIMAL(20,6) | 续报期内gmv |  |
| renewal_gmv | DECIMAL(20,6) | 累计续报gmv |  |
| dt | VARCHAR | 分区字段 |  |

## man_l1_s_goal_small_detail

L1 体验课销售 S 标目标明细（人工维护）：按 term × sku × user_group × 分析班渠道维度，给出招生 / 续报 / GMV / 续报率 / ASP 等 S 标目标。供 S 标追标 SQL 引用；金额字段为 DECIMAL(20,6)。

| 字段 | 类型 | 中文名 | 口径 |
|---|---|---|---|
| asp_target | BIGINT | 目标asp |  |
| gmv_target | DECIMAL(20,6) | 目标gmv |  |
| analysis_class_channel_tag | VARCHAR | 切换智配项目前的分析班型(analysis_class_tag),用于将当前智配项目和以前对应分析班型做对比 |  |
| analysis_class_tag | VARCHAR | 分析班型 |  |
| enroll_target | DECIMAL(20,6) | 目标招生人数 |  |
| goal_channel_type | VARCHAR | 目标渠道类型 |  |
| goal_class_tag | VARCHAR | 目标班型 |  |
| goal_mkt_group | VARCHAR | 目标核算组 |  |
| goal_operation_group | VARCHAR | 目标运营组 |  |
| goal_parent_operation_type_name | VARCHAR | 业务线 |  |
| renewal_rate_target | DECIMAL(20,6) | 目标续报率 |  |
| renewal_target | DECIMAL(20,6) | 目标续报人数 |  |
| sku | VARCHAR | sku |  |
| term_enroll_end_date | VARCHAR | 学期招生结束时间 |  |
| term_enroll_end_month | VARCHAR | 学期招生结束月份 |  |
| term_enroll_start_date | VARCHAR | 学期招生开始时间 |  |
| term_name | VARCHAR | 学期名称 |  |
| term_perform_month | VARCHAR | 学期执行月份 |  |
| term_renewal_end_date | VARCHAR | 学期续报结束时间 |  |
| term_renewal_end_month | VARCHAR | 学期续报结束月份 |  |
| term_renewal_start_date | VARCHAR | 学期续报开始时间 |  |
| term_start_date | VARCHAR | 学期开始时间 |  |
| user_group | VARCHAR | 用户群体 |  |
| dt | VARCHAR | 分区字段 |  |

## dim_analysis_class_channel_df

分析班 × 渠道维度桥接表：把 analysis_class_tag × goal_mkt_group × goal_channel_type × sku 组合派生为 analysis_class_channel_tag，用于销售目标表 (man_l1_s_goal_small_detail) 与业务宽表 (ads_eda_l1_v2_business_regular_df) 之间的维度对齐。

业务字段映射：
- 「大班型_渠道来源」= analysis_class_channel_tag（本表）；分析渠道维度的过程转化 / 续报时按此字段分组。
- 区分于：channel_type_name（渠道类型）、channel_group_name（渠道分组）、goal_class_tag（目标班型标签）—— 这三个字段不是「大班型_渠道来源」。
- 主要关联事实表：ads_eda_l1_v2_business_regular_df（JOIN 键：analysis_class_tag + goal_mkt_group + goal_channel_type + sku + dt）。

业务别名（按场景）：
- 智配项目分析场景（ads_eda_l1_v2_business_regular_df.is_smart_match='1' AND analysis_class_tag='智配项目'）下，「来源」「渠道来源」即 analysis_class_channel_tag；查询时需将本表 JOIN 到主事实表 ads_eda_l1_v2_business_regular_df（JOIN 键：analysis_class_tag + goal_mkt_group + goal_channel_type + sku + dt）。
- 其他业务场景的「来源」口径尚未确认，遇到非智配场景时应先向用户确认，不自动套用本字段。

| 字段 | 类型 | 中文名 | 口径 |
|---|---|---|---|
| analysis_class_tag | VARCHAR | 班型(分析用) |  |
| goal_mkt_group | VARCHAR | 定标核算组 |  |
| goal_channel_type | VARCHAR | 定标渠道类型 |  |
| sku | VARCHAR | sku |  |
| analysis_class_channel_tag | VARCHAR | 切换智配项目前的分析班型(analysis_class_tag),用于将当前智配项目和以前对应分析班型做对比 |  |
| dt | VARCHAR | 分区字段 |  |

## ads_eda_l1_v2_multidimensional_indicators_df

L1 体验课多维指标明细宽表（行/订单粒度）：每行一笔订单/用户在某 term × sku × 渠道 × 班主任 × 家长地理 × 年级维度下的转化与续报快照。包含 92 列：支付/期次/班期/续报区间日期（VARCHAR）、term/sku/business_line/goal_class_tag/goal_operation_group/channel_* 等业务维度、renewal_staff 与 renewal_counselor_group 维度、parents_province/parents_city/city_level/pay_grade/talent_author_name 用户画像维度，以及 login/enroll/wx_add(+t10min/t1h/t24h/t48h/t3 时间窗)/attend1~5_t6/attend_last_t6/finish1~5_t6/finish_last_t6/renewal_t0~t3/renewal_lst/renewal_is_ahead/renewal_acc 等行级 0/1 转化标记；金额字段（inrenewal_renewal_gmv/renewal_gmv/refund_*_gmv）为 DECIMAL(20,6) 或 DECIMAL(20,2)；六类退费分阶段 cnt + gmv（before_enter_class / before_unclock_class / after_unclock_class / fullvl / before_fullvl）；direct_sale_cnt、class_teacher_hit_order_cnt 直销与命中标记。分区字段 dt（VARCHAR）。\n\n粒度说明：本表保留行级 0/1 标记，可在订单/用户粒度做漏斗、cohort、时滞与人群细分分析；适用于按家长地理 / 年级 / 达人 / 时间窗等细维度下钻的场景。已聚合宽表请用 ads_eda_l1_v2_business_regular_df。

| 字段 | 类型 | 中文名 | 口径 |
|---|---|---|---|
| is_target | VARCHAR | 是否标内学期:1标内0标外 |  |
| pay_year | VARCHAR | 订单支付年份 |  |
| pay_month | VARCHAR | 订单支付月份 |  |
| pay_date | VARCHAR | 订单支付日期 |  |
| term_enroll_start_month | VARCHAR | 招生开始月份 |  |
| term_enroll_start_date | VARCHAR | 招生开始日期 |  |
| term_enroll_end_month | VARCHAR | 招生结束月份 |  |
| term_enroll_end_date | VARCHAR | 招生结束日期 |  |
| user_class_start_date | VARCHAR | 用户首次进班日期 |  |
| term_start_year | VARCHAR | 学期开始年份 |  |
| term_start_month | VARCHAR | 学期开始月份 |  |
| term_start_date | VARCHAR | 学期开始日期 |  |
| term_renewal_start_date | VARCHAR | 学期续报开始日期 |  |
| term_renewal_start_month | VARCHAR | 学期续报开始月份 |  |
| term_renewal_end_date | VARCHAR | 学期续报结束日期 |  |
| term_renewal_end_month | VARCHAR | 学期续报结束月份 |  |
| term_id | BIGINT | 学期id |  |
| term | VARCHAR | 学期名称 |  |
| intend_term_id | BIGINT | 计划进班学期id |  |
| intend_term | VARCHAR | 计划进班学期 |  |
| user_group | VARCHAR | 用户群体 |  |
| business_line | VARCHAR | 定标业务线 |  |
| goal_class_tag | VARCHAR | 定标班型 |  |
| sku | VARCHAR | 定标SKU |  |
| goal_operation_group | VARCHAR | 定标团队 |  |
| renewal_staff_id | BIGINT | 续报老师员工id |  |
| renewal_staff_name | VARCHAR | 续报班级老师真名 |  |
| renewal_staff_employee_no | VARCHAR | 续报老师工号 |  |
| renewal_counselor_group_id | VARCHAR | 续报老师组id |  |
| renewal_counselor_group_name | VARCHAR | 续报老师组名称 |  |
| renewal_counselor_group_city | VARCHAR | 续报老师组基地 |  |
| is_new_renewal_staff | BIGINT | 续报老师是否新人:1是0否 |  |
| is_new_class_ct_business | BIGINT | 本期次老师的班型是否与上期次一致:0否1是(经营口径) |  |
| goal_mkt_group | VARCHAR | 定标核算组 |  |
| channel_type_name | VARCHAR | 渠道类型 |  |
| goal_channel_type | VARCHAR | 定标渠道类型 |  |
| channel_subtype_name | VARCHAR | 渠道子类型 |  |
| channel_group_id | BIGINT | 渠道组id |  |
| channel_group_name | VARCHAR | 渠道组名称 |  |
| new_class_tag_name | VARCHAR | 新班级标签名称 |  |
| talent_author_name | VARCHAR | 达人名称 |  |
| diff_term_start | VARCHAR | 等待开课天数 |  |
| is_repeat_order | VARCHAR | 是否重复购买:(package_group、package_phase、package_course_type相同就互斥;即同一学科的第二次购买算重复购买) |  |
| pay_grade | VARCHAR | 支付年级 |  |
| parents_province | VARCHAR | 家长所在省份,手机号解析 |  |
| parents_city | VARCHAR | 家长所在城市,手机号解析 |  |
| city_level | VARCHAR | 家长所在城市线级,手机号解析 |  |
| login | BIGINT | 加微人数 |  |
| enroll | BIGINT | 进班人数 |  |
| wx_add_ahead_unlocked1 | BIGINT | 首课解锁前加微人数 |  |
| wx_add | BIGINT | 累计加微人数 |  |
| wx_add_t10min | BIGINT | 10min内加微人数 |  |
| wx_add_t1h | BIGINT | 1h内加微人数 |  |
| wx_add_t24h | BIGINT | 24h内加微人数 |  |
| wx_add_t48h | BIGINT | 48h内加微人数 |  |
| wx_add_t3 | BIGINT | 3日内加微人数 |  |
| attend0_ahead_unlocked1 | BIGINT | 首课解锁前准备课到课人数 |  |
| attend1_ahead_del_wx | BIGINT | 1课前删微人数 |  |
| attend1_t6 | BIGINT | 1课到课人数 |  |
| attend2_t6 | BIGINT | 2课到课人数 |  |
| attend3_t6 | BIGINT | 3课到课人数 |  |
| attend4_t6 | BIGINT | 4课到课人数 |  |
| attend5_t6 | BIGINT | 5课到课人数 |  |
| attend_last_t6 | BIGINT | 末课到课人数 |  |
| finish1_t6 | BIGINT | 1课完课人数 |  |
| finish2_t6 | BIGINT | 2课完课人数 |  |
| finish3_t6 | BIGINT | 3课完课人数 |  |
| finish4_t6 | BIGINT | 4课完课人数 |  |
| finish5_t6 | BIGINT | 5课完课人数 |  |
| finish_last_t6 | BIGINT | 末课完课人数 |  |
| renewal_acc | BIGINT | 累计续报人数 |  |
| renewal_is_ahead | BIGINT | 提前收单人数 |  |
| renewal_t0 | BIGINT | 1日续报人数 |  |
| renewal_t1 | BIGINT | 2日续报人数 |  |
| renewal_t2 | BIGINT | 3日续报人数 |  |
| renewal_t3 | BIGINT | 4日续报人数 |  |
| renewal_lst | BIGINT | 末日续报人数 |  |
| inrenewal_renewal_gmv | DECIMAL(20,6) | 续报期内gmv |  |
| renewal_gmv | DECIMAL(20,6) | 累计gmv |  |
| refund_cnt | BIGINT | 年课退费人数 |  |
| refund_before_enter_class_cnt | BIGINT | 年课全额期-进班前退费人数 |  |
| refund_before_unclock_class_cnt | BIGINT | 年课全额期-开课前退费人数 |  |
| refund_after_unclock_class_cnt | BIGINT | 年课全额期-开课后退费人数 |  |
| refund_fullvl_cnt | BIGINT | 年课非全额期退费人数 |  |
| refund_before_fullvl_cnt | BIGINT | 年课全额期退费人数 |  |
| refund_gmv | DECIMAL(20,2) | 年课退费gmv |  |
| refund_before_enter_class_gmv | DECIMAL(20,2) | 年课全额期-进班前退费gmv |  |
| refund_before_unclock_class_gmv | DECIMAL(20,2) | 年课全额期-开课前退费gmv |  |
| refund_after_unclock_class_gmv | DECIMAL(20,2) | 年课全额期-开课后退费gmv |  |
| refund_fullvl_gmv | DECIMAL(20,2) | 年课非全额期退费gmv |  |
| refund_before_fullvl_gmv | DECIMAL(20,2) | 年课全额期退费gmv |  |
| direct_sale_cnt | BIGINT | 续报的年课订单为直售订单的人数 |  |
| class_teacher_hit_order_cnt | BIGINT | 续报的年课订单和班主任撞单的人数 |  |
| dt | VARCHAR | 日期分区 |  |

## ba_eda_l1_v2_info_order_hdf

L1 体验课 v2 订单信息事实表（订单粒度，一行一笔 order_no）：ba_eda 系列中 ads_eda_l1_v2_business_regular_df / ads_eda_l1_v2_multidimensional_indicators_df 的订单级底表，类比 ba_eda_annual_info_order_hdf 的 L1 v2 版本。共 217 列，覆盖六大维度组：(1) 订单/支付——order_no、user_id、pay_time/pay_date/pay_month、real_pay、ctime；(2) 期次/班期——term_id/term_name、term_enroll/start/renewal_*_date、super_term_tag、intend_term_*；(3) 渠道与班型——goal_channel_type、channel_type_name/subtype/group、enroll/renewal/analyse_channel_type、analysis_class_tag、analysis_class_channel_tag、is_smart_match(BIGINT 0/1)、new_class_tag_name、is_tmp_class；(4) 人员——counselor_id/name/real_name、teacher_level、renewal_staff_*、renewal_counselor_group_*；(5) 用户画像与转介绍——pay_grade/package_grade、parents_province/city、city_level、income_level、talent_author_*，以及 inviter_* 一组转介绍来源维度；(6) 过程转化与结果——is_login、wx_add(+t10min/t1h/t24h/t48h/t3)、attend0~5_t6/t0 与 finish0~5_t6/t0（含 _last）、unlock 标记、renewal_t0~t3/renewal_lst/renewal_acc/renewal_is_ahead 行级 0/1，续报金额 renewal_gmv/inrenewal_renewal_gmv，六分阶段退费 refund_*_cnt 与 refund_*_gmv（before_enter_class/before_unclock_class/after_unclock_class/fullvl/before_fullvl）、is_apply/actually_refund、refund_stage、财务口径 is_refund_finance/refund_amount_finance，以及 perf_*_dept_name 绩效部门链路、is_hit_order/business_mode_name。类型约定：日期/时间字段为 VARCHAR，金额为 DECIMAL(20,6)，状态/计数/0-1 标记多为 BIGINT。分区字段 dt（VARCHAR）。注意 is_smart_match 在本表为 BIGINT 0/1，与 ads_eda_l1_v2_business_regular_df 的 VARCHAR 1/0 口径不同，过滤时勿照搬。订单粒度可做漏斗/cohort/退费/转介绍归因；已聚合经营宽表用 ads_eda_l1_v2_business_regular_df。

| 字段 | 类型 | 中文名 | 口径 |
|---|---|---|---|
| order_no | VARCHAR | 订单编号 |  |
| user_id | BIGINT | 用户id |  |
| pay_time | VARCHAR | 订单支付时间 |  |
| pay_date | VARCHAR | 订单支付日期 |  |
| pay_month | VARCHAR | 订单支付月份 |  |
| term_id | BIGINT | 学期id |  |
| term_name | VARCHAR | 学期名称 |  |
| term_enroll_start_date | VARCHAR | 学期招生开始日期 |  |
| term_start_date | VARCHAR | 学期开始日期 |  |
| term_start_month | VARCHAR | 学期开始月份 |  |
| term_renewal_start_date | VARCHAR | 学期续报开始日期 |  |
| term_renewal_start_month | VARCHAR | 学期续报开始月份 |  |
| term_renewal_end_date | VARCHAR | 学期续报结束日期 |  |
| term_renewal_end_month | VARCHAR | 学期续报结束月份 |  |
| user_class_group_id | BIGINT | 该订单最后进入的班级id |  |
| user_class_group_name | VARCHAR | 班级名称 |  |
| user_group | VARCHAR | 用户群体 |  |
| sku | VARCHAR | 定标SKU |  |
| goal_parent_operation_type_name | VARCHAR | 定标业务线 |  |
| is_target | VARCHAR | 是否标内学期:1标内0标外 |  |
| goal_class_tag | VARCHAR | 定标班型 |  |
| goal_operation_group | VARCHAR | 定标团队 |  |
| goal_mkt_group | VARCHAR | 定标核算组 |  |
| goal_channel_type | VARCHAR | 定标渠道类型 |  |
| channel_type_name | VARCHAR | 渠道类型 |  |
| channel_subtype_name | VARCHAR | 渠道子类型 |  |
| channel_group_id | BIGINT | 渠道组id |  |
| channel_group_name | VARCHAR | 渠道组名称 |  |
| enroll_channel_type | VARCHAR | 招生渠道类型(商分录入) |  |
| renewal_channel_type | VARCHAR | 招续报渠道类型(商分录入) |  |
| is_live | BIGINT | 是否直播渠道:1是0否 |  |
| talent_author_id | BIGINT | 达人id |  |
| talent_author_name | VARCHAR | 达人名称 |  |
| subject_type | VARCHAR | 科目类型:C++,Math,Python,ScratchJr,Script,Standard,Go,Write,Piano |  |
| class_group_tag_name | VARCHAR | 人群标签名称 |  |
| super_term_tag | VARCHAR | 班型 |  |
| counselor_id | BIGINT | 老师id |  |
| teacher_level | VARCHAR | 老师评级 |  |
| is_continue_class | BIGINT | 老师是否连续带班:1是0否 |  |
| renewal_staff_id | BIGINT | 续报老师员工id |  |
| renewal_staff_name | VARCHAR | 续报班级老师真名 |  |
| renewal_staff_employee_no | VARCHAR | 续报老师工号 |  |
| renewal_counselor_group_id | VARCHAR | 续报老师组id |  |
| renewal_counselor_group_name | VARCHAR | 续报老师组名称 |  |
| renewal_counselor_group_city | VARCHAR | 续报老师组基地 |  |
| is_new_renewal_staff | BIGINT | 续报老师是否新人:1是0否 |  |
| is_new_acourse | BIGINT | 是否新课:1是0否 |  |
| is_refund_before_start | BIGINT | 是否开课前退费:1是0否 |  |
| pay_grade | VARCHAR | 支付年级 |  |
| package_grade | VARCHAR | 高低年级 |  |
| diff_term_start | VARCHAR | 等待开课天数 |  |
| parents_city | VARCHAR | 家长所在城市,手机号解析 |  |
| city_level | VARCHAR | 家长所在城市线级,手机号解析 |  |
| platform | VARCHAR | 上课设备 |  |
| is_repeat_order | VARCHAR | 是否重复购买 |  |
| income_level | VARCHAR | 收入水平 |  |
| inviter_super_category_name | VARCHAR | 转介绍运营方向 |  |
| inviter_annual_course_name | VARCHAR | 转介绍学科名称 |  |
| inviter_detail_name | VARCHAR | 转介绍学科详细名称 |  |
| inviter_parents_city_level | VARCHAR | 转介绍城市线级 |  |
| inviter_parents_city | VARCHAR | 转介绍城市 |  |
| inviter_is_same_city_level | VARCHAR | 转介绍是否同城市线级 |  |
| inviter_is_same_city | VARCHAR | 转介绍是否同城市 |  |
| inviter_is_annual_renewal_period | VARCHAR | 转介绍是否续报周期 |  |
| inviter_invite_stage | VARCHAR | 转介绍邀请阶段状态 |  |
| inviter_up_type | VARCHAR | 转介绍用户上传类型 |  |
| inviter_life_cycle | VARCHAR | 转介绍邀请生命周期 |  |
| inviter_referral_channel_type | VARCHAR | 转介绍渠道类型 |  |
| b2c_school_city | VARCHAR | BTC学校所在城市 |  |
| b2c_school_district | VARCHAR | BTC学校所在区县 |  |
| b2c_city_score | VARCHAR | BTC学校所在城市评分 |  |
| city_score | VARCHAR | 区域评分 |  |
| leads_rank | VARCHAR | 栗子等级 |  |
| is_inclass | BIGINT | 是否进班 |  |
| wx_add | BIGINT | 是否加微 |  |
| wx_add_t3 | BIGINT | 是否3日内加微 |  |
| unlocked_last_time | VARCHAR | 末课解锁时间 |  |
| attend0_t6 | BIGINT | 是否准备课到课 |  |
| attend1_t6 | BIGINT | 是否1课到课 |  |
| attend2_t6 | BIGINT | 是否2课到课 |  |
| attend3_t6 | BIGINT | 是否3课到课 |  |
| attend4_t6 | BIGINT | 是否4课到课 |  |
| attend5_t6 | BIGINT | 是否5课到课 |  |
| attend_last_t6 | BIGINT | 是否末课到课 |  |
| finish0_t6 | BIGINT | 是否准备课完课 |  |
| finish1_t6 | BIGINT | 是否1课完课 |  |
| finish2_t6 | BIGINT | 是否2课完课 |  |
| finish3_t6 | BIGINT | 是否3课完课 |  |
| finish4_t6 | BIGINT | 是否4课完课 |  |
| finish5_t6 | BIGINT | 是否5课完课 |  |
| finish_last_t6 | BIGINT | 是否末课完课 |  |
| renewal_acc | BIGINT | 是否续报(全局累计续报) |  |
| renewal_is_ahead | BIGINT | 提前收单 |  |
| renewal_t0 | BIGINT | 首日续报 |  |
| renewal_t1 | BIGINT | 2日续报 |  |
| renewal_t2 | BIGINT | 3日续报 |  |
| renewal_t3 | BIGINT | 4日续报 |  |
| renewal_lst | BIGINT | 末日续报 |  |
| inrenewal_renewal_gmv | DECIMAL(20,6) | 续报期内gmv |  |
| renewal_gmv | DECIMAL(20,6) | 累计续报gmv |  |
| renewal_order_no | VARCHAR | 首次续报订单号(年课订单) |  |
| min_renewal_time | VARCHAR | 首次续报时间(年课订单) |  |
| last_transfer_order_no | VARCHAR | 首次续报订单的最后一次转单订单号(年课订单) |  |
| is_apply_refund | BIGINT | 是否申请退费且审核通过:1是0否,来源ods_crm_approval_year_course_refund(年课订单) |  |
| apply_refund_time | VARCHAR | 申请退费且审核通过的申请时间,来源ods_crm_approval_year_course_refund(年课订单) |  |
| is_actually_refund | BIGINT | 是否退费成功:1是0否,来源dwd_trade_refund_hdf(年课订单) |  |
| actually_refund_time | VARCHAR | 退费成功时间,来源dwd_trade_refund_hdf(年课订单) |  |
| is_inviter_recall | BIGINT | 是否转介绍召回:1是0否 |  |
| refund_stage | VARCHAR | 退费状态(年课订单) |  |
| analyse_channel_type | VARCHAR | 分析渠道(BP用) |  |
| term_season | BIGINT | 学季 |  |
| parents_province | VARCHAR | 家长所在省份,手机号解析 |  |
| wx_add_ahead_unlocked1 | BIGINT | 是否首课解锁前加微 |  |
| wx_add_unlocked1_datediff | VARCHAR | 加微时间到首课解锁时间天数差 |  |
| attend0_ahead_unlocked1 | BIGINT | 是否首课解锁前准备课到课 |  |
| attend0_unlocked1_datediff | VARCHAR | 准备课到课时间到首课解锁时间天数差 |  |
| subject_sku | VARCHAR | 学科SKU(routine用) |  |
| counselor_name | VARCHAR | 老师名称 |  |
| counselor_real_name | VARCHAR | 老师真名 |  |
| channel_id | BIGINT | 子渠道id(旧订单表中的order_channel),默认值-1 |  |
| channel_desc | VARCHAR | 子渠道描述,同name |  |
| channel_account | VARCHAR | 子渠道描述对应的kol名 |  |
| term_end_time | VARCHAR | 学期结束时间 |  |
| term_enroll_end_time | VARCHAR | 招生结束时间 |  |
| term_renewal_end_time | VARCHAR | 学期续报结束时间 |  |
| b2c_school_province | VARCHAR | b2c学校所在省份 |  |
| b2c_city_level | VARCHAR | b2c城市等级:一线/二线/三线/四线/五线/新一线 |  |
| original_pay_grade | BIGINT | 支付链接上带的年级,对应关系:1一年级、2二年级、3三年级、4四年级、5五年级、6六年级、7幼儿中班、8幼儿大班、11初一、12初二、13初三、14Python入门 |  |
| first_add_counselor_wx_time | VARCHAR | 首次加微时间 |  |
| original_diff_term_start | BIGINT | 支付到开课等待天数:未分桶 |  |
| original_pay_faw_date_diff | BIGINT | 支付到加微等待天数:未分桶 |  |
| original_faw_term_start_date_diff | BIGINT | 加微到开课等待天数:未分桶 |  |
| user_class_start_time | VARCHAR | 用户进班时间:每个订单每个term_id最后一次进班时间 |  |
| termid_enrollment_count | BIGINT | 该学期id招生计划人数 |  |
| new_class_tag_name | VARCHAR | 新班级标签名称 |  |
| unit1_unlocked_time | VARCHAR | unit1解锁时间 |  |
| attend0_t0 | BIGINT | 是否准备课到课(首日内) |  |
| attend1_t0 | BIGINT | 是否1课到课(首日内) |  |
| attend2_t0 | BIGINT | 是否2课到课(首日内) |  |
| attend3_t0 | BIGINT | 是否3课到课(首日内) |  |
| attend4_t0 | BIGINT | 是否4课到课(首日内) |  |
| attend5_t0 | BIGINT | 是否5课到课(首日内) |  |
| attend_last_t0 | BIGINT | 是否末课到课(首日内) |  |
| finish0_t0 | BIGINT | 是否准备课完课(首日内) |  |
| finish1_t0 | BIGINT | 是否1课完课(首日内) |  |
| finish2_t0 | BIGINT | 是否2课完课(首日内) |  |
| finish3_t0 | BIGINT | 是否3课完课(首日内) |  |
| finish4_t0 | BIGINT | 是否4课完课(首日内) |  |
| finish5_t0 | BIGINT | 是否5课完课(首日内) |  |
| finish_last_t0 | BIGINT | 是否末课完课(首日内) |  |
| gift_province | VARCHAR | 实物系统获取(B2C用户使用学校地址)-省份 |  |
| gift_city | VARCHAR | 实物系统获取(B2C用户使用学校地址)-城市 |  |
| gift_district | VARCHAR | 实物系统获取(B2C用户使用学校地址)-区域 |  |
| gift_city_score | BIGINT | 根据实物系统获取(B2C用户使用学校地址)-城市评分 |  |
| repeat_order_nums | BIGINT | 该用户第几次购买L1订单 |  |
| is_fst_level_unlock | BIGINT | 是否解锁首课level(年课订单) |  |
| is_full_level_unlock | BIGINT | 是否解锁非全额退费期level(年课订单) |  |
| order_business_presentation | VARCHAR | 业务描述字段json字符串 |  |
| order_user_group | VARCHAR | 业务描述字段json字符串 |  |
| wx_add_t10min | BIGINT | 是否10min内加微 |  |
| wx_add_t1h | BIGINT | 是否1h内加微 |  |
| wx_add_t24h | BIGINT | 是否24h内加微 |  |
| wx_add_t48h | BIGINT | 是否48h内加微 |  |
| goal_mkt_group_twenty_four | VARCHAR | 定标核算组2024FY |  |
| goal_channel_type_twenty_four | VARCHAR | 定标渠道类型2024FY |  |
| refund_amount | BIGINT | 退费金额：单位是分(该L1订单续报的首个年课编程订单的退费金额) |  |
| refund_cnt | BIGINT | 年课退费人数 |  |
| refund_before_enter_class_cnt | BIGINT | 年课全额期-进班前退费人数 |  |
| refund_before_unclock_class_cnt | BIGINT | 年课全额期-开课前退费人数 |  |
| refund_after_unclock_class_cnt | BIGINT | 年课全额期-开课后退费人数 |  |
| refund_fullvl_cnt | BIGINT | 年课非全额期退费人数 |  |
| refund_before_fullvl_cnt | BIGINT | 年课全额期退费人数 |  |
| refund_gmv | DECIMAL(20,6) | 退费金额：单位是元(该L1订单根据其pay_class_id续报订单的总退费金额,含编程、专项课等) |  |
| refund_before_enter_class_gmv | DECIMAL(20,6) | 年课全额期-进班前退费金额：单位是元(该L1订单根据其pay_class_id续报订单的总退费金额) |  |
| refund_before_unclock_class_gmv | DECIMAL(20,6) | 年课全额期-开课前退费金额：单位是元(该L1订单根据其pay_class_id续报订单的总退费金额) |  |
| refund_after_unclock_class_gmv | DECIMAL(20,6) | 年课全额期-开课后退费金额：单位是元(该L1订单根据其pay_class_id续报订单的总退费金额) |  |
| refund_fullvl_gmv | DECIMAL(20,6) | 年课非全额期退费金额：单位是元(该L1订单根据其pay_class_id续报订单的总退费金额) |  |
| refund_before_fullvl_gmv | DECIMAL(20,6) | 年课全额期退费金额：单位是元(该L1订单根据其pay_class_id续报订单的总退费金额) |  |
| org_term | VARCHAR | 召回原始学期 |  |
| org_sku | VARCHAR | 召回原始SKU |  |
| org_business_line | VARCHAR | 召回原始业务线 |  |
| org_order_no | VARCHAR | 召回原始订单编号 |  |
| inviter_id | BIGINT | 转介绍原始用户id |  |
| renewal_order_no_concat | VARCHAR | 续报订单号(续报的所有年课订单号) |  |
| renewal_pay_time_concat | VARCHAR | 续报时间(续报的所有年课订单支付时间) |  |
| third_order_no | VARCHAR | 第三方订单号 |  |
| after_sale | BIGINT | 售后类型 0退费退班 1退费不退班 |  |
| active_delay | BIGINT | 延迟激活0立即激活 大于0延迟具体多少分钟激活 |  |
| is_refund_finance | BIGINT | 是否退费(L1订单 |  |
| refund_amount_finance | DECIMAL(20,6) | 退款金额:元(L1订单) |  |
| real_pay | DECIMAL(20,6) | 订单上的实付价格 单位:元 原始order_amount(L1订单) |  |
| ctime | VARCHAR | 创建时间(进流量池时间) |  |
| budget_group_name | VARCHAR | 业务组名称 |  |
| finance_sku | VARCHAR | 财务SKU(财务核算CAC用) |  |
| is_smart_match | BIGINT | 是否智能匹配订单:1是 |  |
| analysis_class_tag | VARCHAR | 班型(分析用) |  |
| stock_id | BIGINT | 库存ID |  |
| batch_id | BIGINT | 批次ID |  |
| intend_term_id | BIGINT | 计划进班学期id |  |
| intend_term_name | VARCHAR | 计划进班学期 |  |
| channel_group_tag_id_names | VARCHAR | 渠道组标签id-name,如:1-0元,2-1元,3-6.8元 |  |
| analysis_class_channel_tag | VARCHAR | 切换智配项目前的分析班型(analysis_class_tag),用于将当前智配项目和以前对应分析班型做对比 |  |
| is_tmp_class | BIGINT | 是否智配占位班级 1-是 0-否(L1使用) |  |
| business_team_tag_id | BIGINT | 承接团队标签id |  |
| order_source_category | VARCHAR | 订单来源类别:直售 |  |
| is_hit_order | VARCHAR | 是否撞单:0-否;1-和班主任撞单;2-和课导撞单 |  |
| is_new_class_ct_business | BIGINT | 本期次老师的班型是否与上期次一致:0否1是(经营口径) |  |
| is_login | BIGINT | 是否登录:0-否;1-是 |  |
| is_attend1_ahead_del_wx | BIGINT | 是否1课前删微:0-否;1-是 |  |
| perf_prepare_dept_name | VARCHAR | 绩效-备选管理部门名称 |  |
| perf_frontline_dept_name | VARCHAR | 绩效-一线部门名称 |  |
| perf_junior_dept_name | VARCHAR | 绩效-初级部门名称 |  |
| perf_senior_dept_name | VARCHAR | 绩效-高级部门名称 |  |
| perf_primary_dept_name | VARCHAR | 绩效-部部门名称 |  |
| business_mode_name | VARCHAR | 业务模式名称 |  |
| is_contain_box | BIGINT | 是否包含盒子：1是0否 |  |
| dt | VARCHAR | 分区字段 |  |

