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


/** 「测试连接」的结果提示。分三种,因为它们对用户意味着完全不同的事:
 *  - 能列出库、且当前入口库在其中 → 成功,没别的要做;
 *  - 能列出库、但**进不去当前入口库** → 账号可用,只是入口库填错了。直接把该填哪个说出来
 *    (线上就卡在这:账号进不去数据源那个默认库,而用户无从知道自己能进哪个);
 *  - 一个库都列不出 → 账号登得进去但没有任何数据权限,得找数仓授权。
 *  库多时只列前几个:提示条塞不下几百个库名,给出个数与样例就够定位问题了。 */
export const connectOkMsg = (
  databases?: string[],
  entry?: string | null
): { level: "success" | "warning"; text: string } => {
  const dbs = databases ?? [];
  if (!dbs.length)
    return {
      level: "warning",
      text: "账号可登录,但它在数仓里看不到任何库 —— 说明这套账号还没有任何数据权限,请找数仓管理员为它授权",
    };
  const sample = dbs.slice(0, 6).join("、") + (dbs.length > 6 ? " 等" : "");
  const head = `连接成功。该账号可访问 ${dbs.length} 个库:${sample}`;
  if (entry && !dbs.includes(entry))
    return {
      level: "warning",
      text: `${head}。但它进不去当前入口库「${entry}」—— 请把「入口库」改成上面列出的其中一个,任务才跑得起来`,
    };
  return { level: "success", text: head };
};
