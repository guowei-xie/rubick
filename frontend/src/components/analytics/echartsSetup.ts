/**
 * ECharts 的**唯一**注册处。全站只有这里调 use(),各组件只从这里 import。
 *
 * 按需注册而不是 `import * as echarts from "echarts"`:后者会把全部图表类型与组件
 * 一起打进包里。这页是全站唯一用图表的页面(且只给两类管理员看),已经通过 App.tsx 的
 * React.lazy 拆成了独立 chunk,再按需注册把那个 chunk 本身也压到最小。
 *
 * 用 SVGRenderer 而不是 Canvas:产物更小、高 DPI 屏上不糊,而且图元是真实 DOM,
 * 能被页面的 CSS 变量影响 —— 与本仓库其余部分用 CSS 变量统一配色的做法对得上。
 */
import * as echarts from "echarts/core";
import { BarChart, LineChart, PieChart } from "echarts/charts";
import {
  GridComponent,
  LegendComponent,
  TooltipComponent,
} from "echarts/components";
import { SVGRenderer } from "echarts/renderers";

echarts.use([
  LineChart,
  BarChart,
  PieChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  SVGRenderer,
]);

export default echarts;
export type { EChartsOption } from "echarts";
