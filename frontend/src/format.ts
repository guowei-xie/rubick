/** 展示格式化工具,集中一处,避免每个页面各写一份。 */

/**
 * 后端返回的时间是 ISO 字符串(朴素本地时间),转成人读的 "YYYY-MM-DD HH:mm[:ss]"。
 * 空值统一显示 "-"。
 */
export function fmtTime(t?: string | null, withSeconds = true): string {
  if (!t) return "-";
  return t.replace("T", " ").slice(0, withSeconds ? 19 : 16);
}

/** 窄表格里的时间:省掉年份(08-25 14:02)。年份要么看悬停,要么用 fmtTime。
 *  切片放在这儿而不是调用处 —— 这一刀依赖 fmtTime 的输出恰好是 "YYYY-MM-DD HH:mm",
 *  那是本模块自己的约定,换成 dayjs 或加时区后缀时要一起改的也只有这一处。 */
export function fmtDayTime(t?: string | null): string {
  if (!t) return "-";
  return fmtTime(t, false).slice(5);
}

/**
 * 毫秒 → 人读的耗时。分档而不是一律给毫秒:一次取数可能跑 300 毫秒,也可能跑 40 分钟,
 * 「2412000 ms」没人读得出那是多久。空值(还在排队/失败/老数据没记)统一 "-"。
 */
export function fmtDuration(ms?: number | null): string {
  if (ms == null || ms < 0) return "-";
  if (ms < 1000) return `${Math.round(ms)} 毫秒`;
  // 先四舍五入到一位小数再判断,免得 59.97 秒显示成「60.0 秒」
  const sec1 = Math.round(ms / 100) / 10;
  if (sec1 < 60) return `${sec1} 秒`;
  const totalSec = Math.round(ms / 1000);
  const min = Math.floor(totalSec / 60);
  const sec = totalSec % 60;
  if (min < 60) return sec ? `${min} 分 ${sec} 秒` : `${min} 分`;
  const h = Math.floor(min / 60);
  const m = min % 60;
  return m ? `${h} 小时 ${m} 分` : `${h} 小时`;
}

/**
 * 比率(0~1)→ 百分比。**空值给 "-",不给 "0%"** —— 与 dash 同一个空值口径:
 * 「0 次取数里 0 次失败」的失败率不是 0%,是没得算,后端为此专门返回 null。
 *
 * 小数位默认 1 位。同一屏上同一个数字一处 96.5%、另一处 97%,读的人只会以为是两个数 ——
 * 所以精度在这里定一次,调用方别各自 toFixed。
 */
export function fmtPercent(v?: number | null, digits = 1): string {
  if (v == null) return "-";
  return `${(v * 100).toFixed(digits)}%`;
}

/** 表格里的空值占位:null/undefined/"" 都显示 "-"。 */
export function dash(v: unknown): string {
  return v === null || v === undefined || v === "" ? "-" : String(v);
}


/** 「测试连接」的结果提示。分三种,因为它们对用户意味着完全不同的事:
 *  - 能列出库、且数据源那个默认库在其中 → 成功,没别的要做;
 *  - 能列出库、但**进不去数据源那个默认库** → 账号可用,只是不写库名的 SQL 解析不到表。
 *    把能进的库列出来,作者据此写全限定表名(线上就卡在这:用户无从知道自己能进哪个库);
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
      text: `${head}。但它进不去数据源的默认库「${entry}」—— 任务 SQL 请写全限定表名(库名.表名),不写库名会报找不到表`,
    };
  return { level: "success", text: head };
};

/** 选人下拉里「这是谁」的一行:姓名 · 邮箱。同名同事在名单里区分不开,
 *  而选错人的代价是把任务/权限交给了另一个人,所以带上邮箱。 */
export function personLabel(p: { name?: string | null; email?: string | null }): string {
  return [p.name, p.email].filter(Boolean).join(" · ");
}

/** 中文排序用的 collator。建一次复用:排序器里现建会按次比较重建一份,整表排一次是上千次比较。 */
const ZH = new Intl.Collator("zh");

/** 「按任务名排序」的比较器。任务列表与团队页「任务编辑权」两张表共用 ——
 *  「用哪种中文排序规则」是一个决定,有两个真相源的话,哪天改成
 *  `zh-Hans-u-kn-true`(让 `报表2` 排在 `报表10` 前)只会有一处被改,另一张表静默用旧规则。 */
export const byTaskName = (a: { name?: string | null }, b: { name?: string | null }): number =>
  ZH.compare(a.name || "", b.name || "");
