import { useEffect, useState } from "react";
import { Button, Card, Form, Input, InputNumber, message, Modal, Select, Space, Table, Tag } from "antd";
import { createDatasource, errMsg, listDatasources, testDatasource } from "../../api";

export default function DatasourcesPage() {
  const [rows, setRows] = useState<any[]>([]);
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm();

  const load = () => listDatasources().then(setRows);
  useEffect(() => {
    load();
  }, []);

  const save = async () => {
    const v = await form.validateFields();
    // Hive 的认证方式放进 extra.auth;MySQL 无需
    const { auth, ...rest } = v;
    const payload = { ...rest, extra: v.engine === "hive" ? { auth: auth || "LDAP" } : {} };
    try {
      await createDatasource(payload);
      message.success("已创建");
      setOpen(false);
      form.resetFields();
      load();
    } catch (e: any) {
      message.error(errMsg(e, "创建失败"));
    }
  };

  const test = async (id: number) => {
    try {
      await testDatasource(id);
      message.success("连接成功");
    } catch (e: any) {
      message.error(errMsg(e, "连接失败"));
    }
  };

  const columns = [
    { title: "名称", dataIndex: "name" },
    { title: "引擎", dataIndex: "engine", render: (e: string) => <Tag>{e}</Tag> },
    { title: "地址", render: (_: any, r: any) => `${r.host}:${r.port}/${r.database || ""}` },
    { title: "账号", dataIndex: "username" },
    {
      title: "操作",
      render: (_: any, r: any) => (
        <Button type="link" onClick={() => test(r.id)}>
          测试连接
        </Button>
      ),
    },
  ];

  return (
    <Card
      title="数据源"
      extra={
        <Button type="primary" onClick={() => setOpen(true)}>
          新增数据源
        </Button>
      }
    >
      <Table rowKey="id" dataSource={rows} columns={columns} />
      <Modal title="新增数据源" open={open} onCancel={() => setOpen(false)} onOk={save}>
        <Form form={form} layout="vertical" initialValues={{ engine: "mysql", port: 3306 }}>
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
            <Form.Item name="password" label="密码">
              <Input.Password />
            </Form.Item>
          </Space>
          <Form.Item noStyle shouldUpdate={(p, c) => p.engine !== c.engine}>
            {({ getFieldValue }) =>
              getFieldValue("engine") === "hive" ? (
                <Form.Item name="auth" label="Hive 认证方式" initialValue="LDAP">
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
