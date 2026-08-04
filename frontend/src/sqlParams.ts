/** SQL 里 `:变量` 占位符的唯一口径:扫描、形态判定、按行分段、受影响行定位。
 *
 *  编辑器的变量卡、镜像层高亮、只读 SQL 视图全部走这里,避免同一条正则散落多处各自漂移。
 *  与后端 app/services/params_service.py 一一对应,改这里需同步后端。 */

/** `:name` 占位符。与后端 render_sql / expand_list_params 使用的裸 `:name` 口径一致。
 *  注意:不做字符串/注释感知,所以 `'%H:%i:%s'` 里的 `:i`、`:s` 同样会被识别成变量 ——
 *  这是与后端一致的已知口径,前端单方面收窄会与后端失同步。
 *
 *  两个常量都只在本模块内用:matchAll 会克隆正则、不改原对象的 lastIndex,共享全局实例安全;
 *  HAS_PARAM 不带 /g,test() 也就没有 lastIndex 状态。 */
const PARAM_RE = /:([a-zA-Z_][a-zA-Z0-9_]*)/g;
const HAS_PARAM = /:[a-zA-Z_]/;

/** 一行里的一段:param 有值表示这段就是 `:变量`,否则是普通文本。 */
export type SqlSegment = { text: string; param?: string };

/** 一个物理行:拆好的段 + 该行是否含变量(即是否为「参数影响行」)。 */
export type SqlLine = { segments: SqlSegment[]; hasParam: boolean };

/** 归一换行:textarea 的 value 由浏览器归一,但从后端读回的字符串不会,不归一会整体错位。 */
const toLines = (sql: string): string[] => (sql || "").replace(/\r\n?/g, "\n").split("\n");

/** 从 SQL 解析出去重的 :变量 名。 */
export function parseVariables(sql: string): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const m of (sql || "").matchAll(PARAM_RE)) {
    if (!seen.has(m[1])) {
      seen.add(m[1]);
      out.push(m[1]);
    }
  }
  return out;
}

/** 变量形态只判 值列表 / 单值:`字段 IN (:x)` / `NOT IN (:x)` → 值列表,其余 → 单值。
 *  与后端 params_service.detect_is_list 保持等价,改此正则需同步后端。 */
export function isListVar(sql: string, name: string): boolean {
  if (!name) return false;
  const esc = name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return new RegExp(`\\bIN\\s*\\(\\s*:${esc}\\b`, "i").test(sql || "");
}

/** 按物理行切分,并把每行拆成「普通文本 / :变量」段。镜像层与只读视图共用。
 *
 *  末尾空行保留:"a\n" 切出 ["a", ""],textarea 也确实显示两行(光标能停在第 2 行)。 */
export function splitSqlLines(sql: string): SqlLine[] {
  return toLines(sql).map((line) => {
    if (!HAS_PARAM.test(line)) return { segments: [{ text: line }], hasParam: false };
    const segments: SqlSegment[] = [];
    let at = 0;
    for (const m of line.matchAll(PARAM_RE)) {
      if (m.index! > at) segments.push({ text: line.slice(at, m.index) });
      segments.push({ text: m[0], param: m[1] });
      at = m.index! + m[0].length;
    }
    if (at < line.length) segments.push({ text: line.slice(at) });
    return { segments, hasParam: true };
  });
}

/** 定位「参数影响行」:renderedSql 是参数已代入的语句,里面的 `:x` 多半已被字面量替换,
 *  因此按 sourceSql(代入前的原始 SQL)的行号来标。
 *
 *  行号 1:1 对应的前提:后端 params_service 的 render_sql / expand_list_params 都是单行正则
 *  替换、不插换行,参数值本身也不可能含换行。两条都没有强制约束,所以行数对不上时退化成
 *  「只标仍带 :x 的行」—— 最坏少标几行,绝不画错行。 */
export function affectedLines(sourceSql: string, renderedSql: string): {
  lines: SqlLine[];
  flags: boolean[];
} {
  const lines = splitSqlLines(renderedSql);
  const fromSource = toLines(sourceSql).map((l) => HAS_PARAM.test(l));
  return {
    lines,
    flags: fromSource.length === lines.length ? fromSource : lines.map((l) => l.hasParam),
  };
}
