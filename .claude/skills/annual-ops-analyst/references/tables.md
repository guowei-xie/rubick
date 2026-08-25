# 可见数据表

## ads_bam_annual_process_class_hdf

年课（BAM）过程运营班级宽表（班级组粒度，一行一个 user_class_group_id，65 列）：term × 班主任(counselor)/续报员(renewal_staff)维度，覆盖基地(base_dept)/大区(region_dept)/学部(xuebu)/班主任组(counselor_group)四级组织链（部门、城市、负责人姓名与工号），过程转化计数——renewal_user_cnt、赛考报名(saikao_enroll_cnt)、Level 报名/到课/出结果(level_enroll/attend/result_cnt)、解锁(unlock_cnt)、day0/day8/累计完课(day0_finish/day8_finish/acc_finish_cnt)，分日续报 user_renewal_day1/day4/day11 与累计 user_renewal_acc，以及首 Level 班(fstlvl_class_id/fstlvl_unlock_cnt)、renewal_times、is_new_counselor/is_sameterm_dual_class/is_throw_class 等运营标记。日期字段为 VARCHAR；dt 为快照分区（当前单分区，约 1.28 万行、614 个 term）。用于年课经营分析场景：过程转化漏斗、班主任/续报员人效、组织维度诊断。

| 字段 | 类型 | 中文名 | 口径 |
|---|---|---|---|
| term_id | BIGINT | 续报开始时学期ID |  |
| term_name | VARCHAR | 续报开始时学期名称(期次) |  |
| term_year | VARCHAR | 续报开始时学年 |  |
| term_start_time | VARCHAR | 续报开始时学期开始时间 |  |
| term_renewal_start_time | VARCHAR | 续报开始时间 |  |
| annual_course_name | VARCHAR | 年课名称 |  |
| parent_operation_type_name | VARCHAR | 续报开始时父节点运营类型名称 |  |
| operation_type_name | VARCHAR | 续报开始时运营类型名称 |  |
| renewal_technology_spec_type | VARCHAR | 续报开始时科特 非科特 |  |
| renewal_class_type | VARCHAR | 续报开始时学期类型 |  |
| user_class_group_id | BIGINT | 续报开始时班级ID |  |
| package_grade | VARCHAR | 编程:高年级和低年级,数学:目前是一年级/二年级/三年级 |  |
| counselor_id | BIGINT | 课导ID |  |
| counselor_name | VARCHAR | 课导花名 |  |
| renewal_staff_id | BIGINT | 续报员工ID(课导ID) |  |
| renewal_staff_employee_no | VARCHAR | 续报员工工号 |  |
| renewal_staff_name | VARCHAR | 续报员工姓名 |  |
| renewal_staff_entry_time | VARCHAR | 续报员工入职时间 |  |
| base_dept_id | BIGINT | 基地部门ID |  |
| base_dept_name | VARCHAR | 基地部门名称 |  |
| base_dept_city | VARCHAR | 基地部门所在城市 |  |
| base_leader_staff_name | VARCHAR | 基地部门leader姓名 |  |
| base_leader_staff_employee_no | VARCHAR | 基地部门leader工号 |  |
| region_dept_id | BIGINT | 大区部门ID |  |
| region_dept_name | VARCHAR | 大区部门名称 |  |
| region_dept_city | VARCHAR | 大区部门所在城市 |  |
| region_leader_staff_name | VARCHAR | 大区部门leader姓名 |  |
| region_leader_staff_employee_no | VARCHAR | 大区部门leader工号 |  |
| region_leader_counselor_id | BIGINT | 大区部门leader课导ID |  |
| xuebu_id | BIGINT | 学部ID(初级管理部门) |  |
| xuebu_name | VARCHAR | 学部名称 |  |
| xuebu_city | VARCHAR | 学部所在城市 |  |
| xuebu_leader_staff_name | VARCHAR | 学部leader姓名 |  |
| xuebu_staff_employee_no | VARCHAR | 学部leader工号 |  |
| counselor_group_id | BIGINT | 课导组ID(一线部门ID) |  |
| counselor_group_name | VARCHAR | 课导组名称(一线部门名称) |  |
| counselor_group_city | VARCHAR | 课导组所在城市(一线部门城市） |  |
| counselor_group_leader_staff_name | VARCHAR | 课导组leader姓名(一线部门leader) |  |
| counselor_leader_staff_employee_no | VARCHAR | 课导组leader工号 |  |
| renewal_user_cnt | BIGINT | 续报开始时在班学员人数 |  |
| saikao_enroll_cnt | BIGINT | 赛考招报人数(等级+赛事) |  |
| level_enroll_cnt | BIGINT | 等级考试招报人数 |  |
| level_attend_cnt | BIGINT | 等级考试参赛人数 |  |
| level_result_cnt | BIGINT | 等级考试通过人数 |  |
| unlock_cnt | BIGINT | 解锁人数 |  |
| day0_finish_cnt | BIGINT | 解锁当日完课数 |  |
| day8_finish_cnt | BIGINT | 解锁7日完课数 |  |
| acc_finish_cnt | BIGINT | 累计完课数 |  |
| user_renewal_acc | BIGINT | 累计续报人数 |  |
| user_renewal_day1 | BIGINT | 续报首日续报人数 |  |
| user_renewal_day4 | BIGINT | 续报4天内续报人数 |  |
| user_renewal_day11 | BIGINT | 续报11天内续报人数 |  |
| fstlvl_class_id | BIGINT | 首课解锁班级id |  |
| fstlvl_unlock_cnt | BIGINT | 首课解锁人数 |  |
| renewal_times | INTEGER | 续报次数(课导维度) |  |
| is_new_counselor | INTEGER | 是否新课导(0否1是) |  |
| sameterm_class_cnt | BIGINT | 同期带班数量 |  |
| is_sameterm_dual_class | INTEGER | 是否同期双班(0否1是) |  |
| is_throw_class | INTEGER | 是否拆班(0否1是) |  |
| dt | VARCHAR | 分区字段 |  |
| contest_enroll_cnt | BIGINT | 赛事招报人数[TODO 待业务确认] |  |
| fin_fstlvl_unlocked_cnt | BIGINT | 首课最终解锁人数[TODO 待业务确认，可能与旧字段 fstlvl_renewal_cnt 相关] |  |
| transfer_user_cnt | BIGINT | 转班人数[TODO 待业务确认] |  |
| new_class_tag_id | BIGINT | 班级标签ID[TODO 待业务确认] |  |
| new_class_tag_name | VARCHAR | 班级标签名称[TODO 待业务确认] |  |

## ba_eda_annual_info_order_hdf

年课订单事实表（订单粒度，一行一笔 order_no，217 列）：年课（BAM）全订单视角的主事实表，记录单笔年课订单从支付 → 首次加微 → 进班 → 解锁首课 Level → 解锁非全额退费期 Level → 结课 → 续报的完整生命周期，并附来源 L1 归因、赛考/等考参与与结果、退费状态与转单链路。分区字段 dt（VARCHAR）。

## 粒度与选表分工
- 本表为**订单粒度**，适合单订单全生命周期追踪、cohort、时滞（date_diff）分析、来源渠道/团队归因、赛考覆盖与等考结果下钻。
- `ads_bam_annual_process_class_hdf`（年课过程运营班级宽表）是**班级组粒度**聚合过程表（一行一个 user_class_group_id，含赛考报名 / Level / 解锁 / 完课计数与分日续报）。班级组维度的过程转化计数、班主任与续报员人效走班级宽表；订单级明细与来源归因走本表。
- 两表粒度不同，**不要 JOIN 后混用口径**；同一指标两表都能算时，先与用户确认以哪张为准。

## 四时点前缀族（高频误用点）
同一批「学期 / 班级 / 老师 / 运营类型 / 续报起止时间 / 班级标签 / 大区 / 学部领导」维度，按四个业务时点各有一整套前缀字段，选错前缀等于换了时点口径：
- `fst_term_*` —— 首次进班学期时点（fst_term_id / fst_term_name / fst_term_counselor_name / fst_term_class_id / fst_term_prrof_name / fst_term_new_class_tag_name 等）
- `fstlvl_*`（含 `fst_level_*`）—— 解锁首课 Level 时点
- `fullvl_*`（含 `full_level_*`）—— 解锁非全额退费期 Level 时点
- `renewal_*` —— 续报开始时点（renewal_start_term_id / renewal_counselor_name / renewal_staff_name / renewal_counselor_group_city 等）

默认取用建议：进班与开班归因用 `fst_term_*`；续报员与续报期人效归因用 `renewal_*`（续报开始时在班的老师，配合 is_renewal_start_inclass）；退费风险口径用 `fullvl_*`。跨时点混用须向用户说明。

## 两套来源 L1 归因字段（高频误用点）
- `l1_*`（l1_order_no / l1_pay_time / l1_sku / l1_business_line / l1_class_type / l1_user_group / l1_goal_mkt_group / l1_goal_channel_type / l1_goal_operation_group / l1_goal_class_tag / l1_city_level / l1_pay_grade / l1_renewal_channel_type / l1_term_id / l1_term_name 等）：口径为「该年课订单对应用户的**所有**续报编程年课的 L1 订单中、支付时间早于本年课订单」的来源。
- `l1_order_*`（l1_order_order_no / l1_order_pay_time / l1_order_sku / l1_order_goal_operation_group / l1_order_term_id 等）：口径为「与本年课订单号**直接对应**的那笔 L1 订单」。

两套字段同名后缀并存，混用会导致来源渠道 / 定标团队归因错位，取数前必须先确认用哪套。另有 `ps_pre_package_group` / `ps_pre_super_category_name` / `ps_pre_annual_course_name` 三级生源来源，以及 l1_channel_type_name / l1_subtype_name（来源渠道与子渠道）。「团队」类维度对应 `l1_goal_operation_group`（定标团队），不要用 l1_goal_mkt_group（定标核算组）。

## 转化链路标记（BIGINT 0/1）
is_inclass（进班）→ is_fst_add_wx（首次加微）→ is_fst_level_unlock（解锁首课 Level）→ is_full_level_unlock（解锁非全额退费期 Level）→ is_close_class（已结课，由 class_course_progress=3 判定）→ is_renewal_start_inclass（续报开始时在班）→ is_renewal（是否续报）。续报期口径另有 is_renewal_in_period（密集续报期内续报，提前续报也归入）与 is_renewal_in_day11（同上，商分口径）。

## 时滞字段
fst_term_inclass_date_diff（支付→进班天数，有退费时为支付→退费）及分桶 _bins；fst_level_unlock_date_diff（支付→开课，有退费时为退费→开课）及分桶；pay_faw_date_diff（支付→首次加微）；faw_fstlvl_date_diff（首次加微→首课解锁）；fst_renewal_datediff / lst_renewal_datediff（班级续报开始时间与年课续报时间之差，日级）。

## 续报结果
renewal_cnt（累计续报订单数）、fst_renewal_time / lst_renewal_time（首/末次续报支付时间）、fst_renewal_order_no、renewal_but_refund_cnt（该订单续报的年课订单中的退费订单数）、annual_renewal_course_name / annual_renewal_course_type（首次续报的年课课程）、annual_renewal_grade（续报时年级）。
annual_renewal_order_no_concat / annual_renewal_pay_time_concat 是**多值拼接串**（该订单续报的所有年课订单号 / 支付时间），需 split 后再计数，不能当单值直接聚合。

## 赛考 / 等考（含退费与不含退费两套）
- 含退费口径：is_level（等考报名，YCL/CTL 任一）、is_contest（比赛报名）、is_gesp、is_saikao_cover（赛考覆盖，不含预报名、含 GESP）、is_saikao_cover_all（含预报名）。
- 不含退费口径：is_paid_level / is_paid_contest / is_paid_gesp / is_paid_saikao_cover / is_paid_saikao_cover_all。
- 另有 is_pre_contest（预报名 AIIC/JMNL）、is_region（区域赛，IRC/MSSC/SFSJ/GDQK 任一）。
- 结果字段：level_attend（等考参赛）、level_result（仅区分是否通过）、level_result_type（0 未知/未作答/不予评价，1 未通过，2 通过，3 良好，4 优秀）、gesp_result（1 通过 0 未通过）；一个学期多次考试取最优一次。
- 口径选择：对外业绩与覆盖率类默认用不含退费的 is_paid_* 系列，报名意愿分析用含退费系列，输出时须声明用的哪套。

## 转单与去重（务必显式处理）
is_transfer（本单由其它订单转单而来）、is_transfer_origin（本单会转单为其它订单）、transfer_after_order_no（转单后订单号）、transfer_after_order_no_pay_time。做订单量 / 续报率统计前必须与用户确认转单单据的取舍，否则同一用户会被重复计数。

## 订单来源与销售
order_source_category（直售 / L1学期续年 / 年续年）、direct_sale_team（直售分团队）、annual_course_source_new（年课生源新）、source_order_sequence（生源订单序列）。

## 用户与课程画像
child_gender、child_age（续报开始时年龄）、pay_child_age（支付时年龄）、annual_pay_grade（年课支付年级）、annual_renewal_grade、annual_city_level、offen_login_platform（常用登录客户端平台）；课程侧 annual_course_name / annual_course_type / subject_type / package_course_type / super_category_name / unit_template_name / is_morton。

## 未进班预分配学期
未进班订单的预分配学期维度走 store_* 族：store_term_id / store_term / store_user_group_tag_name / store_parent_operation_type_name / store_course_mode_tag_name / store_term_operation_type_tag_name。

## 类型与陷阱
- **本表不含任何金额字段**：全表仅 BIGINT 与 VARCHAR 两种类型，无支付金额 / GMV / 退费金额列（退费侧仅 refund_time「申请退费且已退费」的时间与 refund_stage 退费状态）。GMV、客单价、退费金额类诉求不能用本表，需先与用户确认改用哪张含金额的表。
- 日期 / 时间字段全部为 VARCHAR（含 pay_time、各 *_time、dt 分区），过滤用单引号字符串；需要日期运算时先显式转换。
- 0/1 标记多为 BIGINT（过滤写 `= 1`，不加引号）；但少数为 VARCHAR，须带引号 `'1'`：is_contest、class_cnt（续报开始时老师是否带多班）、mid_throw_class（是否甩班）、renewal_times（累计续报学期次数）。照搬 BIGINT 写法会得到空结果。
- fst_term_child_grade 已废弃，勿用作年级维度（改用 annual_pay_grade / l1_pay_grade）。
- class_course_progress：0 未知、1 未开课、2 开课中、3 已结课。

| 字段 | 类型 | 中文名 | 口径 |
|---|---|---|---|
| order_no | VARCHAR | 订单编号 |  |
| is_transfer | BIGINT | 是否为转单后的订单,1代表是,即该订单是由其它订单转单而来 |  |
| is_transfer_origin | BIGINT | 是否为转单前的原始订单,1代表是,即该订单会转单为其它订单 |  |
| transfer_after_order_no | VARCHAR | 转单后订单号 |  |
| user_id | VARCHAR | 用户ID |  |
| child_gender | VARCHAR | 孩子性别 |  |
| pay_time | VARCHAR | 订单支付时间 |  |
| pay_month | VARCHAR | 订单支付月份 |  |
| annual_course_name | VARCHAR | 年课课程名称 |  |
| is_morton | BIGINT | 是否莫顿 |  |
| super_category_name | VARCHAR | 适用方向大分类名称 |  |
| is_inclass | BIGINT | 是否进班 |  |
| fst_term_inclass_time | VARCHAR | 首次学期进班时间 |  |
| fst_term_inclass_date_diff | BIGINT | 支付到进班天数差,当有退费时是支付到退费天数差 |  |
| fst_term_inclass_date_diff_bins | VARCHAR | 支付到进班天数差分桶,当有退费时是支付到退费天数差分桶 |  |
| fst_term_id | BIGINT | 首次进班学期id |  |
| fst_term_name | VARCHAR | 首次进班学期名称 |  |
| fst_term_year | VARCHAR | 首次进班学期年份 |  |
| fst_term_season | VARCHAR | 首次进班学期季节 |  |
| fst_term_class_id | BIGINT | 首次进班学期的班级id |  |
| fst_term_classopen_staff_id | BIGINT | 首次进班学期开班老师员工id |  |
| fst_term_classopen_staff_name | VARCHAR | 首次进班学期开班时老师真名 |  |
| fst_term_counselor_id | BIGINT | 首次进班学期老师id |  |
| fst_term_counselor_name | VARCHAR | 首次进班学期老师名称 |  |
| fst_term_counselor_group_city | VARCHAR | 首次进班学期老师所在组基地 |  |
| fst_term_technology_spec_type | VARCHAR | 首次进班学期当前期次：科特/非科特 |  |
| fst_term_class_type | VARCHAR | 首次进班学期当前期次：类型  常规/班主任 |  |
| is_fst_level_unlock | BIGINT | 是否解锁首课level |  |
| fst_level_unlock_term_id | BIGINT | 解锁首课level时所在学期id |  |
| fst_level_unlock_term_name | VARCHAR | 解锁首课level时所在学期名称 |  |
| fst_level_unlock_time | VARCHAR | 解锁首课level的时间 |  |
| fst_level_unlock_date_diff | BIGINT | 支付到开课天数差,当有退费时是退费到开课天数差 |  |
| fst_level_unlock_date_diff_bins | VARCHAR | 支付到开课天数差分桶,当有退费时是退费到开课天数差分桶 |  |
| fstlvl_term_year | VARCHAR | 解锁首课level时所在学期年份 |  |
| fstlvl_term_season | VARCHAR | 解锁首课level时所在学期季节 |  |
| fstlvl_class_id | BIGINT | 解锁首课level时所在班级id |  |
| fstlvl_classopen_staff_id | BIGINT | 解锁首课level时开班老师员工id |  |
| fstlvl_classopen_staff_name | VARCHAR | 解锁首课level时开班老师真名 |  |
| fstlvl_counselor_id | BIGINT | 解锁首课level时老师id |  |
| fstlvl_counselor_name | VARCHAR | 解锁首课level时老师名称 |  |
| fstlvl_counselor_group_city | VARCHAR | 解锁首课level时老师所在组基地 |  |
| is_close_class | BIGINT | 是否已结课:1是0否,根据class_course_progress=3判断 |  |
| class_course_progress | BIGINT | 班级课程状态:0未知1未开课2开课中3已结课 |  |
| last_unit_unlocked_time | VARCHAR | 班级:最后一节unit解锁时间 原始字段-last_class_time |  |
| class_close_time | VARCHAR | 针对结课标识class_course_progress=3的班级结课时间:编程（最后一节unit解锁时间+7天）、数学（最后一节unit解锁时间） |  |
| fstlvl_technology_spec_type | VARCHAR | 解锁首课level时学期当前期次：科特/非科特 |  |
| fstlvl_class_type | VARCHAR | 首解锁首课level时学期当前期次：类型  常规/班主任 |  |
| is_full_level_unlock | BIGINT | 是否解锁非全额退费期level |  |
| full_level_unlock_term_id | BIGINT | 解锁非全额退费期level时所在学期id |  |
| full_level_unlock_term_name | VARCHAR | 解锁非全额退费期level时所在学期名称 |  |
| full_level_unlock_time | VARCHAR | 解锁非全额退费期level的时间 |  |
| full_level_term_year | BIGINT | 解锁非全额退费期level时所在学期年份 |  |
| full_level_term_season | VARCHAR | 解锁非全额退费期level时所在学期季节 |  |
| fullvl_class_id | BIGINT | 解锁非全额退费期level时所在班级id |  |
| fullvl_classopen_staff_id | BIGINT | 解锁非全额退费期level时开班老师员工id |  |
| fullvl_classopen_staff_name | VARCHAR | 解锁非全额退费期level时开班老师真名 |  |
| fullvl_counselor_id | BIGINT | 解锁非全额退费期level时老师id |  |
| fullvl_counselor_name | VARCHAR | 解锁非全额退费期level时老师名称 |  |
| fullvl_counselor_group_city | VARCHAR | 解锁非全额退费期level时老师所在组基地 |  |
| fullvl_technology_spec_type | VARCHAR | 解锁非全额退费期level时学期当前期次：科特/非科特 |  |
| fullvl_class_type | VARCHAR | 解锁非全额退费期level时学期当前期次：类型  常规/班主任 |  |
| is_renewal_start_inclass | BIGINT | 是否续报开始时在班 |  |
| renewal_start_term_id | BIGINT | 续报开始时所在学期id,如果有多个学期,按学期开始续报时间取最晚记录 |  |
| renewal_start_term_name | VARCHAR | 续报开始时所在学期名称 |  |
| renewal_parent_operation_type_name | VARCHAR | 续报开始时年课父节点运营类型名称 |  |
| renewal_operation_type_name | VARCHAR | 续报开始时年课运营类型名称 |  |
| renewal_term_year | BIGINT | 续报开始时所在学期年份 |  |
| renewal_term_season | VARCHAR | 续报开始时所在学期季节 |  |
| renewal_class_id | BIGINT | 续报开始时所在学期的班级id |  |
| renewal_start_time | VARCHAR | 续报开始时间,限制续报时间在来源订单首次进班时间之后 |  |
| renewal_start_month | VARCHAR | 续报开始月份 |  |
| renewal_staff_id | BIGINT | 续报老师员工id |  |
| renewal_staff_name | VARCHAR | 续报老师真名 |  |
| renewal_counselor_id | BIGINT | 续报老师id |  |
| renewal_counselor_name | VARCHAR | 续报老师名称 |  |
| renewal_staff_employee_no | VARCHAR | 续报老师工号 |  |
| renewal_counselor_group_id | BIGINT | 续报开始时老师所在组id |  |
| renewal_counselor_group_city | VARCHAR | 续报开始时老师所在组基地,续报开始时不在班的为NA |  |
| renewal_times | VARCHAR | 续报开始时老师参与续报次数,截止当前累计续报学期次数(学期续报开始时间去重计数) |  |
| class_cnt | VARCHAR | 续报开始时该学期老师是否带多班,1是0否 |  |
| mid_throw_class | VARCHAR | 续报开始时该学期老师是否甩班,1是0否 |  |
| prrof_name | VARCHAR | 续报开始时老师所在大区 |  |
| renewal_technology_spec_type | VARCHAR | 续报开始时学期当前期次：科特/非科特 |  |
| renewal_class_type | VARCHAR | 续报开始时学期当前期次：类型  常规/班主任 |  |
| child_age | VARCHAR | 续报开始时用户年龄 |  |
| ps_pre_package_group | VARCHAR | 年课一级来源课程组:编程L1课,编程年课;取该年课订单对应的用户,这个用户的所有S低&S高&算法&硬件&L1订单,且这些订单的支付时间在该年课支付时间之前的最后一单 |  |
| ps_pre_super_category_name | VARCHAR | 年课二级来源:年课为一周两课非一周两课,L1课为业务线;同上 |  |
| ps_pre_annual_course_name | VARCHAR | 年课三级来源:年课为学科名称,L1课为续报渠道类型;同上 |  |
| l1_business_line | VARCHAR | 来源l1业务线:取该年课订单对应的用户,这个用户的所有续报编程年课的L1订单,且L1订单的支付时间在该年课支付时间之前的最后一单 |  |
| l1_class_type | VARCHAR | 来源l1班型:同上 |  |
| l1_user_group | VARCHAR | 来源l1人群:同上 |  |
| l1_goal_mkt_group | VARCHAR | 来源l1定标核算组:同上 |  |
| l1_goal_channel_type | VARCHAR | 来源l1定标渠道类型:同上 |  |
| l1_goal_operation_group | VARCHAR | 来源l1定标团队:同上 |  |
| l1_city_level | VARCHAR | 来源l1城市等级:同上 |  |
| l1_pay_grade | VARCHAR | 来源l1年级:同上 |  |
| l1_renewal_channel_type | VARCHAR | 来源l1续报渠道类型:同上 |  |
| term_renewal_start_time_c | VARCHAR | 来源l1学期续报开始时间:同上 |  |
| term_renewal_end_time_c | VARCHAR | 来源l1学期续报结束时间:同上 |  |
| min_renewal_time | VARCHAR | 来源l1最早续报时间:同上 |  |
| l1_renew_time_status | VARCHAR | l1续报时间状态:同上 |  |
| annual_city_level | VARCHAR | 年课城市等级 |  |
| is_renewal | BIGINT | 是否续报 |  |
| is_renewal_in_period | BIGINT | 是否在密集续报期内续报年课,包含提前续报的数据也归属到密集续报期 |  |
| is_renewal_in_day11 | BIGINT | 是否在密集续报期内续报年课（商分口径),包含提前续报的数据也归属到密集续报期 |  |
| renewal_cnt | BIGINT | 累计续报订单数 |  |
| renewal_but_refund_cnt | BIGINT | 该订单续报的年课订单的退费订单数 |  |
| fst_renewal_time | VARCHAR | 首次续报订单的支付时间 |  |
| fst_renewal_datediff | BIGINT | （首次）班级中的续报开始时间和年课续报时间的时间差(日级别) |  |
| lst_renewal_time | VARCHAR | 末次续报订单的支付时间 |  |
| lst_renewal_datediff | BIGINT | （末次）班级中的续报开始时间和年课续报时间的时间差(日级别) |  |
| refund_time | VARCHAR | 申请退费时间,申请退费且已经退费 |  |
| refund_stage | VARCHAR | 退费状态 |  |
| annual_course_type | VARCHAR | 年课课程类型 |  |
| fst_term_prrof_name | VARCHAR | 首次进班学期老师所在大区 |  |
| l1_order_order_no | VARCHAR | 来源l1订单编号:年课订单编号对应的l1订单编号 |  |
| l1_order_user_group | VARCHAR | 来源l1人群:年课订单编号对应的l1订单编号 |  |
| annual_course_source_new | VARCHAR | 年课生源新(当前仅为S低S高算法Python1234) |  |
| fst_renewal_order_no | VARCHAR | 该订单续报的所有年课订单中,最小的交易订单号 |  |
| unit_template_name | VARCHAR | 课程树模板名称（对应编程的course_tree_template_name |  |
| transfer_after_order_no_pay_time | VARCHAR | 转单后订单的支付时间 |  |
| suspend_ctime | VARCHAR | 停课创建时间 |  |
| is_fst_add_wx | BIGINT | 是否首次加微 |  |
| fst_add_wx_time | VARCHAR | 首次加微时间 |  |
| pay_faw_date_diff | BIGINT | 支付到首次加微时间差 |  |
| faw_fstlvl_date_diff | BIGINT | 首次加微到首课解锁时间差 |  |
| fst_term_renewal_start_time | VARCHAR | 首次学期续报开始时间 |  |
| fst_term_renewal_start_month | VARCHAR | 首次学期续报开始月份 |  |
| fstlvl_renewal_start_time | VARCHAR | 解锁首课level时所在学期续报开始时间 |  |
| fstlvl_renewal_start_month | VARCHAR | 解锁首课level时所在学期续报开始月份 |  |
| fullvl_renewal_start_time | VARCHAR | 解锁非全额退费期level时所在学期续报开始时间 |  |
| fullvl_renewal_start_month | VARCHAR | 解锁非全额退费期level时所在学期续报开始月份 |  |
| fst_term_parent_operation_type_name | VARCHAR | 首次进班学期年课父节点运营类型名称 |  |
| fst_term_operation_type_name | VARCHAR | 首次进班学期年课运营类型名称 |  |
| fstlvl_parent_operation_type_name | VARCHAR | 解锁首课level时年课父节点运营类型名称 |  |
| fstlvl_operation_type_name | VARCHAR | 解锁首课level时学期年课运营类型名称 |  |
| fullvl_parent_operation_type_name | VARCHAR | 解锁非全额退费期level时学期年课父节点运营类型名称 |  |
| fullvl_operation_type_name | VARCHAR | 解锁非全额退费期level时学期年课运营类型名称 |  |
| fst_term_renewal_end_time | VARCHAR | 首次学期续报结束时间 |  |
| fst_term_renewal_end_month | VARCHAR | 首次学期续报结束月份 |  |
| fstlvl_renewal_end_time | VARCHAR | 解锁首课level时所在学期续报结束时间 |  |
| fstlvl_renewal_end_month | VARCHAR | 解锁首课level时所在学期续报结束月份 |  |
| fullvl_renewal_end_time | VARCHAR | 解锁非全额退费期level时所在学期续报结束时间 |  |
| fullvl_renewal_end_month | VARCHAR | 解锁非全额退费期level时所在学期续报结束月份 |  |
| fstlvl_prrof_name | VARCHAR | 解锁首课level时老师所在大区 |  |
| fullvl_prrof_name | VARCHAR | 解锁非全额退费期level时老师所在大区 |  |
| source_order_sequence | VARCHAR | 生源订单序列 |  |
| l1_order_pay_time | VARCHAR | 来源l1支付时间:年课订单编号对应的l1订单编号 |  |
| l1_order_business_line | VARCHAR | 来源l1业务线:年课订单编号对应的l1订单编号 |  |
| l1_order_sku | VARCHAR | 来源l1SKU:年课订单编号对应的l1订单编号 |  |
| l1_order_class_type | VARCHAR | 来源l1班型:年课订单编号对应的l1订单编号 |  |
| l1_order_goal_mkt_group | VARCHAR | 来源l1定标核算组:年课订单编号对应的l1订单编号 |  |
| l1_order_goal_channel_type | VARCHAR | 来源l1定标渠道类型:年课订单编号对应的l1订单编号 |  |
| l1_order_goal_operation_group | VARCHAR | 来源l1定标团队:年课订单编号对应的l1订单编号 |  |
| l1_order_city_level | VARCHAR | 来源l1城市等级:年课订单编号对应的l1订单编号 |  |
| l1_order_pay_grade | VARCHAR | 来源l1年级:年课订单编号对应的l1订单编号 |  |
| l1_order_renewal_channel_type | VARCHAR | 来源l1续报渠道类型:年课订单编号对应的l1订单编号 |  |
| l1_order_term_renewal_start_time_c | VARCHAR | 来源l1学期续报开始时间:年课订单编号对应的l1订单编号 |  |
| l1_order_term_renewal_end_time_c | VARCHAR | 来源l1学期续报结束时间:年课订单编号对应的l1订单编号 |  |
| l1_order_min_renewal_time | VARCHAR | 来源l1最早续报时间:年课订单编号对应的l1订单编号 |  |
| l1_order_renew_time_status | VARCHAR | l1续报时间状态:年课订单编号对应的l1订单编号 |  |
| l1_order_no | VARCHAR | 来源l1订单编号:取该年课订单对应的用户,这个用户的所有续报编程年课的L1订单,且L1订单的支付时间在该年课支付时间之前的最后一单 |  |
| l1_pay_time | VARCHAR | 来源l1支付时间:取该年课订单对应的用户,这个用户的所有续报编程年课的L1订单,且L1订单的支付时间在该年课支付时间之前的最后一单 |  |
| l1_sku | VARCHAR | 来源l1SKU:取该年课订单对应的用户,这个用户的所有续报编程年课的L1订单,且L1订单的支付时间在该年课支付时间之前的最后一单 |  |
| l1_goal_class_tag | VARCHAR | 来源l1定标班型:取该年课订单对应的用户,这个用户的所有续报编程年课的L1订单,且L1订单的支付时间在该年课支付时间之前的最后一单 |  |
| l1_order_goal_class_tag | VARCHAR | 来源l1定标班型:年课订单编号对应的l1订单编号 |  |
| package_course_type | VARCHAR | 课程类型:Standard,Script,Python,C++,Math,ScratchJr |  |
| fst_term_child_grade | VARCHAR | 首次进班学期用户年级(该字段已废弃) |  |
| l1_order_term_id | BIGINT | 来源l1学期id:年课订单编号对应的l1订单编号 |  |
| l1_order_term_name | VARCHAR | 来源l1学期名称:年课订单编号对应的l1订单编号 |  |
| l1_term_id | BIGINT | 来源l1学期id |  |
| l1_term_name | VARCHAR | 来源l1学期名称 |  |
| term_start_time | VARCHAR | 年课订单的首次开课时间 |  |
| l1_channel_type_name | VARCHAR | 来源l1的渠道 |  |
| l1_subtype_name | VARCHAR | 来源l1的子渠道 |  |
| fst_term_new_class_tag_name | VARCHAR | 首次进班学期班级标签 |  |
| fstlvl_new_class_tag_name | VARCHAR | 解锁首课level时班级标签 |  |
| fullvl_new_class_tag_name | VARCHAR | 解锁非全额退费期level班级标签 |  |
| renewal_new_class_tag_name | VARCHAR | 续报开始时班级标签 |  |
| order_source_category | VARCHAR | 订单来源类别：直售/L1学期续年/年续年 |  |
| direct_sale_team | VARCHAR | 直售分团队 |  |
| subject_type | VARCHAR | 科目类型:C++,Math,Python,ScratchJr,Script,Standard,Go,Write,Piano;来源于dwd_trade_order_hdf |  |
| annual_renewal_course_name | VARCHAR | 该年课订单首次续报的年课课程名称 |  |
| annual_renewal_course_type | VARCHAR | 该年课订单首次续报的年课课程类型 |  |
| offen_login_platform | VARCHAR | 常用登录客户端平台 |  |
| fst_term_counselor_xuebu_leader_real_name | VARCHAR | 首次进班学期时学部领导真名 |  |
| fstlvl_counselor_xuebu_leader_real_name | VARCHAR | 解锁首课level时学部领导真名 |  |
| fullvl_counselor_xuebu_leader_real_name | VARCHAR | 解锁非全额退费期level时学部领导真名 |  |
| renewal_counselor_xuebu_leader_real_name | VARCHAR | 续报开始时学部领导真名 |  |
| store_term_id | BIGINT | 未进班时预先分配的学期id |  |
| store_term | VARCHAR | 未进班时预先分配的学期名称 |  |
| annual_renewal_order_no_concat | VARCHAR | 续报订单号(续报的所有年课订单号) |  |
| annual_renewal_pay_time_concat | VARCHAR | 续报时间(续报的所有年课订单支付时间) |  |
| store_user_group_tag_name | VARCHAR | 未进班时预先分配的学期 用户群体标签名称 |  |
| store_parent_operation_type_name | VARCHAR | 未进班时预先分配的学期 父节点运营类型名称 |  |
| store_course_mode_tag_name | VARCHAR | 未进班时预先分配的学期 课程模式标签名称 |  |
| store_term_operation_type_tag_name | VARCHAR | 未进班时预先分配的学期 运营类型标签名称 |  |
| pay_child_age | VARCHAR | 支付年课订单时用户年龄 |  |
| annual_pay_grade | VARCHAR | 年课支付年级(年课支付时间-来源用户L1支付时间) |  |
| annual_renewal_grade | VARCHAR | 年课续报时年级(年课续报时间-来源用户L1支付时间,无续报时间时取当前时间) |  |
| is_level | BIGINT | 是否报名等考(含退费):1是0否;(报名YCL/CTL任一) |  |
| is_contest | VARCHAR | 是否报名比赛(含退费;免费赛事取确认):1是0否;(报名STEMA/ICC/AILD/TURING/roborave/CAMH/HSWH任一) |  |
| is_region | BIGINT | 是否报名区域赛(取确认):1是0否;(报名IRC/MSSC/SFSJ/GDQK任一) |  |
| is_saikao_cover | BIGINT | 是否赛考覆盖(含退费):1是0否;报名任一赛事(不含预报名,含GESP) |  |
| level_result_type | BIGINT | 等考结果(区分各通过类型):0未知/未作答/不予评价;1未通过;2通过;3良好;4优秀;(若用户一个学期多次考试，取结果最优的一次) |  |
| level_result | BIGINT | 等考结果(只区分是否通过);(若用户一个学期多次考试，取结果最优的一次) |  |
| level_attend | BIGINT | 等考参赛;(若用户一个学期多次考试，参赛一次即记为参赛) |  |
| is_paid_level | BIGINT | 是否报名等考(不含退费):1是0否;(报名YCL/CTL任一) |  |
| is_paid_contest | BIGINT | 是否报名比赛(不含退费;免费赛事取确认):1是0否;(报名STEMA/ICC/AILD/TURING/roborave/CAMH/HSWH任一) |  |
| is_paid_saikao_cover | BIGINT | 是否赛考覆盖(不含退费):1是0否;(报名任一赛事,不含预报名,含GESP) |  |
| is_pre_contest | BIGINT | 是否预报名比赛(预报名没有退费):1是0否;预报名AIIC/JMNL |  |
| is_gesp | BIGINT | 是否报名GESP(含退费):1是0否;报名GESP |  |
| is_saikao_cover_all | BIGINT | 是否赛考覆盖(含退费,含预报名):1是0否;报名任一赛事(含预报名,含GESP) |  |
| gesp_result | BIGINT | GESP结果1通过0未通过若用户一个学期多次考试,取结果最优的一次 |  |
| is_paid_gesp | BIGINT | 是否报名GESP(不含退费):1是0否;报名GESP |  |
| is_paid_saikao_cover_all | BIGINT | 是否赛考覆盖(不含退费,含预报名):1是0否;报名任一赛事(含预报名,含GESP) |  |
| dt | VARCHAR | 分区字段 |  |

## ads_bam_annual_ue_refund_hdf

年课（BAM）UE（单位经济）测算宽表（学期主课路径 × 用户分层粒度，一行一个 order_rank × first_user_group × term_name × first_subject × second_subject × third_subject × fourth_subject × fifth_subject × sixth_subject 组合，16 列）：以订单序次(order_rank) 与首学期用户分层(first_user_group) 为切入，按学期名称(term_name) 与 6 段学期主课路径(first_subject / second_subject / third_subject / fourth_subject / fifth_subject / sixth_subject) 展开生命周期学习路径；核心指标覆盖支付量 pay_cnt 与进班量 enroll_cnt，以及两套续报率（renew_rate_inclass 在班分母、renew_rate_pay 支付分母）与两套退费率（refund_rate_inclass 在班分母、refund_rate_pay 支付分母），用于年课单位经济模型 UE 的续报-退费联合测算与学科路径贡献拆解。dt 为快照分区（VARCHAR，YYYYMMDD 字符串，过滤加单引号）。用于年课经营分析场景：UE 结构诊断、学科主线路径贡献、续报与退费的收益/损耗联合建模。率类指标（renew_rate_* / refund_rate_*）为已聚合数值，跨维度合计不得对行级率做算术平均，需按对应分子/分母重算；本表无金额字段，UE 中涉及金额部分需外接其它数据源。 

| 字段 | 类型 | 中文名 | 口径 |
|---|---|---|---|
| order_rank | VARCHAR | 订单序次 | 订单在用户年课续报链条中的序次标记（首单 / 二次续报 / 三次续报 ……），UE 漏斗按序次分层的关键维度 |
| first_user_group | VARCHAR | 首学期用户分层 | 用户首次进入年课学习时所属的用户分层 / 分群标签，用于按初始画像做 UE 结构对比 |
| term_name | VARCHAR | 学期名称 | 订单所属学期的显示名（例 2026 春 A2 等），配合 order_rank 描述订单所在的时间学期切片 |
| first_subject | VARCHAR | 第 1 学期主课 | 用户学习路径中第 1 学期报读的主课学科（Scratch / Python / C++ 等），UE 学习路径展开维度之一 |
| second_subject | VARCHAR | 第 2 学期主课 | 用户学习路径中第 2 学期报读的主课学科；无对应学期时为空 |
| third_subject | VARCHAR | 第 3 学期主课 | 用户学习路径中第 3 学期报读的主课学科；无对应学期时为空 |
| fourth_subject | VARCHAR | 第 4 学期主课 | 用户学习路径中第 4 学期报读的主课学科；无对应学期时为空 |
| fifth_subject | VARCHAR | 第 5 学期主课 | 用户学习路径中第 5 学期报读的主课学科；无对应学期时为空 |
| sixth_subject | VARCHAR | 第 6 学期主课 | 用户学习路径中第 6 学期报读的主课学科；无对应学期时为空 |
| pay_cnt | BIGINT | 支付订单数 | 该维度组合下的年课支付订单数（含退费），UE 计算分母之一 |
| enroll_cnt | BIGINT | 进班订单数 | 该维度组合下的年课进班订单数，UE 在班口径的分母 |
| renew_rate_inclass | DECIMAL(20,6) | 在班续报率 | 续报订单数 / 进班订单数（enroll_cnt 为分母的续报率），UE 在班口径核心指标；率为已聚合值，跨维度合计须按分子分母重算 |
| renew_rate_pay | DECIMAL(20,6) | 支付续报率 | 续报订单数 / 支付订单数（pay_cnt 为分母的续报率），UE 支付口径核心指标；率为已聚合值，跨维度合计须按分子分母重算 |
| refund_rate_inclass | DECIMAL(20,6) | 在班退费率 | 退费订单数 / 进班订单数（enroll_cnt 为分母的退费率），UE 在班口径损耗指标；率为已聚合值，跨维度合计须按分子分母重算 |
| refund_rate_pay | DECIMAL(20,6) | 支付退费率 | 退费订单数 / 支付订单数（pay_cnt 为分母的退费率），UE 支付口径损耗指标；率为已聚合值，跨维度合计须按分子分母重算 |
| dt | VARCHAR | 快照分区 | 宽表快照分区（YYYYMMDD 字符串），过滤时用单引号包裹 |

## ba_bam_annual_contest_class_hdf

年课（BAM）contest 相关订单明细宽表（订单粒度，一行一个 order_no，共 88 列）：从命名推断为围绕参赛 / contest 场景的年课订单切片；但表内没有显式的 contest / saikao / level_result 等赛考等考字段，列结构与年课订单事实表 ba_eda_annual_info_order_hdf 高度重叠——推测行已按参赛用户 / 特定活动预筛，列不带 contest 标记（需业务同学复核）。字段涵盖：订单基础(order_no / user_id / pay_time / annual_course_name)、进班标记(is_inclass)、四时点前缀族(fst_term_* 首次进班 / fstlvl_* 首课 Level 解锁 / renewal_* 续报开始)、续报口径(is_renewal / is_renewal_in_period / is_renewal_in_day11)、来源 L1 归因(l1_* 系列 5 列)、订单来源(order_source_category / direct_sale_team / is_direct_sale / direct_sale_type)、转单链路(is_transfer / origin_* / last_* / transfer_origin_* / transfer_to_*)、预售(is_pre_order / pre_*)、退费(refund_stage)、用户来源(user_source)。日期字段全部 VARCHAR，dt 为快照分区。类型陷阱：is_transfer / is_inclass / is_renewal / is_morton 等为 BIGINT（过滤写 = 1），is_transfer_order / is_direct_sale / is_pre_order 为 INTEGER；fst_term_child_grade 已废弃勿用作年级维度。适用场景需业务侧确认后固定。

| 字段 | 类型 | 中文名 | 口径 |
|---|---|---|---|
| order_no | VARCHAR | 订单号 | 年课订单唯一编号，本表主键（一行一笔 order_no） |
| is_transfer | BIGINT | 是否转单进来 | 本单是否由其它订单转单而来，0/1 BIGINT |
| is_transfer_origin | BIGINT | 是否转单出去 | 本单是否已转单为其它订单，0/1 BIGINT |
| transfer_after_order_no | VARCHAR | 转单后订单号 | 本单转单后对应的目标订单号 |
| user_id | BIGINT | 用户 ID | 下单用户在核桃系统中的唯一 ID |
| child_gender | VARCHAR | 孩子性别 | 订单对应孩子的性别（男/女/未知） |
| pay_time | VARCHAR | 支付时间 | 本单支付完成时间，VARCHAR，过滤加单引号 |
| pay_month | VARCHAR | 支付月 | 支付时间所属月份（YYYYMM 或 YYYY-MM） |
| annual_course_name | VARCHAR | 年课名称 | 本单对应的年课课程名 |
| is_morton | BIGINT | 是否莫顿 | 是否莫顿系课程，0/1 BIGINT |
| super_category_name | VARCHAR | 大品类名称 | 课程所属大品类（编程 / 数学思维 等） |
| is_inclass | BIGINT | 是否进班 | 本单是否进班，0/1 BIGINT |
| fst_term_id | BIGINT | 首次进班学期 ID | 四时点前缀族 fst_term_*：用户首次进班时的学期 ID |
| fst_term_name | VARCHAR | 首次进班学期名 | 四时点前缀族 fst_term_*：用户首次进班时的学期名 |
| fst_term_class_type | VARCHAR | 首次进班班级类型 | 四时点前缀族 fst_term_*：首次进班的班级类型 |
| fst_level_unlock_term_id | BIGINT | 首课 Level 解锁学期 ID | 四时点前缀族 fstlvl_*：解锁首课 Level 时所属学期 ID |
| fst_level_unlock_term_name | VARCHAR | 首课 Level 解锁学期名 | 四时点前缀族 fstlvl_*：解锁首课 Level 时所属学期名 |
| fst_level_unlock_time | VARCHAR | 首课 Level 解锁时间 | 解锁首课 Level 的时间，VARCHAR |
| fstlvl_class_id | BIGINT | 首课 Level 班级 ID | 四时点前缀族 fstlvl_*：解锁首课 Level 时对应班级 ID |
| fstlvl_technology_spec_type | VARCHAR | 首课 Level 技术规格类型 | 首课 Level 班级的技术规格类型（直播 / 录播 / AI 等） |
| fstlvl_class_type | VARCHAR | 首课 Level 班级类型 | 首课 Level 班级的班级类型 |
| is_renewal_start_inclass | BIGINT | 续报开始时是否在班 | 续报开始时刻用户是否仍在班，0/1 BIGINT，用于续报员归因的关键筛选 |
| renewal_start_term_id | BIGINT | 续报开始学期 ID | 四时点前缀族 renewal_*：续报开始时所处学期 ID |
| renewal_start_term_name | VARCHAR | 续报开始学期名 | 四时点前缀族 renewal_*：续报开始时所处学期名 |
| renewal_parent_operation_type_name | VARCHAR | 续报期父运营类型 | 续报开始时的父级运营策略类型 |
| renewal_operation_type_name | VARCHAR | 续报期运营类型 | 续报开始时的运营策略类型（普通续报 / 大促续报 等） |
| renewal_start_time | VARCHAR | 续报开始时间 | 续报开始的时间点，VARCHAR |
| renewal_class_id | BIGINT | 续报期班级 ID | 续报开始时所在班级 ID |
| renewal_technology_spec_type | VARCHAR | 续报期技术规格类型 | 续报开始时班级的技术规格类型 |
| renewal_class_type | VARCHAR | 续报期班级类型 | 续报开始时的班级类型 |
| child_age | BIGINT | 孩子年龄 | 续报开始时的孩子年龄 |
| is_renewal | BIGINT | 是否续报 | 本单用户是否发生续报，0/1 BIGINT |
| is_renewal_in_period | BIGINT | 是否密集续报期内续报 | 是否在密集续报期内续报（含提前续报），0/1 BIGINT |
| is_renewal_in_day11 | BIGINT | 是否 day11 内续报 | 商分口径：是否在开班 day11 内续报，0/1 BIGINT |
| annual_city_level | VARCHAR | 年课城市级别 | 订单所属城市线（一线 / 新一线 / 二线 等） |
| annual_course_type | VARCHAR | 年课课程类型 | 本单年课的课程类型标签 |
| package_course_type | VARCHAR | 包课程类型 | 订单打包课程类型（体验包 / 学期包 / 年包 等） |
| annual_course_source_new | VARCHAR | 年课生源新 | 年课订单生源来源分类（新口径） |
| source_order_sequence | VARCHAR | 生源订单序列 | 生源侧订单序列标记 |
| fst_term_child_grade | VARCHAR | 首次进班孩子年级（已废弃） | 四时点前缀族 fst_term_*：首次进班时孩子年级；【已废弃】年级维度改用 annual_pay_grade / l1_pay_grade |
| fst_renewal_datediff | BIGINT | 首次续报天差 | 班级续报开始时间与年课续报时间之差（日级） |
| l1_business_line | VARCHAR | 来源 L1 业务线 | 来源 L1 归因 l1_* 系列：本单对应用户所有早于本单支付的续报编程年课 L1 订单口径下的业务线 |
| l1_sku | VARCHAR | 来源 L1 SKU | 来源 L1 归因 l1_* 系列：SKU 编号 |
| l1_term_id | BIGINT | 来源 L1 学期 ID | 来源 L1 归因 l1_* 系列：L1 学期 ID |
| l1_term_name | VARCHAR | 来源 L1 学期名 | 来源 L1 归因 l1_* 系列：L1 学期名 |
| l1_pay_grade | VARCHAR | 来源 L1 支付年级 | 来源 L1 归因 l1_* 系列：L1 支付时孩子年级，年级维度推荐用此字段 |
| order_source_category | VARCHAR | 订单来源分类 | 订单来源分类（直售 / L1 学期续年 / 年续年） |
| direct_sale_team | VARCHAR | 直售分团队 | 订单归属的直售分团队（当 order_source_category=直售 时有值） |
| is_transfer_order | INTEGER | 是否转单订单（整型） | 是否属于转单订单，0/1 INTEGER |
| origin_order_no | VARCHAR | 起源订单号 | 本单转单链条中的起源订单号 |
| origin_pay_time | VARCHAR | 起源订单支付时间 | 起源订单的支付时间，VARCHAR |
| origin_super_category_name | VARCHAR | 起源订单大品类 | 起源订单的大品类名称 |
| origin_annual_course_name | VARCHAR | 起源订单年课名 | 起源订单的年课课程名 |
| origin_package_course_type | VARCHAR | 起源订单包课程类型 | 起源订单的包课程类型 |
| origin_annual_course_type | VARCHAR | 起源订单年课类型 | 起源订单的年课课程类型标签 |
| origin_annual_course_name_tag | VARCHAR | 起源订单年课名标签 | 起源订单年课名的标签化字段 |
| last_order_no | VARCHAR | 上一单订单号 | 本单前一单（前置续报关系）订单号 |
| last_pay_time | VARCHAR | 上一单支付时间 | 上一单支付时间，VARCHAR |
| last_super_category_name | VARCHAR | 上一单大品类 | 上一单大品类名 |
| last_annual_course_name | VARCHAR | 上一单年课名 | 上一单年课课程名 |
| last_package_course_type | VARCHAR | 上一单包课程类型 | 上一单的包课程类型 |
| last_annual_course_type | VARCHAR | 上一单年课类型 | 上一单的年课课程类型 |
| last_annual_course_name_tag | VARCHAR | 上一单年课名标签 | 上一单年课名的标签化字段 |
| transfer_origin_order_no | VARCHAR | 转单来源订单号 | 转单链条中的来源订单号（区分于 origin，代表直接来源） |
| transfer_origin_pay_time | VARCHAR | 转单来源订单支付时间 | 转单来源订单的支付时间 |
| transfer_origin_super_category_name | VARCHAR | 转单来源大品类 | 转单来源订单的大品类名 |
| transfer_origin_annual_course_name | VARCHAR | 转单来源年课名 | 转单来源订单的年课课程名 |
| transfer_origin_package_course_type | VARCHAR | 转单来源包课程类型 | 转单来源订单的包课程类型 |
| transfer_origin_annual_course_type | VARCHAR | 转单来源年课类型 | 转单来源订单的年课课程类型 |
| transfer_origin_annual_course_name_tag | VARCHAR | 转单来源年课名标签 | 转单来源订单年课名的标签化字段 |
| transfer_to_order_no | VARCHAR | 转单目标订单号 | 本单转单后的目标订单号（区分于 transfer_after_order_no，语义相近，可能为不同链路口径） |
| transfer_to_pay_time | VARCHAR | 转单目标订单支付时间 | 转单目标订单的支付时间 |
| transfer_to_super_category_name | VARCHAR | 转单目标大品类 | 转单目标订单的大品类名 |
| transfer_to_annual_course_name | VARCHAR | 转单目标年课名 | 转单目标订单的年课课程名 |
| transfer_to_package_course_type | VARCHAR | 转单目标包课程类型 | 转单目标订单的包课程类型 |
| transfer_to_annual_course_type | VARCHAR | 转单目标年课类型 | 转单目标订单的年课课程类型 |
| transfer_to_annual_course_name_tag | VARCHAR | 转单目标年课名标签 | 转单目标订单年课名的标签化字段 |
| is_direct_sale | INTEGER | 是否直售 | 本单是否属于直售订单，0/1 INTEGER |
| direct_sale_type | VARCHAR | 直售类型 | 直售订单的细分类型 |
| is_pre_order | INTEGER | 是否预售订单 | 本单是否为预售订单，0/1 INTEGER |
| pre_order_no | VARCHAR | 预售订单号 | 预售关联的订单号 |
| pre_pay_time | VARCHAR | 预售支付时间 | 预售订单的支付时间 |
| pre_package_group | BIGINT | 预售包组 | 预售订单所属的包组编号 |
| pre_super_category_name | VARCHAR | 预售大品类 | 预售订单的大品类名 |
| pre_annual_course_name | VARCHAR | 预售年课名 | 预售订单对应的年课课程名 |
| pre_annual_course_name_tag | VARCHAR | 预售年课名标签 | 预售年课名的标签化字段 |
| refund_stage | VARCHAR | 退费阶段 | 退费状态阶段（如全额退费期 / 非全额退费期 / 已退费 等） |
| user_source | VARCHAR | 用户来源 | 用户来源渠道标签 |
| dt | VARCHAR | 快照分区 | 宽表快照分区（YYYYMMDD 字符串，过滤加单引号） |

## ads_eda_annual_operation_info_df

年课（BAM）班主任运营指标宽表（term × 班级组 × 课程单元粒度，一行一个 term_id × user_class_group_id × course_level × unit_sequence 切片，74 列）：以学期(term_name / term_id) × 班级组(user_class_group_id) × 课程 Level(course_level / unit_sequence / course_unit) 为主键维度，展开班主任侧运营触达指标。字段涵盖：课程与班型标记(annual_course_name / technology_spec_type / class_type / new_class_tag_name)；四级组织链——基地(base_dept_*) → 大区(region_dept_*) → 学部(xuebu_*) → 班主任组(counselor_group_*)（含部门 ID / 名称 / 城市 / 负责人姓名 + 工号 + 班主任侧 ID）；过程转化——解锁 unlock_cnt、day0 / day8 / 累计 到课与完课(day0_attend/finish_cnt、day8_attend/finish_cnt、acc_attend/finish_cnt)；通话触达——企微通话 qw_call_duration / qw_call_times、系统通话 call_duration / call_times、时长阈值分档 call_30/180/300_times；企微会话与消息——qw_total_msg_cnt / qw_total_counselor_send_msg_cnt / qw_total_user_send_msg_cnt / qw_total_session_cnt / qw_valid_counselor_interact_session_cnt / qw_valid_msg_interact_cnt / qw_kete_mention_cnt / qw_contest_mention_cnt 等；学中控 xzk_times / xzk_duration；App 内 IM 触达 im_user_msg_cnt / im_teacher_msg_cnt / im_user_msg_read_cnt / im_teacher_msg_read_cnt / im_call_cnt / im_call_duration / unlock_im_call_cnt / unlock_im_call_duration；课前直播 pre_class_live_cnt / pre_class_live_watch_duration / pre_class_live_total_duration。dt 为快照分区（VARCHAR，YYYYMMDD 字符串，过滤加单引号）。用于年课经营分析场景：班主任触达 → 过程转化的因果诊断、组织维度下沉、单元级别人效对比。类型陷阱：多数 0/1 与计数指标为 BIGINT，qw_avg_session_mid_duration 为 DECIMAL；xuebu_leader_counselor_id 为 VARCHAR 与其它 counselor_id BIGINT 不同，跨表 JOIN 时注意类型统一。

| 字段 | 类型 | 中文名 | 口径 |
|---|---|---|---|
| term_name | VARCHAR | 学期名称 | 所属学期的显示名（例 2026 春 A2 等） |
| term_id | BIGINT | 学期 ID | 所属学期的唯一 ID |
| user_class_group_id | BIGINT | 班级组 ID | 本表主键维度之一：班级组 ID，一行一个 term × user_class_group_id × 课程单元切片 |
| annual_course_name | VARCHAR | 年课名称 | 班级组所属年课课程名 |
| course_level | VARCHAR | 课程 Level | 课程 Level 层级标记 |
| unit_sequence | VARCHAR | 单元序号 | 课程单元序号 |
| course_unit | VARCHAR | 课程单元 | 课程单元名称，与 unit_sequence 配合定位当前所在单元 |
| technology_spec_type | VARCHAR | 技术规格类型 | 班级技术规格类型（直播 / 录播 / AI 等） |
| class_type | VARCHAR | 班级类型 | 班级类型标签 |
| new_class_tag_name | VARCHAR | 新班级标签 | 新班级标签名（区分新老班级 / 特殊运营班型） |
| base_dept_id | BIGINT | 基地部门 ID | 组织链一级：基地部门 ID |
| base_dept_name | VARCHAR | 基地部门名称 | 组织链一级：基地部门名称 |
| base_dept_city | VARCHAR | 基地城市 | 基地部门所在城市 |
| base_leader_staff_name | VARCHAR | 基地负责人姓名 | 基地部门负责人姓名 |
| base_leader_staff_employee_no | VARCHAR | 基地负责人工号 | 基地部门负责人工号 |
| region_dept_id | BIGINT | 大区部门 ID | 组织链二级：大区部门 ID |
| region_dept_name | VARCHAR | 大区部门名称 | 组织链二级：大区部门名称 |
| region_dept_city | VARCHAR | 大区城市 | 大区部门所在城市 |
| region_leader_staff_name | VARCHAR | 大区负责人姓名 | 大区部门负责人姓名 |
| region_leader_staff_employee_no | VARCHAR | 大区负责人工号 | 大区部门负责人工号 |
| region_leader_counselor_id | BIGINT | 大区负责人班主任 ID | 大区负责人的班主任侧 ID（负责人本身是班主任身份时的 ID 映射） |
| xuebu_id | BIGINT | 学部 ID | 组织链三级：学部 ID |
| xuebu_name | VARCHAR | 学部名称 | 组织链三级：学部名称 |
| xuebu_city | VARCHAR | 学部城市 | 学部所在城市 |
| xuebu_leader_staff_name | VARCHAR | 学部负责人姓名 | 学部负责人姓名 |
| xuebu_leader_staff_employee_no | VARCHAR | 学部负责人工号 | 学部负责人工号 |
| xuebu_leader_counselor_id | VARCHAR | 学部负责人班主任 ID | 学部负责人的班主任侧 ID（VARCHAR 类型，与其它 counselor_id 略有差异） |
| counselor_group_id | BIGINT | 班主任组 ID | 组织链四级：班主任组 ID |
| counselor_group_name | VARCHAR | 班主任组名称 | 组织链四级：班主任组名称 |
| counselor_group_city | VARCHAR | 班主任组城市 | 班主任组所在城市 |
| counselor_group_leader_staff_name | VARCHAR | 班主任组负责人姓名 | 班主任组负责人姓名（组长） |
| counselor_leader_staff_employee_no | VARCHAR | 班主任组负责人工号 | 班主任组负责人工号 |
| counselor_leader_counselor_name | VARCHAR | 班主任组负责人班主任名 | 班主任组负责人的班主任侧姓名 |
| counselor_leader_counselor_id | BIGINT | 班主任组负责人班主任 ID | 班主任组负责人的班主任侧 ID |
| unlock_cnt | BIGINT | 解锁人数 | 该班级组本单元 Level 解锁人数 |
| day0_attend_cnt | BIGINT | day0 到课人数 | day0（开班当天）到课人数 |
| day0_finish_cnt | BIGINT | day0 完课人数 | day0 完课人数 |
| day8_attend_cnt | BIGINT | day8 到课人数 | day8（开班后第 8 天节点）到课人数 |
| day8_finish_cnt | BIGINT | day8 完课人数 | day8 完课人数 |
| acc_attend_cnt | BIGINT | 累计到课人数 | 本单元累计到课人数 |
| acc_finish_cnt | BIGINT | 累计完课人数 | 本单元累计完课人数 |
| qw_call_duration | BIGINT | 企微通话总时长 | 企业微信通话总时长（秒） |
| call_duration | BIGINT | 通话总时长 | 系统通话总时长（秒），含企微与其他渠道 |
| qw_call_times | BIGINT | 企微通话次数 | 企业微信通话总次数 |
| call_times | BIGINT | 通话总次数 | 系统通话总次数 |
| call_30_times | BIGINT | ≥30 秒通话次数 | 单次通话时长 ≥ 30 秒的通话次数 |
| call_180_times | BIGINT | ≥180 秒通话次数 | 单次通话时长 ≥ 180 秒的通话次数 |
| call_300_times | BIGINT | ≥300 秒通话次数 | 单次通话时长 ≥ 300 秒的通话次数（衡量深度沟通） |
| qw_avg_session_mid_duration | DECIMAL(20,6) | 企微会话中位时长（均值） | 企微单会话中位时长的均值（跨会话聚合），DECIMAL |
| qw_unit_max_counselor_reply_duration | BIGINT | 企微本单元班主任最长回复时长 | 本单元内企微班主任单次最长回复时长（秒） |
| qw_total_msg_cnt | BIGINT | 企微消息总数 | 企微发出与收到的消息总数 |
| qw_total_counselor_send_msg_cnt | BIGINT | 企微班主任发送消息数 | 班主任在企微发送的消息数 |
| qw_total_user_send_msg_cnt | BIGINT | 企微用户发送消息数 | 用户（家长）在企微发送的消息数 |
| qw_total_session_cnt | BIGINT | 企微会话总数 | 企微会话总数 |
| qw_counselor_session_cnt | BIGINT | 企微班主任会话数 | 班主任主动参与的企微会话数 |
| qw_valid_counselor_interact_session_cnt | BIGINT | 企微班主任有效互动会话数 | 班主任产生有效互动的会话数（有效性定义见平台口径） |
| qw_user_session_cnt | BIGINT | 企微用户会话数 | 用户参与的企微会话数 |
| qw_valid_msg_interact_cnt | BIGINT | 企微有效消息互动数 | 企微中有效双向消息互动次数 |
| qw_valid_msg_interact_session_cnt | BIGINT | 企微有效消息互动会话数 | 包含有效消息互动的会话数 |
| qw_kete_mention_cnt | BIGINT | 企微科特提及次数 | 企微消息中提及「科特（Scratch 相关运营主题）」的次数 |
| qw_contest_mention_cnt | BIGINT | 企微比赛提及次数 | 企微消息中提及「比赛 / contest」的次数 |
| xzk_times | BIGINT | 学中控次数 | 学中控（xzk）触发次数 |
| xzk_duration | BIGINT | 学中控总时长 | 学中控（xzk）总时长（秒） |
| im_user_msg_cnt | BIGINT | IM 用户消息数 | App 内 IM 用户发送消息数 |
| im_user_msg_read_cnt | BIGINT | IM 用户消息已读数 | App 内 IM 用户消息被班主任已读数 |
| im_teacher_msg_cnt | BIGINT | IM 班主任消息数 | App 内 IM 班主任发送消息数 |
| im_teacher_msg_read_cnt | BIGINT | IM 班主任消息已读数 | App 内 IM 班主任消息被用户已读数 |
| im_call_duration | BIGINT | IM 通话总时长 | App 内 IM 通话总时长（秒） |
| im_call_cnt | BIGINT | IM 通话次数 | App 内 IM 通话次数 |
| unlock_im_call_cnt | BIGINT | 解锁前 IM 通话次数 | 解锁 Level 前触发的 IM 通话次数 |
| unlock_im_call_duration | BIGINT | 解锁前 IM 通话时长 | 解锁 Level 前触发的 IM 通话总时长（秒） |
| pre_class_live_cnt | BIGINT | 课前直播观看人数 | 课前直播观看人数 |
| pre_class_live_watch_duration | BIGINT | 课前直播观看总时长 | 课前直播观看总时长（秒） |
| pre_class_live_total_duration | BIGINT | 课前直播总时长 | 课前直播总时长（秒，直播本身的时长基数） |
| dt | VARCHAR | 快照分区 | 宽表快照分区（YYYYMMDD 字符串，过滤加单引号） |

## man_renewal_goal_detail_hdf

年课续报目标明细表（用户分层 × 订单序次 × 学科 × 班型 × 学期 × 续报进度粒度，一行一个 user_group × order_seq × subject × class_type × term_name × renewal_progress × renewal_start_month 组合，33 列）：以运营维护（man_ 前缀）的年课续报期目标为核心，记录两套核心续报口径下的目标 vs 实际 vs 预测 vs 缺口——d2d 口径（【待业务复核】疑为 day-to-day 或某续报统计口径代号）与 L3-1 口径（【待业务复核】疑为 L3 Level 1 单元续报口径），每套口径均含 d4 / d11 续报人数与续报率、d4→d11 增长率（成熟度系数）、d11 预测续报率与预测续报人数、基线（baseline）与挑战（challenge）目标、基线缺口（预测 vs baseline）。字段涵盖：主键维度(user_group / order_seq / subject / class_type / term_name / renewal_progress / renewal_start_month)、基数(estimated_paid_users 预估付费用户数 / l3_1_unlock_users L3-1 解锁人数)、d2d 口径 8 列、L3-1 口径 9 列（含 l3_gap_baseline 命名与其它 l3_1_* 略异）、切片转出率(slice_transfer_out_rate)、Python 学科特化目标 4 列(python_d2d_baseline_goal / python_d2d_challenge_goal / python_l3_baseline_goal / python_l3_challenge_goal)。dt 为快照分区（VARCHAR）。用于年课经营分析场景：续报目标达成诊断、缺口归因、d4→d11 成熟度修正、Python 特化目标追踪。率类指标（*_renewal_rate / *_forecast_rate / *_growth / slice_transfer_out_rate）为已聚合数值，跨维度合计不得对行级率做算术平均，须按分子分母重算；用户续报进度 d4 / d11 天数节点、d2d 与 L3-1 口径的详细定义需业务同学复核。

| 字段 | 类型 | 中文名 | 口径 |
|---|---|---|---|
| user_group | VARCHAR | 用户分层 | 用户分层 / 分群标签，续报目标的主键维度之一 |
| order_seq | VARCHAR | 订单序次 | 订单在用户续报链条中的序次（首单 / 二次续报 / 三次续报 …），续报目标的主键维度之一 |
| subject | VARCHAR | 主课学科 | 学科（Scratch / Python / C++ 等），续报目标的主键维度之一 |
| class_type | VARCHAR | 班级类型 | 班级类型标签，续报目标的主键维度之一 |
| term_name | VARCHAR | 学期名称 | 目标所属学期显示名 |
| renewal_progress | VARCHAR | 续报进度阶段 | 续报进度阶段标签（如密集续报期、非密集期等），续报目标的主键维度之一 |
| renewal_start_month | VARCHAR | 续报起始月 | 续报期起始月份（YYYY-MM 或 YYYYMM） |
| estimated_paid_users | DECIMAL(20,6) | 预估付费用户数 | 该维度组合下预估付费用户数（续报期分母基数），DECIMAL 允许小数（含加权） |
| d2d_d4_renewal_users | BIGINT | d2d 口径 d4 续报人数 | d2d 口径（【待业务复核】疑为 day-to-day 或某续报统计口径代号）下 day4 续报人数（截止开班后第 4 天） |
| d2d_d11_renewal_users | BIGINT | d2d 口径 d11 续报人数 | d2d 口径下 day11 续报人数（截止开班后第 11 天） |
| d2d_d4_renewal_rate | DECIMAL(20,6) | d2d 口径 d4 续报率 | d2d 口径 d4 续报率 = d2d_d4_renewal_users / 分母基数；率为已聚合值，跨维度合计须按分子分母重算 |
| d2d_d11_forecast_rate | DECIMAL(20,6) | d2d 口径 d11 预测续报率 | d2d 口径 d11 预测续报率（基于 d4 实际 + 历史 d4→d11 修正） |
| d2d_d4_d11_growth | DECIMAL(20,6) | d2d 口径 d4→d11 增长率 | d2d 口径下 d4 到 d11 续报率增长（历史或本期的续报期成熟度系数） |
| d2d_baseline_goal | DECIMAL(20,6) | d2d 口径基线目标 | d2d 口径的基线（baseline）目标值 |
| d2d_challenge_goal | DECIMAL(20,6) | d2d 口径挑战目标 | d2d 口径的挑战（challenge）目标值 |
| d2d_gap_baseline | DECIMAL(20,6) | d2d 口径基线缺口 | d2d 口径预测值 vs 基线目标的缺口（预测 - baseline） |
| d2d_d11_forecast_renewal_users | DECIMAL(20,6) | d2d 口径 d11 预测续报人数 | d2d 口径 d11 预测续报人数 = 预估付费用户数 × d11 预测续报率 |
| l3_1_unlock_users | DECIMAL(20,6) | L3-1 解锁人数 | L3 Level 1（【待业务复核】疑为 L3 Level 1 单元）解锁人数，作为 L3-1 续报口径的分母基数 |
| l3_1_d4_renewal_users | BIGINT | L3-1 口径 d4 续报人数 | L3-1 口径下 d4 续报人数 |
| l3_1_d11_renewal_users | BIGINT | L3-1 口径 d11 续报人数 | L3-1 口径下 d11 续报人数 |
| l3_1_d4_renewal_rate | DECIMAL(20,6) | L3-1 口径 d4 续报率 | L3-1 口径 d4 续报率 = l3_1_d4_renewal_users / l3_1_unlock_users |
| l3_1_d11_forecast_rate | DECIMAL(20,6) | L3-1 口径 d11 预测续报率 | L3-1 口径 d11 预测续报率（基于 d4 实际 + 历史 d4→d11 修正） |
| l3_1_d4_d11_growth | DECIMAL(20,6) | L3-1 口径 d4→d11 增长率 | L3-1 口径 d4 到 d11 续报率增长 |
| l3_1_baseline_goal | DECIMAL(20,6) | L3-1 口径基线目标 | L3-1 口径的基线目标值 |
| l3_1_challenge_goal | DECIMAL(20,6) | L3-1 口径挑战目标 | L3-1 口径的挑战目标值 |
| l3_gap_baseline | DECIMAL(20,6) | L3 口径基线缺口 | L3 口径预测值 vs 基线目标的缺口（预测 - baseline）；注意此列名为 l3_gap_baseline 而非 l3_1_gap_baseline，与其它 l3_1_* 列命名略有差异，需业务复核是否为同一口径 |
| l3_1_forecast_renewal_users | DECIMAL(20,6) | L3-1 口径预测续报人数 | L3-1 口径 d11 预测续报人数 |
| slice_transfer_out_rate | DECIMAL(20,6) | 切片转出率 | 切片（slice）转出率，用于目标建模中修正转班 / 转出影响的分母 |
| python_d2d_baseline_goal | DECIMAL(20,6) | Python 学科 d2d 基线目标 | Python 学科特化：d2d 口径基线目标 |
| python_d2d_challenge_goal | DECIMAL(20,6) | Python 学科 d2d 挑战目标 | Python 学科特化：d2d 口径挑战目标 |
| python_l3_baseline_goal | DECIMAL(20,6) | Python 学科 L3 基线目标 | Python 学科特化：L3 口径基线目标 |
| python_l3_challenge_goal | DECIMAL(20,6) | Python 学科 L3 挑战目标 | Python 学科特化：L3 口径挑战目标 |
| dt | VARCHAR | 快照分区 | 宽表快照分区（YYYYMMDD 字符串，过滤加单引号） |

