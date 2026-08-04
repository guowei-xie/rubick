/** 展示格式化工具,集中一处,避免每个页面各写一份。 */

/**
 * 后端返回的时间是 ISO 字符串(朴素本地时间),转成人读的 "YYYY-MM-DD HH:mm[:ss]"。
 * 空值统一显示 "-"。
 */
export function fmtTime(t?: string | null, withSeconds = true): string {
  if (!t) return "-";
  return t.replace("T", " ").slice(0, withSeconds ? 19 : 16);
}

/** 表格里的空值占位:null/undefined/"" 都显示 "-"。 */
export function dash(v: unknown): string {
  return v === null || v === undefined || v === "" ? "-" : String(v);
}
