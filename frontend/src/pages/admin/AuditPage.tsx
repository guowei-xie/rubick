import { useEffect, useState } from "react";
import { Button, Card, Input, Space, Table, Tag, Typography } from "antd";
import { listAuditLogs } from "../../api";

export default function AuditPage() {
  const [logs, setLogs] = useState<any[]>([]);
  const [action, setAction] = useState("");
  const [loading, setLoading] = useState(false);

  const load = () => {
    setLoading(true);
    listAuditLogs(action ? { action } : {})
      .then(setLogs)
      .finally(() => setLoading(false));
  };
  useEffect(load, []);

  const columns = [
    { title: "时间", dataIndex: "created_at", width: 180 },
    { title: "用户", dataIndex: "user_name" },
    { title: "动作", dataIndex: "action", render: (a: string) => <Tag color="geekblue">{a}</Tag> },
    { title: "资源", render: (_: any, r: any) => (r.resource_type ? `${r.resource_type}#${r.resource_id}` : "-") },
    {
      title: "详情",
      dataIndex: "detail",
      render: (d: any) => (
        <Typography.Text style={{ fontSize: 12 }} code>
          {JSON.stringify(d)}
        </Typography.Text>
      ),
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
    </Card>
  );
}
