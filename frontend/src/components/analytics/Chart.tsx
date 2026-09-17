import { useEffect, useRef } from "react";
import { Empty } from "antd";
import echarts, { EChartsOption } from "./echartsSetup";

/**
 * ECharts 的 React 外壳。自己写而不是装 echarts-for-react:那一层很薄,却多一份版本
 * 对齐成本,而 React 18 严格模式下 effect 会跑两次,init/dispose 的时序自己管更可控。
 *
 * **空数据不画空坐标轴** —— 一张只有网格线的图看起来像加载失败,而不是「这段时间没有数据」。
 */
export default function Chart({
  option,
  height = 240,
  empty,
  emptyText,
}: {
  option: EChartsOption;
  height?: number;
  empty?: boolean;
  emptyText?: string;
}) {
  const box = useRef<HTMLDivElement | null>(null);
  const inst = useRef<echarts.ECharts | null>(null);

  useEffect(() => {
    if (empty || !box.current) return;
    const chart = echarts.init(box.current, undefined, { renderer: "svg" });
    inst.current = chart;
    // 跟随容器宽度:侧边栏与窄屏下图必须重画,否则会溢出到卡片外面
    const ro = new ResizeObserver(() => chart.resize());
    ro.observe(box.current);
    return () => {
      ro.disconnect();
      chart.dispose();
      inst.current = null;
    };
  }, [empty]);

  useEffect(() => {
    // notMerge=true:切时间范围时序列条数可能变少,合并会把上一次的旧序列留在图上
    inst.current?.setOption(option, true);
  }, [option]);

  if (empty)
    return (
      <div
        style={{
          height,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description={emptyText || "这段时间没有数据"}
        />
      </div>
    );

  return <div ref={box} style={{ height, width: "100%" }} />;
}
