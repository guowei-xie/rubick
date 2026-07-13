import { useEffect, useState } from "react";
import { Button, Card, Input, message, Select, Space, Table, Tooltip } from "antd";
import { SyncOutlined } from "@ant-design/icons";
import { errMsg, listUsers, setUserRole, syncContacts } from "../../api";
import StatusTag, { ROLE } from "../../components/StatusTag";

const ROLE_OPTS = Object.entries(ROLE).map(([value, { label }]) => ({ value, label }));

export default function UsersPage() {
  const [rows, setRows] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [syncing, setSyncing] = useState(false);
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

  const doSync = async () => {
    setSyncing(true);
    const hide = message.loading("正在从飞书同步通讯录…", 0);
    try {
      const r = await syncContacts();
      hide();
      message.success(`同步完成:部门 ${r.departments} 个,用户 ${r.users} 人`);
      load();
    } catch (e: any) {
      hide();
      message.error(errMsg(e, "同步失败(检查飞书通讯录权限是否开通)"));
    } finally {
      setSyncing(false);
    }
  };

  const columns = [
    { title: "ID", dataIndex: "id", width: 70 },
    { title: "姓名", dataIndex: "name" },
    { title: "邮箱", dataIndex: "email", render: (e: string) => e || "-" },
    { title: "部门ID", dataIndex: "department_id", width: 90, render: (d: number) => d || "-" },
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
      extra={
        <Space>
          <Input.Search
            placeholder="搜姓名"
            allowClear
            style={{ width: 180 }}
            onSearch={load}
            onChange={(e) => setQ(e.target.value)}
          />
          <Tooltip title="从飞书拉取部门和用户(需应用已开通通讯录读取权限)">
            <Button icon={<SyncOutlined />} loading={syncing} onClick={doSync}>
              同步飞书通讯录
            </Button>
          </Tooltip>
        </Space>
      }
    >
      <Table rowKey="id" loading={loading} dataSource={rows} columns={columns} />
    </Card>
  );
}
