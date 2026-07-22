import { useEffect, useState } from "react";
import { Button, Card, Input, Space, Table, Tag } from "antd";
import { listAuditLogs } from "../../api";
import SqlModal from "../../components/SqlModal";

const AUTH_ACTIONS = ["login", "logout"]; // 登录/登出:详情留空
const SQL_ACTIONS = ["run_query", "run_query_failed"]; // 跑代码:详情看运行的 SQL

/** detail 可能是对象或 JSON 字符串,统一成对象 */
function asObj(d: any): any {
  if (!d) return null;
  if (typeof d === "string") {
    try {
      return JSON.parse(d);
    } catch {
      return { detail: d };
    }
  }
  return d;
}

export default function AuditPage() {
  const [logs, setLogs] = useState<any[]>([]);
  const [action, setAction] = useState("");
  const [loading, setLoading] = useState(false);
  const [viewRow, setViewRow] = useState<any>(null);

  const load = () => {
    setLoading(true);
    listAuditLogs(action ? { action } : {})
      .then(setLogs)
      .finally(() => setLoading(false));
  };
  useEffect(load, []);

  const detail = asObj(viewRow?.detail);
  const sql = SQL_ACTIONS.includes(viewRow?.action) ? detail?.executed_sql : null;

  const columns = [
    { title: "时间", dataIndex: "created_at", width: 180 },
    { title: "用户", dataIndex: "user_name" },
    { title: "动作", dataIndex: "action", render: (a: string) => <Tag color="geekblue">{a}</Tag> },
    { title: "资源", render: (_: any, r: any) => (r.resource_type ? `${r.resource_type}#${r.resource_id}` : "-") },
    {
      title: "详情",
      width: 110,
      render: (_: any, r: any) => {
        if (AUTH_ACTIONS.includes(r.action)) return <span style={{ color: "#ccc" }}>—</span>;
        const d = asObj(r.detail);
        if (!d || Object.keys(d).length === 0) return <span style={{ color: "#ccc" }}>—</span>;
        return (
          <Button type="link" size="small" onClick={() => setViewRow(r)}>
            点击查阅
          </Button>
        );
      },
    },
    { title: "IP", dataIndex: "ip", width: 120 },
  ];

  return (
    <Card title="审计日志">
      <Space style={{ marginBottom: 12 }}>
        <Input
          placeholder="按动作筛选,如 run_query / download / login"
          value={action}
          onChange={(e) => setAction(e.target.value)}
          style={{ width: 320 }}
          onPressEnter={load}
        />
        <Button type="primary" onClick={load}>
          查询
        </Button>
      </Space>
      <Table rowKey="id" loading={loading} dataSource={logs} columns={columns} />

      <SqlModal
        title={sql ? "运行的 SQL" : "详情"}
        sql={viewRow ? sql || JSON.stringify(detail, null, 2) : null}
        onClose={() => setViewRow(null)}
      />
    </Card>
  );
}
