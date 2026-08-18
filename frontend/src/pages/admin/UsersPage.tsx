import { useEffect, useState } from "react";
import { Card, Input, message, Select, Table } from "antd";
import { errMsg, listUsers, setUserRole } from "../../api";
import StatusTag, { ROLE } from "../../components/StatusTag";
import { dash, fmtTime } from "../../format";

const ROLE_OPTS = Object.entries(ROLE).map(([value, { label }]) => ({ value, label }));

export default function UsersPage() {
  const [rows, setRows] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [q, setQ] = useState("");

  const load = () => {
    setLoading(true);
    listUsers(q)
      .then(setRows)
      .finally(() => setLoading(false));
  };
  useEffect(load, []);

  const changeRole = async (id: number, role: string) => {
    try {
      await setUserRole(id, role);
      message.success("角色已更新");
      load();
    } catch (e: any) {
      message.error(errMsg(e, "更新失败"));
    }
  };

  const columns = [
    { title: "ID", dataIndex: "id", width: 70 },
    { title: "姓名", dataIndex: "name", width: 180, ellipsis: true },
    { title: "邮箱", dataIndex: "email", width: 220, ellipsis: true, render: dash },
    {
      title: "最近登录",
      dataIndex: "last_login_at",
      width: 160,
      render: (t: string) => fmtTime(t),
    },
    {
      title: "当前角色",
      dataIndex: "role",
      width: 110,
      render: (r: string) => <StatusTag map={ROLE} value={r} />,
    },
    {
      title: "设置角色",
      width: 160,
      render: (_: any, row: any) => (
        <Select
          size="small"
          style={{ width: 130 }}
          value={row.role}
          options={ROLE_OPTS}
          onChange={(v) => changeRole(row.id, v)}
        />
      ),
    },
  ];

  return (
    <Card
      title="用户管理 / 角色分配"
      style={{ borderRadius: 28, minHeight: "calc(100vh - 120px)" }}
      extra={
        <Input.Search
          placeholder="搜姓名/邮箱"
          allowClear
          style={{ width: 200 }}
          onSearch={load}
          onChange={(e) => setQ(e.target.value)}
        />
      }
    >
      <Table
        rowKey="id"
        loading={loading}
        dataSource={rows}
        columns={columns}
        size="middle"
        locale={{ emptyText: "还没有用户登录过。用户通过飞书扫码登录后会自动出现在这里(默认普通用户)。" }}
        pagination={{ pageSize: 15, showTotal: (t) => `共 ${t} 人(仅登录过的)` }}
      />
    </Card>
  );
}
