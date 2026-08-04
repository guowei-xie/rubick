import { memo } from "react";
import type { SqlLine } from "../sqlParams";

/** 逐行渲染 SQL:「参数影响行」打底纹,行内 `:变量` 加药丸。编辑器镜像层与只读视图共用。
 *
 *  flags 省略时按每行自身是否含 :变量 判定(编辑器镜像层);只读视图传 flags,因为那里的
 *  参数多半已被字面量替换,得按代入前的源 SQL 行号来标。
 *
 *  memo:lines 已在调用方按内容 memo 过,包一层可让 SQL 没变的重渲染整片跳过 —— 编辑器所在的
 *  TaskEditor 会因试跑 / 保存 / 展开变量卡等无关状态频繁重渲染。 */
function SqlLines({ lines, flags }: { lines: SqlLine[]; flags?: boolean[] }) {
  return (
    <>
      {lines.map((ln, i) => (
        <div
          key={i}
          className={(flags ? flags[i] : ln.hasParam) ? "rk-sql-line is-param" : "rk-sql-line"}
        >
          {ln.segments.map((s, j) =>
            s.param ? (
              <span key={j} className="rk-sql-tok">{s.text}</span>
            ) : (
              s.text
            )
          )}
        </div>
      ))}
    </>
  );
}

export default memo(SqlLines);
