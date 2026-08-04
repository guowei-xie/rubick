import { useCallback, useLayoutEffect, useMemo, useRef } from "react";
import type { ChangeEvent } from "react";
import { Input } from "antd";
import type { GetProps, GetRef } from "antd";
import { splitSqlLines } from "../sqlParams";
import SqlLines from "./SqlLines";
import "../styles/sql-highlight.css";

type Props = Omit<GetProps<typeof Input.TextArea>, "value" | "onChange"> & {
  value?: string; // 由 Form.Item 注入
  onChange?: (e: ChangeEvent<HTMLTextAreaElement>) => void; // 由 Form.Item 注入
};

/** 可编辑 SQL 框 + 背景镜像层:含 `:变量` 的行常驻底纹,变量本身加药丸底色。
 *
 *  镜像层只画底色不画字,正文仍由 textarea 渲染,光标 / 选区 / 输入法都是原生行为。
 *  value / onChange 原样透传,因此 antd Form 默认的 getValueFromEvent 照常工作。 */
export default function SqlHighlightArea({ value = "", onChange, className, ...rest }: Props) {
  const taRef = useRef<GetRef<typeof Input.TextArea>>(null);
  const mirrorRef = useRef<HTMLDivElement>(null);
  const gutterRef = useRef<number | null>(null);
  const lines = useMemo(() => splitSqlLines(value), [value]);

  // 滚动同步:高频路径(滚轮/拖拽每秒可触发上百次),只做两次赋值,不碰布局
  const syncScroll = useCallback(() => {
    const ta = taRef.current?.resizableTextArea?.textArea;
    const m = mirrorRef.current;
    if (!ta || !m) return;
    m.scrollTop = ta.scrollTop;
    m.scrollLeft = ta.scrollLeft;
  }, []);

  // 纵向滚动条出现/消失会吃掉 textarea 的可用宽度、改变折行点,镜像补等宽右内边距。
  // 只在内容或尺寸变化时量:滚动过程中 gutter 不可能变。
  const syncMetrics = useCallback(() => {
    const ta = taRef.current?.resizableTextArea?.textArea;
    const m = mirrorRef.current;
    if (!ta || !m) return;
    // 用 offsetWidth/clientWidth 而非 getBoundingClientRect:Modal 开场动画带 transform:scale,会污染 rect
    const cs = getComputedStyle(ta);
    const gutter = Math.max(
      0,
      ta.offsetWidth - ta.clientWidth - parseFloat(cs.borderLeftWidth) - parseFloat(cs.borderRightWidth)
    );
    // 值没变就不写:写自定义属性会让整个镜像子树重算布局
    if (gutter !== gutterRef.current) {
      gutterRef.current = gutter;
      m.style.setProperty("--rk-sql-gutter", `${gutter}px`);
    }
    syncScroll();
  }, [syncScroll]);

  // 打字时浏览器已在 input 事件内同步把光标滚入视野,useLayoutEffect 在绘制前对齐,不抖帧
  useLayoutEffect(syncMetrics, [value, syncMetrics]);

  // 拖拽 resize 手柄 / Modal 改宽会让滚动条出现或消失,需重测。
  // 几何对齐本身不需要 JS:wrapper 相对定位,镜像 inset:0 自动跟随 textarea 的尺寸。
  useLayoutEffect(() => {
    const ta = taRef.current?.resizableTextArea?.textArea;
    if (!ta || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(syncMetrics);
    ro.observe(ta);
    return () => ro.disconnect();
  }, [syncMetrics]);

  return (
    <div className="rk-sql">
      <div className="rk-sql__mirror" ref={mirrorRef} aria-hidden="true">
        <SqlLines lines={lines} />
      </div>
      <Input.TextArea
        rows={7}
        {...rest}
        ref={taRef}
        className={className ? `rk-sql__input ${className}` : "rk-sql__input"}
        value={value}
        onChange={onChange}
        onScroll={syncScroll}
        wrap="soft" /* 钉死软折行:改成 off 会让 textarea 不折行而镜像仍折 */
        spellCheck={false}
      />
    </div>
  );
}
