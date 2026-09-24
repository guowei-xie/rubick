import { Tooltip } from "antd";
import { Link } from "react-router-dom";
import { Metric } from "../../api";
import { fmtDayTime, fmtPercent } from "../../format";

/**
 * 各板块的数字卡统一用这套响应式列宽。**别在每个板块各写一份** ——
 * 改一次栅格要改四个文件,而漏掉的那个只在某一档屏宽下看得出来。
 */
export const CARD_COL = { xs: 12, sm: 8, lg: 6, xxl: 4 };

/** 数字怎么显示成人话。百分比不能当整数渲染。 */
export type MetricFormat = "int" | "percent";

/** 百分比走 format.ts,与别的页面同一种写法。 */
export function render(value: number, format: MetricFormat): string {
  if (format === "percent") return fmtPercent(value);
  return value.toLocaleString("zh-CN");
}

/**
 * 一张指标卡。**三种状态在视觉上必须分得开** —— 这是整块板可信度的地基:
 *
 *   有值            → 大号数字(+ 环比)
 *   有过、本区间是 0 → 大号 0 + 绿色「本区间没有发生」,这是**正向结果**,不是空态
 *   从来没有过       → 灰色「—」+「还没有数据」+ 指路
 *
 * 把最后一种也渲染成 0,新部署的管理员会看到一屏「失败 0、超时 0、闲置 0」,
 * 以为平台完美无缺 —— 而真相是一次都没跑过。这个错误没有任何提示,所以只能靠渲染分开。
 */
export default function MetricCard({
  label,
  metric,
  format = "int",
  note,
  to,
  suffix,
}: {
  label: string;
  metric?: Metric | null;
  format?: MetricFormat;
  /** 口径说明。来自服务端的 metric_notes —— 前端不自己写一份 */
  note?: string;
  /** 可下钻时给一个目标路由,整张卡变成链接 */
  to?: string;
  suffix?: React.ReactNode;
}) {
  const m = metric ?? null;
  const empty = !m || (m.value === null && !m.has_data);
  const zeroButSeen = !!m && m.value === 0 && m.has_data;

  const body = (
    <div className="rk-metric">
      <div className="rk-metric-label">
        <span>{label}</span>
        {/* 「此刻」标记:这个数不吃上方的时间范围。不标的话,用户切了范围看见它纹丝不动
            会当成 bug —— 这是这类看板最常见的一次误读 */}
        {m && !m.windowed && (
          <Tooltip title="该指标反映当前状态，不随上方的时间范围变化">
            <span className="rk-metric-asof">此刻</span>
          </Tooltip>
        )}
      </div>

      {empty ? (
        <>
          <div className="rk-metric-value rk-metric-empty">—</div>
          <div className="rk-metric-hint">还没有数据</div>
        </>
      ) : (
        <>
          <div className="rk-metric-value">
            {m!.value === null ? "—" : render(m!.value, format)}
            {suffix}
          </div>
          <div className="rk-metric-hint">
            {zeroButSeen ? (
              <span className="rk-metric-good">
                本区间没有发生
                {m!.last_event_at ? ` · 最近一次 ${fmtDayTime(m!.last_event_at)}` : ""}
              </span>
            ) : (
              <Delta metric={m!} format={format} />
            )}
          </div>
        </>
      )}
    </div>
  );

  const card = note ? <Tooltip title={note}>{body}</Tooltip> : body;
  return to ? (
    <Link to={to} className="rk-metric-link">
      {card}
    </Link>
  ) : (
    card
  );
}

/** 环比。只在上一区间**有数**时显示 —— 从 0 涨到 5 的「+∞%」不是信息。 */
function Delta({ metric, format }: { metric: Metric; format: MetricFormat }) {
  const { value, prev_value } = metric;
  if (value === null || prev_value === null || prev_value === undefined) return null;
  if (!prev_value) return <span className="rk-metric-sub">上一区间没有数据</span>;
  const diff = value - prev_value;
  if (!diff) return <span className="rk-metric-sub">与上一区间持平</span>;
  return (
    <span className={diff > 0 ? "rk-metric-up" : "rk-metric-down"}>
      {diff > 0 ? "↑" : "↓"} {fmtPercent(Math.abs(diff / prev_value), 0)}
      <span className="rk-metric-sub"> 较上一区间</span>
    </span>
  );
}
