import { Button, message, Modal } from "antd";

/** 查看只读文本(执行 SQL / 审计详情等),可一键复制。sql 为 null 时不显示。 */
export default function SqlModal({
  sql,
  onClose,
  title = "实际执行的 SQL(参数已代入)",
}: {
  sql: string | null;
  onClose: () => void;
  title?: string;
}) {
  const copy = () => {
    navigator.clipboard?.writeText(sql || "");
    message.success("已复制");
  };
  return (
    <Modal
      title={title}
      open={sql != null}
      onCancel={onClose}
      width={820}
      footer={[
        <Button key="copy" onClick={copy}>复制</Button>,
        <Button key="ok" type="primary" onClick={onClose}>关闭</Button>,
      ]}
    >
      <pre
        style={{
          whiteSpace: "pre-wrap",
          wordBreak: "break-word",
          background: "#f6f8fa",
          padding: 12,
          borderRadius: 4,
          maxHeight: "60vh",
          overflow: "auto",
          fontSize: 12,
          margin: 0,
        }}
      >
        {sql}
      </pre>
    </Modal>
  );
}
