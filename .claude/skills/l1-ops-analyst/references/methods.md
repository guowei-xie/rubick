# 分析方法

## 流入下一期成熟度判断与预测修正（mth_l1_flow_next_maturity_adjustment）

市场视角下，对「分析渠道 × 分析期次」逐期判断是否存在流入下一期导致的续报观测未成熟，对不成熟期次执行预测修正、产出 `market_sales_actual_flow_adjusted` 缓存，供续报率归因 / 渠道质量验证 / 过程转化诊断 / GMV 达成诊断等下游工作流消费。

## 判断口径（逐期，不合并）
- `流入下一期占比 = 流入下一期实际招生量 / 实际招生量`
- `观测期内续报率 = 期内续报量 / 实际招生量`

| 条件 | 判断 |
|---|---|
| 占比 = 0 | 成熟 |
| 占比 > 0 且下一期续报结束时间 ≥ 执行当天 | **不成熟**（流入下一期续报未结束）|
| 占比 > 0 且下一期续报结束时间 < 执行当天 | 成熟 |

无阈值；只要占比 > 0 就必须检查下一期续报结束时间。本方法**不**判断当前期自身续报未结束的不成熟。

## 预测修正公式（仅对不成熟期次）
```
流入下一期预计最终续报量 = 流入下一期实际招生量 × 班型预估续报率
预计补充续报量 = max(预计最终续报量 - 流入下一期已观测期内续报量, 0)
预测修正后期内续报量 = 期内续报量 + 预计补充续报量
预测修正后期内续报率 = max(预测修正后期内续报量 / 实际招生量, 观测期内续报率)
预计续报率影响 = 预测修正后期内续报率 - 观测期内续报率
```

**预测下限规则**：任一粒度的修正后期内续报率不得低于观测期内续报率（须在预测生成阶段生效，不能只在展示层兜底）。

## 班型预估续报率降级顺序（4 级）
1. 分析期次内同 `定标班型` 非流入下一期且成熟样本的期内续报率
2. 同渠道、同 `定标班型` 最近成熟期次续报率
3. 同 `sku + 业务核算组 + 定标渠道类型` 的成熟样本整体续报率
4. 当前渠道整体成熟样本续报率

采用降级口径必须在底层预测明细标记 `预估率口径`。

## 预测修正后期内 GMV（可用GMV客单价 4 级降级）
对已做续报量预测修正的不成熟期次，进一步换算预测修正后期内 GMV，供 GMV 达成与归因消费：
```
预测修正后期内GMV = 预测修正后期内续报量 × 可用GMV客单价
可用GMV = 成熟期次取观测期内GMV；不成熟期次取预测修正后期内GMV
GMV目标达成率 = 可用GMV / GMV目标（均先 sum 再相除）
```
可用GMV客单价按 4 级降级取数，命中级别写入明细 `corrected_gmv_status`：
1. `channel_unit_price`：本渠道(本行)期内GMV / 期内续报量（仅当本行期内续报量 > 0）
2. `class_type_unit_price`：同 `期次 × 定标班型`(term × class_type) 期内GMV / 期内续报量
3. `term_unit_price`：同 `期次`(term) 市场整体期内GMV / 期内续报量
4. `term_target_unit_price`：同 `期次`(term) 目标GMV / 目标续报人数（兜底）

4 级均不可算时标记 `unavailable`，该行不产出修正GMV。成熟期次 `corrected_gmv_status = not_adjusted`，可用GMV直接等于观测期内GMV。

## 缓存契约（market_sales_actual_flow_adjusted）
- 基于 `market_sales_actual` 同粒度生成
- 仅对判定不成熟的流入下一期行写入预测补充，其它行 `预计补充续报量 = 0`
- `generated_date = 执行当天`，仅当天有效
- 新增字段：`预测补充续报量 / 预测修正后期内续报量 / 预测修正后期内GMV / 可用续报量(available_renew_count) / 可用GMV(available_gmv) / 流入下一期成熟度判断 / 预测修正口径 / 客单价降级口径(corrected_gmv_status)`
- 下游工作流（续报率归因 / 渠道质量验证 / 过程转化诊断 / GMV 达成诊断）当天有效时**必须优先读取此缓存**，不得继续直接用未修正数据做续报率 / GMV 达成判断

## 引用关系
- 上游：`market_sales_actual` / `market_sales_target`
- 下游 workflow：`wf_l1_renewal_full_attribution`、`wf_l1_process_funnel_diagnosis`
- 产出指标：`mq_l1_corrected_renewal_lst`、`mq_l1_corrected_inrenewal_gmv`、`mq_l1_gmv_achievement_rate`

**可执行脚本**（推荐依赖：pandas>=2.0, numpy>=1.24）：调用 `python3 methods/run.py mth_l1_flow_next_maturity_adjustment '<params_json>'`；参数 schema 见 `methods/mth_l1_flow_next_maturity_adjustment.json`。**不要手写参数化、不要重新生成代码。**

## 过程漏斗连环替代法（mth_l1_funnel_chain_substitution）

用于解释「期内续报率」在两个窗口间的差异是由哪个过程环节下降导致。L1 过程转化漏斗的 4 环节按固定顺序拆解：`加微率 → 加微首到率 → 首到留存率 → 完课转化率`，逐环节用现期值替换基期值，差额归到当前替换的环节。本方法不做结构归因，**与 `mth_l1_structural_attribution` 互不替代**。

## 固定指标
| 环节 | 名称 | 口径 |
|---|---|---|
| F1 | 加微率 | `微信添加量 / 实际招生量` |
| F2 | 加微首到率 | `首课到课量 / 微信添加量` |
| F3 | 首到留存率 | `4课完课量 / 首课到课量` |
| F4 | 完课转化率 | `期内续报量 / 4课完课量` |
| 结果 | 期内续报率 | `期内续报量 / 实际招生量 = F1 × F2 × F3 × F4` |

率类指标**跨期次 / SKU / 渠道组合聚合时**必须用聚合后的分子分母重算；禁止对率做算术平均。任一环节分母为 0 时该环节不得硬算为 0 或 100%，必须标注「不可验证」。

## 连环替代步骤
```
第 0 步：期内续报率(基期) = F1₀ × F2₀ × F3₀ × F4₀
第 1 步：替换 F1 → F1 影响 = F1₁×F2₀×F3₀×F4₀ - F1₀×F2₀×F3₀×F4₀
第 2 步：替换 F2 → F2 影响 = F1₁×F2₁×F3₀×F4₀ - F1₁×F2₀×F3₀×F4₀
第 3 步：替换 F3 → F3 影响 = F1₁×F2₁×F3₁×F4₀ - F1₁×F2₁×F3₀×F4₀
第 4 步：替换 F4 → F4 影响 = F1₁×F2₁×F3₁×F4₁ - F1₁×F2₁×F3₁×F4₀
校验：F1 影响 + F2 影响 + F3 影响 + F4 影响 ≈ 期内续报率(现期) - 期内续报率(基期)
```

替换顺序**不可调换**——顺序变化会改变中间项归属，但加微 → 到课 → 完课 → 续报是核桃编程体验课业务的物理链路顺序，必须遵守。

## 主拖累环节判定
- 各环节影响按**绝对值降序**排列
- 下降场景：标记最大负向环节为「主拖累环节」；正向环节单独列出但不抢主结论
- 上升场景：标记最大正向环节为「主拉动环节」

## 应用层级
1. 整体过程漏斗：判断哪个环节下降影响 `期内续报率`
2. 全部 SKU 验证：主拖累环节是否集中在某个 SKU 或出现 SKU 间瓶颈分化
3. `业务核算组 + 定标渠道类型` 验证：主拖累环节下降是渠道普降、单渠道放大还是多渠道共同拖累——**渠道下钻只服务环节验证**，不替代漏斗主因判断

## 禁用
- 不可用 SKU 结构影响 / 智配-非智配结构影响 / 渠道组合总影响 替代过程漏斗主因——那是 `mth_l1_structural_attribution` 的职责
- 不可使用 `首课到课承接率` / `4课完课承接率` 等旧命名
- 不可对率类指标做算术平均
- 不可纳入 GMV / 累计 GMV / 客单价 作为本方法的输入或主结论

## 引用关系
- 上游数据：`market_sales_actual` / `market_sales_actual_flow_adjusted`（F4 用修正口径时）
- 上游方法：`mth_l1_flow_next_maturity_adjustment`（提供修正后期内续报量）
- 调用 workflow：`wf_l1_process_funnel_diagnosis`、`wf_l1_renewal_full_attribution`（质量验证块的过程漏斗子任务）

**可执行脚本**（推荐依赖：pandas>=2.0）：调用 `python3 methods/run.py mth_l1_funnel_chain_substitution '<params_json>'`；参数 schema 见 `methods/mth_l1_funnel_chain_substitution.json`。**不要手写参数化、不要重新生成代码。**

## 结构归因统一公式（mth_l1_structural_attribution）

用于解释「整体续报率」在两个窗口间的差异——把差异拆为「结构影响」（分组招生占比变化）与「续报率影响」（同分组续报率变化）两部分。L1 续报率归因（SKU / 智配-非智配 / 业务核算组+定标渠道类型）以及质量验证（城市线级 / 支付年级 / 渠道组）的所有结构维度均使用此公式。

## 核心公式
```
整体续报率 = sum(分组招生占比 × 分组续报率)
招生占比变化 = 现期招生占比 - 基期招生占比
归因用基期续报率 = 若基期招生占比 = 0 且现期招生占比 > 0，取现期续报率；否则取基期续报率
结构影响 = 招生占比变化 × (归因用基期续报率 - 本层基期整体续报率)
续报率影响 = 现期招生占比 × (现期续报率 - 归因用基期续报率)
总影响 = 结构影响 + 续报率影响
```

## 新增分组保护规则（强制启用）
当某分组在基期招生占比 = 0（即新增 SKU / 新增渠道组合）但现期招生占比 > 0 时，**禁止**把基期续报率默认为 0 后参与计算——这会把该分组的「自身续报率」错误地放大为 +100% 续报率提升。

正确做法：用「归因用基期续报率 = 现期续报率」兜底，使该分组的「续报率影响 = 0」，只产生「结构影响」（来自该分组从 0 占比扩张到现期占比的体量贡献）。

## 数字展示规则
- 续报率、招生占比：1 位 `%`
- 结构影响、续报率影响、总影响：2 位 `%`；非零但 `|x| < 0.01%` 显示为 `<0.01%` / `>-0.01%`
- **禁用** `pp` / 百分点 / 百分比点 等单位；禁止把非零影响渲染成 `0.0%` 或 `0.00%`
- 影响值按方向红/绿标识；总计行加粗/底色突出

## 总计行规则
招生量加总；续报率与差距按汇总分子分母重算；**禁止对比例和影响值做算术平均**。

## 比较模式
- 对比目标：基期 = 当前窗口目标数据，现期 = 当前窗口实际或修正后实际数据；基期列命名「目标」
- 对比历史：基期 = 历史基准窗口实际或修正后实际数据，现期 = 当前窗口实际或修正后实际数据；基期列命名「历史」

两种模式不得混用基期。`comparison_type = 全部` 时必须分别计算两套归因表。

## 应用层级（在 wf_l1_renewal_full_attribution 内的固定顺序）
1. SKU 层：保留全部 SKU，按 `总影响` 排序
2. SKU 内智配/非智配：所有 SKU 均按 `招生分配类型` 拆解
3. 业务核算组 + 定标渠道类型：每个 SKU 内，`智配班型` 或 `非智配班型` 现期招生占比 ≥ 10% 时下钻；< 10% 不下钻
4. 质量验证（移交范围内）：城市线级 / 支付年级 / 渠道组——下钻结构验证只用观测口径，不分摊预测补充续报量

## 引用关系
- 上游数据：`market_sales_actual` / `market_sales_target` / `market_sales_actual_flow_adjusted`
- 上游方法：`mth_l1_flow_next_maturity_adjustment`（提供修正后续报口径）
- 调用 workflow：`wf_l1_renewal_full_attribution`

**可执行脚本**（推荐依赖：pandas>=2.0, numpy>=1.24）：调用 `python3 methods/run.py mth_l1_structural_attribution '<params_json>'`；参数 schema 见 `methods/mth_l1_structural_attribution.json`。**不要手写参数化、不要重新生成代码。**

## 最近N期续报率默认期次过滤口径（mth_l1_recent_n_terms_default_filter）

面向 L1 续报率类问题（如「最近N期续报率」「最近N期团队/渠道/班型续报率对比」），在用户未额外说明时，统一默认排除『续报尚未结束』的期次，避免把未成熟期次的低续报率纳入主对比，误判团队、渠道或班型续报质量。

## 默认期次选择规则
- 取 `term_renewal_end_date < 执行当天` 的期次为『续报已结束』，纳入主口径
- 即使某期已经开课（`term_start_date ≤ 执行当天`），只要 `term_renewal_end_date ≥ 执行当天` 或为空，仍判为『正在续报 / 续报未开始』，**不纳入主口径**
- 在以上『续报已结束』集合内，按 `term_renewal_end_date` 倒序取最近 N 个学期，作为『最近 N 期续报率』的事实期次集合

## 何时不适用本默认
- 用户明确说出『包含当前期 / 含未成熟期 / 含正在续报 / 观察口径』等关键字时
- 用户明确指定具体期次列表（即手动覆盖期次集合）时
- 走 [[mth_l1_flow_next_maturity_adjustment]] 的预测修正口径分析（已显式处理另一种流向不成熟），无需再叠加本默认

## 与 [[mth_l1_flow_next_maturity_adjustment]] 的关系
- 本方法关注『期次自身续报结束日是否到达』，是期次级粗筛
- [[mth_l1_flow_next_maturity_adjustment]] 关注『流入下一期占比 > 0 且下一期续报结束时间未到』导致的不成熟，是流向级精修
- 通常先按本默认过滤期次，再在被纳入的期次内由 [[mth_l1_flow_next_maturity_adjustment]] 处理流向成熟度

## 与 [[mth_l1_recent_n_terms_maturity_hint]] 的关系
- 用户明确要求观察当前期、或显式包含未成熟期次时，转入 [[mth_l1_recent_n_terms_maturity_hint]]，给出『观察口径』与成熟度风险提示，不再走本默认过滤

## 用法示例
用户：『最近三期，不同销售团队在智配低人效班型的招生量和续报率对比』
→ 应用本默认：仅取最近三个 `term_renewal_end_date < today` 的期次；对正在续报或未续报的最新期次不纳入主口径；若仍想观察当前期，单独走 [[mth_l1_recent_n_terms_maturity_hint]] 的观察口径输出。

## 最近N期续报率成熟度提示与观察口径（mth_l1_recent_n_terms_maturity_hint）

当用户**明确要求**包含当前期 / 含未成熟期 / 含正在续报期，或显式给出最新分区的期次列表，进入本观察口径分支：对未成熟期次自动输出成熟度风险提示，并附『成熟三期版本』兜底，避免把未成熟期次低续报率误判为团队、渠道或班型续报质量问题。

## 触发条件
- 用户显式表达『含当前期 / 含正在续报 / 观察口径 / 加上最新期 / 最新分区也算上』
- 用户给出的最近三期里至少一期满足 `term_renewal_end_date ≥ today` 或 `term_start_date > today`（即续报未结束 或 未开课）
- 默认场景未明示时不进入本分支，按 [[mth_l1_recent_n_terms_default_filter]] 默认过滤

## 成熟度判断（期次级）
对纳入的每个期次，根据执行当天 `today` 做：

| 条件 | 标记 |
|---|---|
| `term_start_date > today`（未开课） | **未开课**（最不成熟） |
| `term_start_date ≤ today` 且 `term_renewal_end_date > today` | **续报未结束**（不成熟） |
| `term_start_date ≤ today` 且 `term_renewal_end_date ≤ today` | **成熟** |
| `term_renewal_end_date` 为空 | **未知**，按不成熟处理并提示运营核对维表 |

## 输出契约
本方法不产出数值修正，只产出两段固定输出供 workflow 拼接：

1. **观察口径声明**（必出）：
   ```
   ⚠️ 观察口径：本结果包含 {n_immature} 期未成熟期次（{terms}），其续报率为当前进度截面，不宜直接用于团队/渠道/班型续报质量评估。
   ```
   `{n_immature}` 为未成熟期数；`{terms}` 为对应学期名列表。

2. **成熟版本兜底**（建议出，可选）：
   当未成熟期次 ≥ 1 时，同时按 [[mth_l1_recent_n_terms_default_filter]] 重新取最近 N 个成熟期次跑一遍，作为成熟版本附在结果末尾，标题为『最近 N 期（成熟版本）』，便于对照。

## 与 [[mth_l1_flow_next_maturity_adjustment]] 的关系
- 本方法仅做『期次自身是否成熟』的标注与提示，不做预测修正
- 一旦进入预测修正分析，应改用 [[mth_l1_flow_next_maturity_adjustment]] 的『流入下一期』成熟度与 4 级降级预估率口径，不重复触发本提示

## 用法示例
用户：『最近三期，不同销售团队在智配低人效班型的招生量和续报率对比，含最新一期』
→ 触发本分支：发现最新期 `term_renewal_end_date ≥ today`，标记『续报未结束』；输出观察口径声明 + 成熟三期版本对照表；最新一期续报率仅作进度参考。

## L1 EDA 默认率公式表（mth_l1_eda_rate_formulas）

# 方法卡：L1 EDA 默认率公式表（mth_l1_eda_rate_formulas）

## 适用场景
凡是 L1 体验课 EDA 场景下的"率/占比"类指标，未额外约定时一律按本表口径计算。源自 R 应用的 `calculated_metrics_ls`，是平台与现有 Shiny 探查工具之间的口径一致性依据。

## 核心规则（不可破坏）
**所有率 = `sum(分子) / sum(分母)`**——先聚合，后相除。

禁止：
- `mean(分子 / 分母)`（行级率算术平均，错误）
- `sum(rate)` / `mean(rate)`（已是率的列再聚合，错误）
- 把率字段当作量再做窗口或滚动平均；要滚动平均时，对 `分子` 和 `分母` 各做窗口再相除（见 [[wf_l1_eda_trend_explore]] Step 3）

## 加微率系列（分母 = `enroll`）
| 中文名 | 分子 | 公式 |
|---|---|---|
| 加微率 | `wx_add` | `sum(wx_add) / sum(enroll)` |
| 十分钟内加微率 | `wx_add_t10min` | `sum(wx_add_t10min) / sum(enroll)` |
| 一小时内加微率 | `wx_add_t1h` | `sum(wx_add_t1h) / sum(enroll)` |
| 二十四小时内加微率 | `wx_add_t24h` | `sum(wx_add_t24h) / sum(enroll)` |
| 四十八小时内加微率 | `wx_add_t48h` | `sum(wx_add_t48h) / sum(enroll)` |
| 三日内加微率 | `wx_add_t3` | `sum(wx_add_t3) / sum(enroll)` |
| 首课前加微率 | `wx_add_ahead_unlocked1` | `sum(wx_add_ahead_unlocked1) / sum(enroll)` |

> 注：`wx_add_t10min` / `wx_add_t1h` / `wx_add_t24h` / `wx_add_t48h` 当前**不在** `ads_eda_l1_v2_business_regular_df`，仅源 R 的 multidim 表有。如用户问到这些率而主表无字段，提示"细粒度时效字段需等数仓 mirror 完成"。

## 删微率
| 中文名 | 公式 |
|---|---|
| 首课前删微率 | `sum(attend1_ahead_del_wx) / sum(enroll)` |

> `attend1_ahead_del_wx` 同样仅在 multidim 表中，主表不可用。

## 到课 / 完课系列
| 中文名 | 公式 | 说明 |
|---|---|---|
| 加微首到率 | `sum(attend1_t6) / sum(wx_add)` | **分母换为 wx_add**，不是 enroll |
| 首到率 | `sum(attend1_t6) / sum(enroll)` | |
| 留存率 | `sum(finish_last_t6) / sum(attend1_t6)` | **分母为首到，不是招生** |
| 末课完课率 | `sum(finish_last_t6) / sum(enroll)` | |
| 累计完转率 | `sum(renewal_acc) / sum(finish_last_t6)` | 完末课人群里的累计续报率 |
| 三课留存率 | `sum(finish3_t6) / sum(attend1_t6)` | |
| 三课完转率 | `sum(renewal_acc) / sum(finish3_t6)` | |

## 续报率系列（分母 = `enroll`）
| 中文名 | 分子 | 公式 |
|---|---|---|
| 累计续报率 | `renewal_acc` | `sum(renewal_acc) / sum(enroll)` |
| 首日续报率 | `renewal_t0` | `sum(renewal_t0) / sum(enroll)` |
| 二日续报率 | `renewal_t1` | `sum(renewal_t1) / sum(enroll)` |
| 三日续报率 | `renewal_t2` | `sum(renewal_t2) / sum(enroll)` |
| 四日续报率 | `renewal_t3` | `sum(renewal_t3) / sum(enroll)` |
| 末日续报率 | `renewal_lst` | `sum(renewal_lst) / sum(enroll)` |

> 与 `l1-ops-analyst` 的 [[mq_l1_renewal_rate_lst]] / [[mq_l1_renewal_rate_t0]] / [[mq_l1_renewal_rate_acc]] **口径一致**，命名不同：经营分析师视角的指标卡含目标对比，本方法卡仅给计算式。

## 退费率系列（分母 = `renewal_acc`）
| 中文名 | 分子 | 公式 |
|---|---|---|
| 退费率 | `refund_cnt` | `sum(refund_cnt) / sum(renewal_acc)` |
| 全额期退费率 | `refund_before_fullvl_cnt` | `sum(refund_before_fullvl_cnt) / sum(renewal_acc)` |
| 进班前退费率 | `refund_before_enter_class_cnt` | `sum(refund_before_enter_class_cnt) / sum(renewal_acc)` |
| 开课前退费率 | `refund_before_unclock_class_cnt` | `sum(refund_before_unclock_class_cnt) / sum(renewal_acc)` |
| 开课后退费率 | `refund_after_unclock_class_cnt` | `sum(refund_after_unclock_class_cnt) / sum(renewal_acc)` |
| 非全额期退费率 | `refund_fullvl_cnt` | `sum(refund_fullvl_cnt) / sum(renewal_acc)` |

> **分母是 `renewal_acc`（累计续报）**，不是 `enroll`——退费率口径里"基数"是已续报人群，不是招生人群。

## 其它（分母 = `renewal_lst`）
| 中文名 | 公式 |
|---|---|
| 直售占比 | `sum(direct_sale_cnt) / sum(renewal_lst)` |
| 撞单占比 | `sum(class_teacher_hit_order_cnt) / sum(renewal_lst)` |

## 展示精度
- 默认百分号 2 位：`64.32%`
- 用户要求时可调小数位

## 与 ops-analyst 指标卡的关系
本方法卡只定义 EDA 探查时的快速计算式，**不替代** `l1-ops-analyst` 角色下的指标卡（如 [[mq_l1_renewal_rate_lst]]）。后者含目标值、归因维度、考核周期等业务上下文；本方法卡更轻量，纯口径。


## L1 多维指标看板维度字段词典与默认筛选口径（mth_l1_multidim_indicator_dictionary）

# 方法卡：L1 多维指标看板维度字段词典与默认筛选口径（mth_l1_multidim_indicator_dictionary）

## 适用场景
当用户要求"按多个维度交叉看 L1 体验课的过程指标 / 漏斗 / 续报 / 退费"，或要在客户端复刻 ads_l1_eda_multidimensional_indicators_df_tab Shiny 看板的探查能力时，按本方法卡选取字段、套默认筛选锚点与口径。

源表：行/订单粒度宽表 `ads_eda_l1_v2_multidimensional_indicators_df`。

## 一、维度字段词典（5 维度组）

### 1. 学期相关
| 中文名 | 字段 |
|---|---|
| 学期名称 | term |
| 学期id | term_id |
| 计划进班学期名称 | intend_term |
| 计划进班学期id | intend_term_id |
| 订单支付月 | pay_month |
| 订单支付日期 | pay_date |
| 用户进班日期 | user_class_start_date |
| 学期开始月 | term_start_month |
| 招生开始月 | term_enroll_start_month |
| 招生结束月 | term_enroll_end_month |
| 续报开始月 | term_renewal_start_month |
| 续报结束月 | term_renewal_end_month |

### 2. 定标相关
| 中文名 | 字段 |
|---|---|
| 用户群体 | user_group |
| 定标SKU | sku |
| 定标业务线 | business_line |
| 定标班型 | goal_class_tag |
| 定标团队 | goal_operation_group |
| 定标核算组 | goal_mkt_group |
| 定标渠道类型 | goal_channel_type |

### 3. 续报老师相关
| 中文名 | 字段 |
|---|---|
| 续报老师组基地 | renewal_counselor_group_city |
| 续报老师组名称 | renewal_counselor_group_name |
| 续报班级老师真名 | renewal_staff_name |
| 续报老师组id | renewal_counselor_group_id |
| 续报老师是否新人 | is_new_renewal_staff |
| 本期次老师班型是否与上期次一致 | is_new_class_ct_business |

### 4. 用户相关
| 中文名 | 字段 |
|---|---|
| 等待开课天数 | diff_term_start |
| 是否重复购买 | is_repeat_order |
| 支付年级 | pay_grade |
| 家长所在城市线级 | city_level |
| 家长所在城市 | parents_city |

### 5. 其它
| 中文名 | 字段 |
|---|---|
| 渠道类型 | channel_type_name |
| 渠道子类型 | channel_subtype_name |
| 渠道组名称 | channel_group_name |
| 班级标签名称 | new_class_tag_name |
| 达人名称 | talent_author_name |

## 二、量指标分组（9 组）

| 分组 | 字段 |
|---|---|
| CT数 | renewal_staff_id（**注意**：聚合走 `count(distinct renewal_staff_id)`，不是 sum） |
| 招生人数 | enroll |
| 加微相关 | wx_add / wx_add_t10min / wx_add_t1h / wx_add_t24h / wx_add_t48h / wx_add_t3 / wx_add_ahead_unlocked1 |
| 到课相关 | attend0_ahead_unlocked1 / attend1_t6 … attend5_t6 / attend_last_t6 |
| 完课相关 | finish1_t6 … finish5_t6 / finish_last_t6 |
| 续报相关 | renewal_acc / renewal_t0 / renewal_t1 / renewal_t2 / renewal_t3 / renewal_lst |
| 续报GMV相关 | inrenewal_renewal_gmv / renewal_gmv |
| 退费相关 | refund_cnt / refund_before_fullvl_cnt / refund_before_enter_class_cnt / refund_before_unclock_class_cnt / refund_after_unclock_class_cnt / refund_fullvl_cnt |
| 退费GMV相关 | refund_gmv / refund_before_fullvl_gmv / refund_before_enter_class_gmv / refund_before_unclock_class_gmv / refund_after_unclock_class_gmv / refund_fullvl_gmv |
| 其它 | direct_sale_cnt / class_teacher_hit_order_cnt |

## 三、率指标公式
所有率指标的计算口径见 [[mth_l1_eda_rate_formulas]]——本方法卡不重复，所有"率 = sum(分子)/sum(分母)"的口径以那张方法卡为准。

## 四、默认筛选锚点（与 R Shiny 看板一致）

| 字段 | 默认值 | 说明 |
|---|---|---|
| 日期口径轴 | term_start_date | 4 选 1，详见五 |
| 时间范围 | 近 180 天 | 默认观察窗口 |
| user_group | 思维 | 默认聚焦思维 BU |
| business_line | 9.9 | 9.9 元体验课业务线 |
| goal_operation_group | 9元团队 | 9 元运营团队 |
| sku | 全部 | 默认不限 SKU |
| goal_class_tag | 全部 | 默认不限班型 |
| is_target | [1, 0] | 标内 + 标外都看（默认两个都选） |

用户未显式指定上述任一筛选时，按本表默认值代入；显式指定（"我只看科特"、"看 0元 项目"）以用户为准。

## 五、4 个日期口径轴

| 选项 | 字段 | 适用场景 |
|---|---|---|
| 按支付日期 | pay_date | 现金流 / 收入归因视角 |
| 按开课日期（默认） | term_start_date | 班期成熟度视角，与续报漏斗对齐 |
| 按续报开始日期 | term_renewal_start_date | 续报窗口启动归因 |
| 按续报结束日期 | term_renewal_end_date | 续报窗口收尾观察 |

用户不指定时默认 `term_start_date`。注意上述四个字段在表中均为 VARCHAR（'YYYY-MM-DD' 形态），比较时按字符串字典序即可。

## 六、聚合规则

- 量指标默认 `sum(col)`，但 **renewal_staff_id 走 `count(distinct renewal_staff_id)`**（CT数 = 不重复班主任数）
- 率指标一律 `sum(分子) / sum(分母)`，不能行级 mean，详见 [[mth_l1_eda_rate_formulas]]

## 七、输出格式约定

- GMV / 金额类列：保留 2 位小数
- 名称含"率"或"占比"的列：转百分比，保留 2 位小数（如 `64.32%`）

## 与 [[mth_l1_eda_rate_formulas]] 的分工

- 本方法卡：维度字段词典 + 量指标分组 + 默认筛选锚点 + 聚合规则 + 输出格式约定
- 那张方法卡：所有率指标的计算公式（分子/分母明细）
- 两张方法卡配合，等价于 ads_l1_eda_multidimensional_indicators_df_tab Shiny 看板的探查能力

## 配套 SQL
参数化多维聚合 SQL 见 [[sql_l1_multidim_indicator_explore]]。


## GMV三因子序贯分解（mth_l1_gmv_three_factor_decomposition）

GMV 达成/变化诊断的定量归因法。当 GMV 未达标（对目标）或环比/同比波动时，定位缺口由 招生量 / 续报率 / 客单价 哪个因子主导、各贡献多少。基于乘法恒等式 `GMV = 招生量E × 续报率R × 客单价P`，按固定顺序序贯替代，三段影响之和精确闭合于 GMV 缺口。

## 核心公式
```
GMV = E × R × P （招生量 × 续报率 × 客单价）
基期取 0，现期取 1；基期随方向取（对目标=S标 / 环比=上一期 / 同比=去年同期）

招生量影响 = (E1 − E0) · R0 · P0
续报率影响 = E1 · (R1 − R0) · P0
客单价影响 = E1 · R1 · (P1 − P0)

三段之和 = E1·R1·P1 − E0·R0·P0 = GMV 缺口 （精确闭合）
```

## 强制护栏
- **因子顺序固定 招生量→续报率→客单价**：序贯分解对顺序敏感，交叉项被后置因子吸收，必须固定此顺序并在报告注明，禁止调换。
- **率先汇总再相除**：R = Σ续报/Σleads，禁对率做算术平均；P 用 [[mq_l1_arpu_actual]] = Σ GMV / Σ招生量，同样先汇总分子分母再相除。
- **新增分组保护**：分组须完整覆盖全对象，新增班型/团队必须纳入，否则 E1·R1·P1 与整体不符、闭合破坏。
- 单一数据源闭合，运营源与市场源不跨源拼接；分母为 0 记 N/A。
- 仅用于 GMV 场景；问续报率缺口改用 [[mth_l1_structural_attribution]]（结构 vs 率），不得反用三因子。

## 数字展示规则
- 三因子影响 = 金额（万元，2 位小数、带正负号）。
- 影响占比 = 各因子影响 / GMV 缺口，2 位 %（禁用 pp）。
- GMV 达成率 1 位 %（<100% 标红）；续报率 R 1 位 %；客单价 P 元。

## 总计行规则
- 三因子影响求和须等于 GMV 缺口（E1R1P1 − E0R0P0），闭合校验 |Σ − 缺口| < 0.01。
- 总计行金额直接取缺口，不对因子求平均；率/客单价按整体 Σ分子/Σ分母 重算。

## 比较模式
- 对目标：E0/R0/P0 取官方 S 标目标。
- 环比：单期基期 = 上一期 + 上一同轮次；多期 = 往前等长不重合窗口。
- 同比：取去年同期。三模式基期不同、结论分列，不混算。

## 引用关系
- 指标：[[mq_l1_gmv_impact_admission]] / [[mq_l1_gmv_impact_renewal_rate]] / [[mq_l1_gmv_impact_arpu]] / [[mq_l1_arpu_actual]]。
- 方法：续报率影响可下钻 [[mth_l1_structural_attribution]]（结构 vs 率细分）。
- 场景卡： / 。

**可执行脚本**（推荐依赖：pandas>=2.0, numpy>=1.24）：调用 `python3 methods/run.py mth_l1_gmv_three_factor_decomposition '<params_json>'`；参数 schema 见 `methods/mth_l1_gmv_three_factor_decomposition.json`。**不要手写参数化、不要重新生成代码。**

## 比值型指标贡献分解（人效贡献通法）（mth_l1_ratio_contribution_decomposition）

比值型指标（人效 = Σ招生/Σ带班CT数，同样适用客单价、各类率）的可加贡献分解通法。达标体检后，把整体（实际 − 目标）缺口按各子群偏离度加权分配到团队/班型，父 = 各子之和、Σ 闭合于整体缺口，定位效能拖累主体。

## 核心公式
```
比值型指标 M = Σ分子 / Σ分母 （如 人效 = Σ招生 / Σ带班CT）
子群 c 偏离: Δ_c = M实际_c − M目标_c
整体缺口: ΔM = M实际_整体 − M目标_整体 （均先汇总分子分母再相除）

贡献_c = (Δ_c / Σ Δ_c) · ΔM

Σ 贡献_c = ΔM （精确闭合于整体缺口）
```
注：子群 Δ_c 与体量无关、直接相加不等于 ΔM，故按 Δ_c 占比再乘 ΔM 重标定以保证闭合。

## 强制护栏
- **率/比值先汇总再相除**：M实际_c、M目标_c、整体值一律 Σ分子/Σ分母，禁对率/比例/贡献做算术平均。
- **新增分组保护**：子群须完整覆盖全对象，新增团队/班型必须纳入，否则 ΣΔ 变化、闭合破坏。
- **ΣΔ_c = 0 记 N/A**：整体无偏离或正负抵消导致 ΣΔ=0 时贡献无定义，改看子群净额；分母为 0 记 N/A。
- 通法适用任意比值型指标（人效/客单价/率）；但率缺口的 结构 vs 率 归因优先用 [[mth_l1_structural_attribution]]，本法用于"按子群分配整体缺口"的贡献视角。

## 数字展示规则
- 贡献值单位随母指标：母指标为率时按 2 位 %（禁用 pp）；为人效/客单价时按母指标原单位 2 位小数、带正负号。
- 贡献占比 = 贡献_c / ΔM，2 位 %（禁用 pp）。
- 人效 2 位小数、客单价元、率 1 位 %；达成率 1 位 %（<100% 标红）。

## 总计行规则
- 贡献求和 = 整体缺口 ΔM，闭合校验 |Σ − ΔM| < 0.01。
- 总计行 M实际/M目标 用整体 Σ分子/Σ分母 重算，不对子群值求平均。

## 比较模式
- 对目标：M目标 取官方目标（如目标人效）。
- 环比/同比：把"目标"替换为上一期/去年同期基期值，公式不变；基期随方向取。

## 引用关系
- 指标：[[mq_l1_enroll_per_counselor_contribution]] / [[mq_l1_enroll_per_counselor_actual]] / [[mq_l1_enroll_per_counselor_target]] / [[mq_l1_enroll_per_counselor_achievement_rate]]。
- 方法：结构 vs 率归因见 [[mth_l1_structural_attribution]]；过程漏斗连环替代配套定位环节。
- 场景卡： / 。

**可执行脚本**（推荐依赖：pandas>=2.0, numpy>=1.24）：调用 `python3 methods/run.py mth_l1_ratio_contribution_decomposition '<params_json>'`；参数 schema 见 `methods/mth_l1_ratio_contribution_decomposition.json`。**不要手写参数化、不要重新生成代码。**

## 多层嵌套结构归因·逐层拆内率闭合（mth_l1_nested_structural_attribution）

用途：把某比率指标（首日/期内续报率、转化率等）的整体变化，沿"父层→子层"逐层下钻、无遗漏无重叠地拆到最细执行单元，且每层可与上层精确对账。本方法是 [[mth_l1_structural_attribution]] 单层两因子归因的嵌套扩展，只增补逐层闭合与去噪机制，不重述单层公式。

## 核心公式
```
单层（见 [[mth_l1_structural_attribution]]）：
 结构影响 = (w1−w0)·(r0 − R̄0) # 必中心化，减本层整体基准率 R̄0
 率影响 = w1·(r1 − r0)
逐层嵌套（本方法新增）：
 本层某组的『率影响』= 对该组继续下钻到下一层的归因之和
 = 下一层内率结构影响 + 下一层内率率影响
 闭合恒等式： 本层结构影响 + 下一层内率 = 上层内率
 全链相加 = 整体率变化（Σ闭合）
去噪（经验贝叶斯收缩）：
 r_adj = (n·r_unit + K·r_parent) / (n + K), K≈300
```

强制护栏：
- 每层拆的是**上一层的"内率/残差"**，非重新对整体拆；拆完必跑闭合校验（本层结构+下一层内率=上层内率），不闭合不得交付。
- 结构影响一律中心化（减本层整体基准 R̄0），禁直接 ×r0。
- **单一数据源闭合**：一条链只用一个源（运营 `fact_operations_actual` 或市场集市），跨源拼接不闭合。
- **去噪**：细粒度小分母易被噪声放大，收缩后接近父层率者判噪声、不入主链（K≈300）。
- **同格可比破 Simpson**：团队/单元边际率与"同SKU同班型可比率"可能反向，执行结论只认可比口径，边际率不作执行判断。
- **未进班/未分配作独立一层**（`is_unplaced`=1，率恒0）：其占比变动是纯结构效应、可被高人效层收缩对冲成"假平静"，成因留专题、更细层不重复计。
- 基期 r0 不可得时 r0:=r1 保闭合；基期随方向取（对目标=S标/环比=上期/同比=去年同期）。

## 数字展示规则
- 影响/差距类：2 位小数、**按 %**（对齐平台，禁用 pp），带正负号。
- 率/占比：1 位小数、按 %。率先汇总分子分母再相除，禁算术平均。

## 总计行规则
- 每层证据表带总计行；总计率由分子分母重算（非子行求和），分母 0 记 N/A。
- 影响列总计 = 各子行之和，须等于上层内率，精确闭合、合计行不留空。

## 应用层级
- 常规班型 2 层 `SKU(市场)→团队(运营)`；智配 3–4 层 `SKU→渠道(市场)→定标班型(TMK)→团队(运营)`；单渠道班型（召回）跳渠道层、运营层拆城市×SKU。指标可切换，公式与嵌套逻辑不变。

## 引用关系
- 单层基：[[mth_l1_structural_attribution]]。
- 产出映射：[[mq_l1_renewal_impact_structure]] 等结构/率影响指标。
- 场景卡：（运营视角班型深拆）、（嵌套闭合）、（季节校准）。

**可执行脚本**（推荐依赖：pandas>=2.0, numpy>=1.24）：调用 `python3 methods/run.py mth_l1_nested_structural_attribution '<params_json>'`；参数 schema 见 `methods/mth_l1_nested_structural_attribution.json`。**不要手写参数化、不要重新生成代码。**

## 续报率三因子分解（结构/A-S差距/A标执行）（mth_l1_renewal_three_factor_attribution）

用途：把续报率相对**整体S标率**的缺口，分解为"招生结构变差 / A标被下调 / 没做到A标"三个可归因来源，回答"是结构、是渠道结构偏弱(A标下调)、还是执行没达标"。本方法是 [[mth_l1_structural_attribution]] 单层两因子的特化：结构影响不变，把『率影响 w1(r1−r0)』沿 r0=S标 进一步拆成 A-S差距 + A标执行两项。

## 核心公式
```
基期取 r0 = 整体/该组 S标率 rS。基于 [[mth_l1_structural_attribution]]：
 班型结构影响 = (w1−w0)·(rS − R̄S) # 中心化，减整体S标率 R̄S
 率影响 = w1·(r1 − rS) 拆成两项：
 A-S差距影响 = w1·(rA − rS) # A标低于S标 = 渠道结构偏弱
 A标执行影响 = w1·(r1 − rA) # 未达A标 = 执行；禁称『率影响』
三者Σ = 实际续报率 − 整体S标率（Σ闭合）
其中 r1=实际续报率, rA=续报率A标, rS=续报率S标, w=班型招生占比
```

强制护栏：
- **命名护栏**：第三项称『**A标执行影响**』，**禁称"率影响"**（率影响是两因子口径的合并项，这里已拆开）。
- 结构影响必中心化（减整体S标率 R̄S），禁直接 ×rS。
- 三项须求和**闭合于『实际续报率 − 整体S标率』**，不闭合不交付。
- 率先汇总分子分母再相除、禁算术平均；分母 0 记 N/A。
- 基期随方向取；对比前市场侧须成熟度前置，运营侧按实际学期不修正。
- 机制话术：班型结构偏弱=向低续报班型倾斜；A标低于S标=渠道结构偏弱；未达A标=执行——话术是分析指引、不写进报告结论槽。

## 数字展示规则
- 三项影响：2 位小数、**按 %**（对齐平台，禁用 pp），带正负号。
- 续报率/占比：1 位小数、按 %。

## 总计行规则
- 明细表带总计行；总计率由分子分母重算，三项影响列总计 = 各行之和，且 Σ三项 = 实际续报率−整体S标率，精确闭合、合计行不留空。

## 应用层级
- 团队总览级续报率缺口定位（先用本三因子分主因）；两因子退化/需看执行侧时突出 A标执行影响。可与 [[mth_l1_nested_structural_attribution]] 衔接向下深拆。

## 引用关系
- 单层基：[[mth_l1_structural_attribution]]。
- 产出映射：[[mq_l1_renewal_impact_structure]] / [[mq_l1_renewal_impact_as_gap]] / [[mq_l1_renewal_impact_a_exec]]。
- 输入率指标：[[mq_l1_renewal_rate_a_target]] / [[mq_l1_renewal_rate_target]] / [[mq_l1_renewal_rate_as_gap]]。
- 场景卡：（三因子分解）。

**可执行脚本**（推荐依赖：pandas>=2.0, numpy>=1.24）：调用 `python3 methods/run.py mth_l1_renewal_three_factor_attribution '<params_json>'`；参数 schema 见 `methods/mth_l1_renewal_three_factor_attribution.json`。**不要手写参数化、不要重新生成代码。**

## 团队/招生线对比与排名（mth_l1_team_comparison_ranking）

团队 / 招生线 / 基地维度的横向对比与排名口径。用于回答"哪个团队/招生线强、差在哪个班型、如何公平排名"。核心前提：不同团队的渠道结构与低/中/高人效班型比例不同，**直接比原始续报率不公平**，须先用 [[mth_l1_structural_attribution]] 剥离结构影响、比同口径率后再排名。

## 核心公式
```
# 直接标准化（统一参照结构），剥离各团队班型结构差异
结构修正后率(team) = Σ_j w_base_j × r_{team,j}
 w_base_j = 统一参照结构下班型 j 的招生占比（如全池招生占比）
 r_{team,j} = 团队 team 在班型 j 的续报率 = Σ续报量 / Σ招生量（先汇总再相除）

# 与原始率之差即该团队的净结构影响（可对齐 [[mth_l1_structural_attribution]] 校验闭合）
结构影响(team) = 原始率(team) − 结构修正后率(team)

# 排名口径（择场景用其一，命名统一"…达成率"、禁用"达标率"）
1) 绝对值率排名 2) 达成率排名(实际/目标)
3) 环比改善排名 4) 结构修正后率排名（跨团队公平比首选）
```

## 护栏规则
- 先结构修正再排名：未修正直接比 → 结论方向可能错；结构剥离一律走 [[mth_l1_structural_attribution]]。
- 率先汇总分子分母再相除，**禁算术平均**；基期率按维度真实取数、不用父级率兜底。
- 团队与招生线是两套口径（0元/9元/蜀都 vs 0元/9元/Python 招生线），**不混用**、对比必标注口径。
- 对比只陈述事实：某维度量多/量少即量多/量少，**纯维度对比不臆断迁移/承接**等因果。
- 单一数据源闭合，运营源与市场源不跨源拼接。
- 分母为 0 记 N/A，不参与排名。

## 数字展示规则
- 率 / 占比 / 达成率 / 结构修正后率：**1 位小数、%**。
- 结构影响 / 与目标差距 / 环比改善幅度：**2 位小数、%，禁用 pp**，带正负号。
- 同一产物内小数位、单位、指标命名一致；排名给整数序号。

## 引用关系
- 前置方法：[[mth_l1_structural_attribution]]（结构剥离）。
- 口径： 场景·团队对比与排名、 运营团队与招生线口径、 业务背景。
- 案例： 团队/招生线对比与排名。

**可执行脚本**（推荐依赖：pandas>=2.0, numpy>=1.24）：调用 `python3 methods/run.py mth_l1_team_comparison_ranking '<params_json>'`；参数 schema 见 `methods/mth_l1_team_comparison_ranking.json`。**不要手写参数化、不要重新生成代码。**

## 季节性班型环比校准（mth_l1_seasonal_class_calibration）

强季节性班型（寒/暑 vs 春/秋，如思维召回）环比校准口径。用于避免把**季节性正常波动误读为运营改善/恶化**：环比看着涨，可能只是历史同期本就涨。核心是"同比幅度 + 绝对水平"双判，把观测环比拆成"季节性兑现"与"运营真实贡献/缺口"两部分。

## 核心公式
```
# 判据一 · 幅度对比（本期环比幅度 vs 去年同期同区间幅度）
Δ_本期 = r_本期 − r_上期
Δ_去年 = r_去年本期 − r_去年上期
若 Δ_本期 ≈ Δ_去年 → 属季节性正常回升, 非运营额外超越

# 判据二 · 绝对水平对比（本期率 vs 去年同期率）
同比缺口 = r_本期 − r_去年本期
若 同比缺口 < 0 → 尚未恢复到去年水平, 即便环比"达标"仍需补同比缺口
（率一律 先汇总分子分母再相除）
```

## 护栏规则
- 仅对**已确认强季节性**的班型启用（召回等）；一般班型按标准深拆 ，不滥用季节校准。
- 新项目 / 无同比基期只做环比、**不臆造同比**；季节班型环比结论**必须显式标注季节因素**。
- 同比须同口径、同期次位次对齐；期次先后按 _term_key 时序判定（寒<春<暑<秋），非字符串字典序。
- 方法/分析规则不写进业务结论；不外推，未来一律"存疑/需观察",禁"一次性/必然"断言。
- 若本期与去年同期"流入下一期成熟度"不同，先用 [[mth_l1_flow_next_maturity_adjustment]] 修正后再比幅度/水平。
- 分母为 0 记 N/A。

## 数字展示规则
- 率 / 绝对水平：**1 位小数、%**。
- 环比幅度 / 同比幅度 / 同比缺口：**2 位小数、%，禁用 pp**，带正负号。
- 同一产物内小数位、单位、命名一致。

## 引用关系
- 案例： 季节性班型环比校准（同比幅度+绝对水平双判）。
- 相关方法：[[mth_l1_flow_next_maturity_adjustment]] 成熟度修正、 运营视角班型深拆。

**可执行脚本**（推荐依赖：pandas>=2.0, numpy>=1.24）：调用 `python3 methods/run.py mth_l1_seasonal_class_calibration '<params_json>'`；参数 schema 见 `methods/mth_l1_seasonal_class_calibration.json`。**不要手写参数化、不要重新生成代码。**

## 经分会全景扫描报告复现规范（mth_l1_scan_report_reproduction）

## 用途
经分会「新生业务扫描」全景多模块报告的产出与复现规范。平台无 report-standard 类型，故以 method 承载。配合 [[wf_l1_jfh_business_scan]] 使用：workflow 定分析链，本规范定报告结构/结论风格/视觉/校验。目标：新会话 100% 复现标杆，数字随期更新，达不到即退化。

## 报告结构（五级标题=通用标签、判断只进结论槽）
骨架序列固定、内容可增删：核心结论(gov+kbar+pills) → 模块①大盘 → 模块②市场(整体+重点渠道) → 模块③运营(整体+班型深拆)。五级标题：L1模块名 / L2章节(结论头 + 定位·归因·下钻·同比 + 编号) / L3子板块(STEP N) / L4图表标题 / L5附注。铁律：所有结构性标题一律通用固定标签，不写数据驱动判断；判断只进 block-lead/gov/pill/解读列等结论槽，每期由 rubric 重生成。

## 克隆优先（GOLDEN 金标准克隆复现）
复现任一模块=从 GOLDEN 逐字克隆该模块 HTML+JS 块，只换①数字（脚本注入、禁 LLM 转录）②结论（按 rubric 重写）。绝不重写 CSS/组件结构/图表 helper。视觉/交互 100% 靠复制资产（benchmark.css/组件/charts.js）。遇 GOLDEN 未覆盖场景（新班型/新基准/无同比基期/跨源/口径拿不准）必先问用户、禁自造。

## 视角标注（同名维度标视角）
目录同名维度必标视角标签消歧：市场·科特直播/思维直播=渠道(琥珀)、运营·科特直播/思维直播=班型(紫)、整体=灰。指标命名同概念归一。

## 季节校准（强季节班型必做）
强季节性班型（暑期回补等）在环比动因块内加季节性校准：斜率图 + 对比表 + 解读，呈现同期回升幅度 + 绝对水位 YoY 差距；把季节性回升与运营改善区分，禁误读为运营功劳、禁外推。

## 结论 rubric（咨询式/数字剥离测试/一句话闸门）
分层，勿把干净短句闸门套到分析型长结论：①定调层(gov)豁免短句闸门、金字塔多分句但须判断非罗列；②结论句层(block-lead)可携证据+一转折、须判断开头或收尾、非导语；③判断短句层(pill/kbar副标/经营状态解读列)严闸门：数字剥离测试过(删数字仍成立、数字≤1锚点)、≤45字、无解释词入句、无AI味、正向也给为什么；④证据句层不套判断闸门。全称/否定判断(无拖累/普遍达标/均超)落笔前必逐行核明细——整体超标≠局部无短板，新人2/3期与班品双换常严重偏低，<100%/负贡献群体必点名。

## 可视化（归因上瀑布/归因柱/条形，内联 echarts）
归因必上图：瀑布/归因柱/条形，忌饼图散点；趋势关键点用图上数据标签；季节性用斜率图。交付铁律：最终报告必把 echarts 内联进单文件 HTML（嵌入前 </script 转义为 <\/script），禁 ./外链（本地正常、分享图表全空是真实事故）。

## 交付校验
先落临时区 _staging 确认后移 docs/reports/final/。校验：逐层归因闭合脚本核对 · 内联 JS node --check · 明细表总计行闭合 · 配色/小数位/负号一致 · 结论审查 pass(逐条数字剥离+一句话闸门) · 图表 div↔调用无重复 id · 内容完整性对照 GOLDEN(node --check 只查语法、不查运行时与完整性)。

**可执行脚本**：调用 `python3 methods/run.py mth_l1_scan_report_reproduction '<params_json>'`；参数 schema 见 `methods/mth_l1_scan_report_reproduction.json`。**不要手写参数化、不要重新生成代码。**

