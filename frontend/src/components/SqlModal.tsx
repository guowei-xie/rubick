import { useMemo } from "react";
import { Button, message, Modal } from "antd";
import { affectedLines } from "../sqlParams";
import SqlLines from "./SqlLines";
import "../styles/sql-highlight.css";
import { MODAL } from "../widths";

/** 查看只读文本(执行 SQL / 审计详情等),可一键复制。sql 为 null 时不显示。
 *
 *  传了 sourceSql 才开启「参数影响行」高亮 —— 必须 opt-in:审计页把 JSON.stringify(detail)
 *  当 sql 传进来,`{"success":true}` 会被朴素扫描器读成 `:true`,不设开关就会误标。
 *  没传时走原样输出,不做任何扫描,与改造前逐字节一致。 */
export default function SqlModal({
  sql,
  onClose,
  title = "实际执行的 SQL(参数已代入)",
  sourceSql,
}: {
  sql: string | null;
  onClose: () => void;
  title?: string;
  /** 代入参数之前的原始 SQL。执行 SQL 里 `:x` 已被字面量替换,靠源 SQL 的行号定位受影响行。 */
  sourceSql?: string | null;
}) {
  const copy = () => {
    navigator.clipboard?.writeText(sql || "");
    message.success("已复制");
  };

  const highlighted = useMemo(
    () => (sourceSql && sql != null ? affectedLines(sourceSql, sql) : null),
    [sourceSql, sql]
  );

  return (
    <Modal
      title={title}
      open={sql != null}
      onCancel={onClose}
      width={MODAL.sql}
      footer={[
        <Button key="copy" onClick={copy}>复制</Button>,
        <Button key="ok" type="primary" onClick={onClose}>关闭</Button>,
      ]}
    >
      {highlighted && (
        <div style={{ fontSize: 12, color: "var(--ink-secondary)", marginBottom: 8 }}>
          带底色的行为「参数影响行」
        </div>
      )}
      <div className="rk-sql-view">
        {highlighted ? <SqlLines lines={highlighted.lines} flags={highlighted.flags} /> : sql}
      </div>
    </Modal>
  );
}
