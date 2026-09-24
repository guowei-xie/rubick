import { JOB_SOURCE, JOB_STATUS } from "../StatusTag";

/**
 * 图表配色与基础样式。
 *
 * 颜色**不新起一套**:成功/失败直接沿用 StatusTag 里那份语义色,
 * 这样「折线图里的绿」和「运行记录表格里那枚绿色 Tag」是同一个十六进制 ——
 * 同一个概念在两处显示成两种绿,读者会以为它们不是一回事。
 *
 * ⚠️ BRAND 与 main.tsx 的 ConfigProvider token `colorPrimary` 是同一个值。
 * 这是引入图表库之后新增的一处「两份真相」,改主题色时两边都要改。
 */
export const BRAND = "#4f3ff0";

/** AntD 预设色名 → 十六进制。StatusTag 存的是预设名(Tag 组件要的),图表要的是色值。 */
const ANTD_HEX: Record<string, string> = {
  green: "#52c41a",
  red: "#ff4d4f",
  blue: "#1677ff",
  orange: "#fa8c16",
  purple: "#722ed1",
  gold: "#faad14",
  geekblue: "#2f54eb",
  cyan: "#13c2c2",
  default: "#8c8c8c",
};

const hex = (antdColor: string) => ANTD_HEX[antdColor] ?? ANTD_HEX.default;

/** 运行成败:与运行记录里的状态 Tag 同色。 */
export const STATUS_COLORS = {
  success: hex(JOB_STATUS.success.color),
  failed: hex(JOB_STATUS.failed.color),
};

/** 四种运行来源:与任务抽屉里的「正式/试跑/定时/API」Tag 同色。 */
export const SOURCE_COLORS = {
  run: hex(JOB_SOURCE.run.color),
  test: hex(JOB_SOURCE.test.color),
  subscribe: hex(JOB_SOURCE.subscribe.color),
  api: hex(JOB_SOURCE.api.color),
};

/** 没有语义的序列(排行、分档)用品牌紫的梯度。 */
export const SERIES_COLORS = [BRAND, "#7a6df5", "#a596f8", "#c9c0fb", "#e4e0fd"];

const INK = "#1f1c2e";
/** 次级文字色。图例/坐标轴/图内标签共用 —— 各板块别再写一遍这个十六进制 */
export const INK_MUTED = "#8894ab";
const GRID = "rgba(136, 148, 171, 0.18)";

/** 每张图共用的底子:留白、字号、网格线、tooltip。各图只覆盖自己特有的部分。 */
export const baseOption = {
  textStyle: { fontFamily: "inherit", color: INK },
  grid: { left: 8, right: 8, top: 28, bottom: 4, containLabel: true },
  tooltip: {
    trigger: "axis" as const,
    axisPointer: { type: "line" as const, lineStyle: { color: GRID } },
    backgroundColor: "rgba(255,255,255,0.97)",
    borderColor: GRID,
    textStyle: { color: INK, fontSize: 12 },
    extraCssText: "box-shadow: 0 10px 30px -12px rgba(71,82,107,.28); border-radius: 10px;",
  },
  legend: {
    top: 0,
    right: 0,
    icon: "roundRect",
    itemWidth: 10,
    itemHeight: 10,
    textStyle: { color: INK_MUTED, fontSize: 12 },
  },
};

/** 时间轴的通用配置(日期标签、淡网格线)。 */
export const timeAxis = {
  type: "category" as const,
  boundaryGap: false,
  axisLine: { lineStyle: { color: GRID } },
  axisTick: { show: false },
  axisLabel: { color: INK_MUTED, fontSize: 11 },
};

export const valueAxis = {
  type: "value" as const,
  axisLine: { show: false },
  axisTick: { show: false },
  axisLabel: { color: INK_MUTED, fontSize: 11 },
  splitLine: { lineStyle: { color: GRID, type: "dashed" as const } },
};
