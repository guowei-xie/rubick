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


/** 「测试连接」成功后的提示:连通只是底线,真正有用的是「这个账号能取哪些库的数」。
 *  库多时只列前几个 —— 提示条塞不下几百个库名,给出个数与样例就够定位问题了。 */
export const connectOkMsg = (databases: string[] = []): string =>
  databases.length
    ? `连接成功。该账号可访问 ${databases.length} 个库:${databases
        .slice(0, 6)
        .join("、")}${databases.length > 6 ? " 等" : ""}`
    : "连接成功,账号可登录(未能列出可访问的库)";
