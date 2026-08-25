# 指标口径

## 首课解锁人数（mq_bam_fstlvl_unlock_cnt · 单位：人）

年课首 Level 班解锁人数（人头口径）。是 [[mq_bam_fstlvl_renewal_rate_d11]] 与 [[mq_bam_fstlvl_dense_out_rate]] 的分母。

首课解锁人数 = SUM(fstlvl_unlock_cnt)。取自年课过程运营班级宽表 ads_bam_annual_process_class_hdf 的班级组粒度 fstlvl_unlock_cnt 列直接汇总。

## 11日首课续报率（mq_bam_fstlvl_renewal_rate_d11 · 单位：%）

首课密集期（解锁 11 日内）续报转化率。分子 user_renewal_day11 为全班粒度 11 日续报人数（含非首课学员），业务上首课解锁与在班一般同期，故作为首课续报口径最佳近似。

11日首课续报率 = SUM(user_renewal_day11) / SUM(fstlvl_unlock_cnt [[mq_bam_fstlvl_unlock_cnt]])。分子为班级组粒度 11 日内续报人数，分母为首课解锁人数；率类必须先汇总分子分母再相除，禁止对行级率算术平均。

## 首课-密集期调出率（mq_bam_fstlvl_dense_out_rate · 单位：%）

首课密集期人员流失比例。分子 = 首课解锁人数 - 续报开始时在班人数（renewal_user_cnt）；分母 = 首课解锁人数。反映首课后至续报期开始前的人员流失率。

首课-密集期调出率 = 1 - SUM(renewal_user_cnt) / SUM(fstlvl_unlock_cnt [[mq_bam_fstlvl_unlock_cnt]])。表示首课解锁到续报开始（密集期结束）之间流失的比例；率类先汇总分子分母再相除。

## T0完课率（mq_bam_finish_rate_t0 · 单位：%）

解锁当日完课率（Level 人次粒度）。分母 unlock_cnt 为 Level 级累计解锁计数（同一学员多 Level 累计），因此为 Level 层面的平均完课率而非学员层面。

T0完课率 = SUM(day0_finish_cnt) / SUM(unlock_cnt)。分子为解锁当日完课人次，分母为解锁人次；率类先汇总分子分母再相除。

## T7完课率（mq_bam_finish_rate_t7 · 单位：%）

解锁后 7 日内完课率（Level 人次粒度）。字段命名 day8_finish_cnt 表义 T7，为业务命名历史遗留（自解锁次日 D1 起数 7 天）。

T7完课率 = SUM(day8_finish_cnt) / SUM(unlock_cnt)。分子字段名为 day8_finish_cnt 但业务语义为解锁 7 日内完课人次；分母为解锁人次；率类先汇总分子分母再相除。

## 赛考覆盖率（mq_bam_saikao_cover_rate · 单位：%）

赛+考总招报覆盖率。saikao_enroll_cnt = 等级考试招报 + 赛事招报之和；表达在班学员中被组织参加赛考的覆盖面。若只看等级考试，用 [[mq_bam_level_enroll_rate]]。

赛考覆盖率 = SUM(saikao_enroll_cnt) / SUM(renewal_user_cnt)。分子 saikao_enroll_cnt 为赛考招报总人数（等级 + 赛事），分母为续报开始时在班人数；率类先汇总分子分母再相除。

## 赛考招报率（mq_bam_level_enroll_rate · 单位：%）

等级考试招报率（仅计等级考试招报，不含赛事）。若需含赛事口径请用 [[mq_bam_saikao_cover_rate]]。

赛考招报率 = SUM(level_enroll_cnt) / SUM(renewal_user_cnt)。分子为等级考试招报人数，分母为续报开始时在班人数；率类先汇总分子分母再相除。

## 赛考招报通过率（mq_bam_level_pass_rate · 单位：%）

等级考试招报通过率。level_result_cnt = 等级通过人数，与 level_enroll_cnt 同一组学员口径；仅覆盖等级考试，不涉及赛事。

赛考招报通过率 = SUM(level_result_cnt) / SUM(level_enroll_cnt)。分子为等级考试通过人数，分母同 [[mq_bam_level_enroll_rate]] 分子（等级考试招报人数）；率类先汇总分子分母再相除。

