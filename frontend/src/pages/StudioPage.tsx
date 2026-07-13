import { useEffect, useState } from "react";
import {
  Button,
  Card,
  Divider,
  Form,
  Input,
  message,
  Modal,
  Select,
  Space,
  Table,
  Tag,
} from "antd";
import {
  archiveTemplate,
  createTemplate,
  getTemplate,
  listDatasources,
  listTemplates,
  publishTemplate,
  errMsg,
  testRun,
  updateTemplate,
} from "../api";
import GrantModal from "../components/GrantModal";
import StatusTag, { TEMPLATE_STATUS } from "../components/StatusTag";

const PARAM_TYPES = ["string", "number", "date", "daterange", "enum", "multi_enum"];

export default function StudioPage() {
  const [templates, setTemplates] = useState<any[]>([]);
  const [datasources, setDatasources] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [editorOpen, setEditorOpen] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [preview, setPreview] = useState<any>(null);
  const [testing, setTesting] = useState(false);
  const [grantTarget, setGrantTarget] = useState<any>(null);
  const [form] = Form.useForm();

  const load = () => {
    setLoading(true);
    listTemplates(true)
      .then(setTemplates)
      .finally(() => setLoading(false));
  };
  useEffect(() => {
    load();
    listDatasources().then(setDatasources);
  }, []);

  const openNew = () => {
    setEditingId(null);
    setPreview(null);
    form.resetFields();
    form.setFieldsValue({ dialect: "mysql", params: [] });
    setEditorOpen(true);
  };

  const openEdit = async (row: any) => {
    const detail = await getTemplate(row.id);
    const v = detail.latest_version || detail.published_version;
    setEditingId(row.id);
    setPreview(null);
    form.setFieldsValue({
      name: detail.name,
      domain: detail.domain,
      description: detail.description,
      datasource_id: detail.datasource_id,
      dialect: detail.dialect,
      sql_text: v?.sql_text,
      params: (v?.params || []).map((p: any) => ({
        ...p,
        options: (p.options || []).join(","),
      })),
    });
    setEditorOpen(true);
  };

  const collectPayload = async () => {
    const v = await form.validateFields();
    return {
      ...v,
      params: (v.params || []).map((p: any) => ({
        name: p.name,
        type: p.type || "string",
        label: p.label,
        required: p.required ?? true,
        default: p.default,
        options: p.options ? String(p.options).split(",").map((s: string) => s.trim()) : undefined,
      })),
    };
  };

  const doTestRun = async () => {
    const payload = await collectPayload().catch(() => null);
    if (!payload) return;
    const values: any = {};
    for (const p of payload.params) if (p.default != null) values[p.name] = p.default;
    setTesting(true);
    setPreview(null);
    const hide = message.loading("试跑中,Hive 查询可能要数十秒,请稍候…", 0);
    try {
      const res = await testRun({
        datasource_id: payload.datasource_id,
        dialect: payload.dialect,
        sql_text: payload.sql_text,
        params: payload.params,
        values,
        limit: 50,
      });
      hide();
      setPreview(res);
      message.success(`试跑成功,返回 ${res.row_count} 行(用默认参数,最多显示 50 行)`);
    } catch (e: any) {
      hide();
      message.error(errMsg(e, "试跑失败"));
    } finally {
      setTesting(false);
    }
  };

  const save = async () => {
    const payload = await collectPayload();
    try {
      if (editingId) await updateTemplate(editingId, payload);
      else await createTemplate(payload);
      message.success("已保存为草稿");
      setEditorOpen(false);
      load();
    } catch (e: any) {
      message.error(errMsg(e, "保存失败"));
    }
  };

  const doPublish = async (row: any) => {
    Modal.confirm({
      title: `发布模板「${row.name}」?`,
      content: "发布 = 验收通过并对业务用户可运行(将发布最新版本)。",
      onOk: async () => {
        await publishTemplate(row.id, "工作台发布");
        message.success("已发布");
        load();
      },
    });
  };

  const columns = [
    { title: "名称", dataIndex: "name" },
    { title: "方言", dataIndex: "dialect", render: (d: string) => <Tag>{d}</Tag> },
    {
      title: "状态",
      dataIndex: "status",
      render: (s: string) => <StatusTag map={TEMPLATE_STATUS} value={s} />,
    },
    {
      title: "操作",
      render: (_: any, row: any) => (
        <Space>
          <Button type="link" onClick={() => openEdit(row)}>
            编辑
          </Button>
          <Button type="link" onClick={() => setGrantTarget(row)}>
            授权
          </Button>
          {row.status !== "published" && (
            <Button type="link" onClick={() => doPublish(row)}>
              发布
            </Button>
          )}
          {row.status === "published" && (
            <Button type="link" danger onClick={() => archiveTemplate(row.id).then(load)}>
              下线
            </Button>
          )}
        </Space>
      ),
    },
  ];

  return (
    <Card
      title="商分工作台"
      extra={
        <Button type="primary" onClick={openNew}>
          新建模板
        </Button>
      }
    >
      <Table rowKey="id" loading={loading} dataSource={templates} columns={columns} />

      <Modal
        title={editingId ? "编辑模板(将生成新版本)" : "新建模板"}
        open={editorOpen}
        onCancel={() => setEditorOpen(false)}
        onOk={save}
        okText="保存草稿"
        width={860}
        styles={{ body: { maxHeight: "70vh", overflowY: "auto" } }}
      >
        <Form form={form} layout="vertical">
          <Space style={{ width: "100%" }} size="large">
            <Form.Item name="name" label="模板名称" rules={[{ required: true }]}>
              <Input style={{ width: 260 }} />
            </Form.Item>
            <Form.Item name="domain" label="业务域">
              <Input style={{ width: 160 }} />
            </Form.Item>
            <Form.Item name="dialect" label="方言" rules={[{ required: true }]}>
              <Select
                style={{ width: 120 }}
                options={[
                  { value: "mysql", label: "MySQL" },
                  { value: "hive", label: "Hive" },
                ]}
              />
            </Form.Item>
            <Form.Item name="datasource_id" label="数据源" rules={[{ required: true }]}>
              <Select
                style={{ width: 180 }}
                options={datasources.map((d) => ({
                  value: d.id,
                  label: `${d.name} (${d.engine})`,
                }))}
              />
            </Form.Item>
          </Space>
          <Form.Item name="description" label="说明">
            <Input.TextArea rows={2} />
          </Form.Item>
          <Form.Item
            name="sql_text"
            label="SQL(变量用 :name 占位)"
            rules={[{ required: true }]}
          >
            <Input.TextArea rows={6} style={{ fontFamily: "monospace" }} />
          </Form.Item>

          <Divider orientation="left">参数定义</Divider>
          <Form.List name="params">
            {(fields, { add, remove }) => (
              <>
                {fields.map((f) => (
                  <Space key={f.key} align="baseline" style={{ display: "flex", marginBottom: 8 }}>
                    <Form.Item {...f} name={[f.name, "name"]} rules={[{ required: true }]}>
                      <Input placeholder="变量名" style={{ width: 120 }} />
                    </Form.Item>
                    <Form.Item {...f} name={[f.name, "type"]}>
                      <Select
                        placeholder="类型"
                        style={{ width: 120 }}
                        options={PARAM_TYPES.map((t) => ({ value: t, label: t }))}
                      />
                    </Form.Item>
                    <Form.Item {...f} name={[f.name, "label"]}>
                      <Input placeholder="显示名" style={{ width: 120 }} />
                    </Form.Item>
                    <Form.Item {...f} name={[f.name, "default"]}>
                      <Input placeholder="默认值" style={{ width: 120 }} />
                    </Form.Item>
                    <Form.Item {...f} name={[f.name, "options"]}>
                      <Input placeholder="枚举项,逗号分隔" style={{ width: 160 }} />
                    </Form.Item>
                    <Button danger type="link" onClick={() => remove(f.name)}>
                      删除
                    </Button>
                  </Space>
                ))}
                <Button onClick={() => add({ type: "string", required: true })}>+ 添加参数</Button>
              </>
            )}
          </Form.List>

          <Divider orientation="left">试跑预览(用默认参数,不落库)</Divider>
          <Button type="primary" ghost loading={testing} onClick={doTestRun}>
            {testing ? "试跑中…" : "测试运行"}
          </Button>
          {preview && (
            <>
              <div style={{ margin: "12px 0 4px", color: "#52c41a" }}>
                ✓ 返回 {preview.row_count} 行 · {preview.columns.length} 列
                {preview.truncated && "(已截断)"}
              </div>
              <Table
                size="small"
                bordered
                scroll={{ x: "max-content" }}
                rowKey={(_, i) => String(i)}
                pagination={{ pageSize: 5 }}
                dataSource={preview.rows.map((r: any[], i: number) => {
                  const o: any = { _i: i };
                  preview.columns.forEach((c: string, ci: number) => (o[c] = r[ci] == null ? "" : String(r[ci])));
                  return o;
                })}
                columns={preview.columns.map((c: string, ci: number) => ({
                  title: c,
                  dataIndex: c,
                  key: `${c}_${ci}`,
                  ellipsis: true,
                }))}
              />
            </>
          )}
        </Form>
      </Modal>

      <GrantModal
        templateId={grantTarget?.id ?? null}
        templateName={grantTarget?.name}
        open={!!grantTarget}
        onClose={() => setGrantTarget(null)}
      />
    </Card>
  );
}
