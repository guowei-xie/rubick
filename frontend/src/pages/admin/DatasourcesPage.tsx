import { useEffect, useState } from "react";
import { Button, Card, Form, Input, InputNumber, message, Modal, Popconfirm, Select, Space, Table, Tag } from "antd";
import {
  createDatasource,
  deleteDatasource,
  errMsg,
  listDatasources,
  testDatasource,
  updateDatasource,
} from "../../api";

export default function DatasourcesPage() {
  const [rows, setRows] = useState<any[]>([]);
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<any>(null); // null=新增,否则为被编辑行
  const [saving, setSaving] = useState(false);
  const [form] = Form.useForm();

  const load = () => listDatasources().then(setRows);
  useEffect(() => {
    load();
  }, []);

  const openCreate = () => {
    setEditing(null);
    form.resetFields();
    form.setFieldsValue({ engine: "mysql", port: 3306, auth: "LDAP" });
    setOpen(true);
  };

  const openEdit = (row: any) => {
    setEditing(row);
    form.resetFields();
    form.setFieldsValue({
      name: row.name,
      engine: row.engine,
      host: row.host,
      port: row.port,
      database: row.database,
      username: row.username,
      password: "", // 留空表示不修改
      auth: row.extra?.auth || "LDAP",
    });
    setOpen(true);
  };

  const save = async () => {
    const v = await form.validateFields();
    const { auth, password, ...rest } = v;
    const payload: any = { ...rest, extra: v.engine === "hive" ? { auth: auth || "LDAP" } : {} };
    // 编辑时密码留空 = 不修改;新增时按填的传
    if (password) payload.password = password;
    else if (!editing) payload.password = null;
    setSaving(true);
    try {
      if (editing) {
        await updateDatasource(editing.id, payload);
        message.success("已更新");
      } else {
        await createDatasource(payload);
        message.success("已创建");
      }
      setOpen(false);
      load();
    } catch (e: any) {
      message.error(errMsg(e, editing ? "更新失败" : "创建失败"));
    } finally {
      setSaving(false);
    }
  };

  const test = async (id: number) => {
    const hide = message.loading("连接测试中…", 0);
    try {
      await testDatasource(id);
      hide();
      message.success("连接成功");
    } catch (e: any) {
      hide();
      message.error(errMsg(e, "连接失败"));
    }
  };

  const remove = async (id: number) => {
    try {
      await deleteDatasource(id);
      message.success("已删除");
      load();
    } catch (e: any) {
      message.error(errMsg(e, "删除失败"));
    }
  };

  const columns = [
    { title: "名称", dataIndex: "name" },
    { title: "引擎", dataIndex: "engine", render: (e: string) => <Tag>{e}</Tag> },
    { title: "地址", render: (_: any, r: any) => `${r.host}:${r.port}/${r.database || ""}` },
    { title: "账号", dataIndex: "username" },
    {
      title: "操作",
      width: 240,
      render: (_: any, r: any) => (
        <Space size={0}>
          <Button type="link" size="small" onClick={() => test(r.id)}>
            测试连接
          </Button>
          <Button type="link" size="small" onClick={() => openEdit(r)}>
            编辑
          </Button>
          <Popconfirm
            title={`删除数据源「${r.name}」?`}
            description="删除后无法恢复;仍被任务引用时无法删除。"
            okText="删除"
            okButtonProps={{ danger: true }}
            cancelText="取消"
            onConfirm={() => remove(r.id)}
          >
            <Button type="link" size="small" danger>
              删除
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <Card
      title="数据源"
      style={{ borderRadius: 28, minHeight: "calc(100vh - 120px)" }}
      extra={
        <Button type="primary" onClick={openCreate}>
          新增数据源
        </Button>
      }
    >
      <Table rowKey="id" dataSource={rows} columns={columns} />
      <Modal
        title={editing ? `编辑数据源:${editing.name}` : "新增数据源"}
        open={open}
        onCancel={() => setOpen(false)}
        onOk={save}
        confirmLoading={saving}
      >
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="名称" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Space>
            <Form.Item name="engine" label="引擎" rules={[{ required: true }]}>
              <Select
                style={{ width: 120 }}
                options={[
                  { value: "mysql", label: "MySQL" },
                  { value: "hive", label: "Hive" },
                ]}
              />
            </Form.Item>
            <Form.Item name="host" label="Host" rules={[{ required: true }]}>
              <Input style={{ width: 180 }} />
            </Form.Item>
            <Form.Item name="port" label="Port" rules={[{ required: true }]}>
              <InputNumber />
            </Form.Item>
          </Space>
          <Form.Item name="database" label="Database">
            <Input />
          </Form.Item>
          <Space>
            <Form.Item name="username" label="只读账号" rules={[{ required: true }]}>
              <Input />
            </Form.Item>
            <Form.Item
              name="password"
              label="密码"
              extra={editing ? "留空则不修改现有密码" : undefined}
            >
              <Input.Password placeholder={editing ? "留空则不修改" : ""} />
            </Form.Item>
          </Space>
          <Form.Item noStyle shouldUpdate={(p, c) => p.engine !== c.engine}>
            {({ getFieldValue }) =>
              getFieldValue("engine") === "hive" ? (
                <Form.Item name="auth" label="Hive 认证方式">
                  <Select
                    style={{ width: 200 }}
                    options={["LDAP", "NONE", "NOSASL", "CUSTOM", "KERBEROS"].map((a) => ({
                      value: a,
                      label: a,
                    }))}
                  />
                </Form.Item>
              ) : null
            }
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  );
}
