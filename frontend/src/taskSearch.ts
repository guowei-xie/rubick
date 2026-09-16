/** 任务搜索的口径,集中一处 —— 任务列表顶栏与团队页「任务编辑权」两处共用。
 *
 *  收进来的是**词法**(怎么把输入解析成编号 / 文本)、**组合律**(什么算命中)与**次序**
 *  (命中的怎么排),不含字段表 —— 两处该搜的字段本就不同(任务列表搜团队名与被授权人,
 *  团队页搜可编辑的人),字段表留在各自页面。
 *
 *  组合律不共用的话,加一个可搜字段就要在两处各加一遍,漏一处就是
 *  「这边搜得到、那边搜不到」,而且没有任何东西会报错。 */

export type TaskQuery = {
  /** 喂给模糊匹配的那一路:trim + toLowerCase 后的原串 */
  text: string;
  /** 看起来就是一个任务编号时给数,给了就做**精确**匹配;否则 null */
  id: number | null;
};

/**
 * 解析一次搜索词。
 *
 * 只剥**前导 #**,不动 text —— `#` 是我们自己在界面上显示编号的写法(`#128`),
 * 而一键复制给出的是纯数字,两种写法都得认;但任务名里也可能带 #(如「#紧急 日报」),
 * 所以模糊那一路仍拿原串去比,不受影响。
 *
 * 先用 /^\d+$/ 把形状卡死再转数字,挡住三类输入:
 *   - 非数字 → Number() 给 NaN;
 *   - "1e3" / "0x80" / "+12" —— Number() 全认,而主键不可能长这样;
 *   - 超长数字串 → Number("99999999999999999999") 会悄悄变成另一个整数,
 *     拿去比 t.id 就是一次**假命中**(搜到一条根本不是你要的任务)。
 * id 从 1 起,0 与空串一律当没填。
 */
export function parseTaskQuery(raw: string | null | undefined): TaskQuery {
  const text = (raw ?? "").trim().toLowerCase();
  const digits = text.replace(/^#/, "");
  const n = /^\d+$/.test(digits) ? Number(digits) : NaN;
  return { text, id: Number.isSafeInteger(n) && n > 0 ? n : null };
}

/**
 * 「什么算命中」:空串放行 → 编号精确 → 任意一个文本字段包含(大小写不敏感)。
 *
 * 编号与文本是**并集**不是二选一:同事丢来一个 128,他要的既可能是 #128 本体,
 * 也可能是名字里带 128 的那张报表 —— 二选一总会猜错一半。
 *
 * 调用方只需给出「这一页该搜哪些字段」:`matcher(t, [t.name, t.author_name, ...])`。
 */
export const taskMatcher =
  ({ text, id }: TaskQuery) =>
  (t: { id: number }, names: (string | null | undefined)[]): boolean =>
    !text ||
    (id !== null && t.id === id) ||
    names.some((n) => (n || "").toLowerCase().includes(text));

/**
 * 「编号精确命中的那条置顶」。
 *
 * qid 为 null 时两边都不等,自然返回 0 —— 不搜编号时对默认顺序零影响;
 * 搜编号时它最多影响**一行**(id 唯一),其余行的相对次序完全不变。
 */
export const idHitFirst = (qid: number | null, a: { id: number }, b: { id: number }): number =>
  Number(b.id === qid) - Number(a.id === qid);
