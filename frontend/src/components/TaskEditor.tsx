import { useEffect, useState } from "react";
import { Button, Card, Divider, Form, Input, message, Modal, Select, Space, Typography } from "antd";
import {
  createTemplate,
  errMsg,
  getTemplate,
  listDatasources,
  publishTemplate,
  runEnumSql,
  testRun,
  updateTemplate,
} from "../api";
import ResultPreviewTable from "./ResultPreviewTable";
import SqlModal from "./SqlModal";
import { PasteListButton } from "./ParamForm";

// 按 SQL 里 :变量 旁的运算符判定列表写法:not_in / in / null(=单值)。NOT IN 要先判,否则会被 IN 命中
function listKind(sql: string, name: string): "in" | "not_in" | null {
  if (!name) return null;
  const esc = name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  if (new RegExp(`\\bNOT\\s+IN\\s*\\(\\s*:${esc}\\b`, "i").test(sql || "")) return "not_in";
  if (new RegExp(`\\bIN\\s*\\(\\s*:${esc}\\b`, "i").test(sql || "")) return "in";
  return null;
}

// 类型:IN / NOT IN → 列表(多选);其余 → 单值文本
function detectType(sql: string, name: string): "multi_enum" | "text" {
  return listKind(sql, name) ? "multi_enum" : "text";
}

/** 从 SQL 解析出去重的 :变量 名。 */
function parseVariables(sql: string): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const m of sql.matchAll(/:([a-zA-Z_][a-zA-Z0-9_]*)/g)) {
    if (!seen.has(m[1])) {
      seen.add(m[1]);
      out.push(m[1]);
    }
  }
  return out;
}

export default function TaskEditor({
  editingId,
  open,
  onClose,
  onSaved,
}: {
  editingId: number | null;
  open: boolean;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [datasources, setDatasources] = useState<any[]>([]);
  const [preview, setPreview] = useState<any>(null);
  const [testing, setTesting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [enumSqlTesting, setEnumSqlTesting] = useState<number | null>(null); // 正在测试 enum_sql 的行
  const [enumSample, setEnumSample] = useState<Record<number, { values: string[]; truncated: boolean }>>({}); // 各行 enum_sql 测试结果
  const [showSql, setShowSql] = useState(false);
  const [expand, setExpand] = useState<Record<string, boolean>>({}); // 卡片里可折叠区(enum_sql / 说明)的展开态
  const [form] = Form.useForm();

  // 折叠区是否展开:显式点过则用点过的;否则字段有值就默认展开
  const isExpanded = (key: string, hasValue: boolean) => (key in expand ? expand[key] : hasValue);
  const setExpanded = (key: string, open: boolean) => setExpand((e) => ({ ...e, [key]: open }));

  useEffect(() => {
    if (!open) return;
    listDatasources().then(setDatasources);
    setPreview(null);
    if (editingId) {
      getTemplate(editingId).then((d) => {
        const v = d.latest_version || d.published_version;
        form.setFieldsValue({
          name: d.name,
          domain: d.domain,
          description: d.description,
          datasource_id: d.datasource_id,
          sql_text: v?.sql_text,
          params: (v?.params || []).map((p: any) => ({
            name: p.name,
            label: p.label,
            description: p.description,
            enum_sql: p.enum_sql,
            test_values: [], // 测试运行专用,不入库,加载时清空
            test_value: undefined,
          })),
        });
      });
    } else {
      form.resetFields();
      form.setFieldsValue({ params: [] });
    }
  }, [open, editingId]);

  // 变量解析:扫 SQL,合并进参数定义(保留已配置的,新增新变量,移除 SQL 中已不存在的)
  const parse = () => {
    const sql = form.getFieldValue("sql_text") || "";
    const vars = parseVariables(sql);
    if (!vars.length) return message.warning("SQL 里没找到 :变量 占位符");
    const existing: any[] = form.getFieldValue("params") || [];
    const byName = Object.fromEntries(existing.map((p) => [p.name, p]));
    const merged = vars.map(
      (name) => byName[name] || { name, label: name, test_values: [] }
    );
    form.setFieldsValue({ params: merged });
    message.success(`解析到 ${merged.length} 个变量`);
  };

  // 分析师测试该变量的「枚举值获取 SQL」是否能跑、返回多少候选
  const testEnumSql = async (fieldIndex: number) => {
    const datasource_id = form.getFieldValue("datasource_id");
    const sql = form.getFieldValue(["params", fieldIndex, "enum_sql"]);
    if (!datasource_id) return message.warning("请先选择数据源");
    if (!sql) return message.warning("请先填写枚举值获取 SQL");
    setEnumSqlTesting(fieldIndex);
    try {
      const res = await runEnumSql({ datasource_id, sql });
      setEnumSample((s) => ({ ...s, [fieldIndex]: { values: res.values, truncated: res.truncated } }));
      message.success(`取到 ${res.values.length} 个候选值${res.truncated ? "(已截断)" : ""}`);
    } catch (e: any) {
      message.error(errMsg(e, "枚举 SQL 测试失败"));
    } finally {
      setEnumSqlTesting(null);
    }
  };

  const collect = async () => {
    const v = await form.validateFields();
    return {
      ...v,
      // 落库的参数:类型由 SQL 自动判定;只留 变量名/展示名/说明/枚举获取SQL;测试值不入库
      params: (v.params || []).map((p: any) => {
        const type = detectType(v.sql_text, p.name);
        return {
          name: p.name,
          type,
          label: p.label,
          description: p.description || undefined,
          enum_sql: type === "multi_enum" ? p.enum_sql || undefined : undefined,
        };
      }),
    };
  };

  const doTestRun = async () => {
    // 试跑只需数据源 + SQL,不强制整表校验(否则任务名等没填会静默不跑)
    const v = form.getFieldsValue(true);
    if (!v.datasource_id) return message.warning("请先选择数据源");
    if (!v.sql_text) return message.warning("请先填写 SQL");
    // 用「测试值」当各变量的值跑一次;测试时不强制必填(留空=不筛该字段),便于验 SQL 结构
    const params = (v.params || []).map((p: any) => ({
      name: p.name,
      type: detectType(v.sql_text, p.name),
      required: false,
    }));
    const values: any = {};
    for (const p of v.params || []) {
      const type = detectType(v.sql_text, p.name);
      if (type === "multi_enum") {
        if (p.test_values?.length) values[p.name] = p.test_values;
      } else if (p.test_value != null && p.test_value !== "") {
        values[p.name] = p.test_value;
      }
    }
    setTesting(true);
    setPreview(null);
    const hide = message.loading("试跑中,可能要数十秒…", 0);
    try {
      const res = await testRun({
        datasource_id: v.datasource_id,
        sql_text: v.sql_text,
        params,
        values,
        limit: 50,
        template_id: editingId ?? undefined, // 关联已存在任务时,试跑会在运行记录里留一条(标记为试跑)
      });
      hide();
      setPreview(res);
      message.success(`试跑成功,返回 ${res.row_count} 行`);
    } catch (e: any) {
      hide();
      message.error(errMsg(e, "试跑失败"));
    } finally {
      setTesting(false);
    }
  };

  const save = async (publish: boolean) => {
    const payload = await collect().catch(() => null);
    if (!payload) return;
    setSaving(true);
    try {
      let id = editingId;
      if (id) await updateTemplate(id, payload);
      else id = (await createTemplate(payload)).id;
      if (publish) await publishTemplate(id!, "编辑器确认上线");
      message.success(publish ? "已保存并上线" : "已保存为草稿");
      onSaved();
      onClose();
    } catch (e: any) {
      message.error(errMsg(e, "保存失败"));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      title={editingId ? "编辑任务" : "新建任务"}
      open={open}
      onCancel={onClose}
      width={880}
      styles={{ body: { maxHeight: "72vh", overflowY: "auto" } }}
      footer={[
        <Button key="cancel" onClick={onClose}>取消</Button>,
        <Button key="draft" loading={saving} onClick={() => save(false)}>保存草稿</Button>,
        <Button key="publish" type="primary" loading={saving} onClick={() => save(true)}>确认上线</Button>,
      ]}
    >
      <Form form={form} layout="vertical">
        <Space style={{ width: "100%" }} size="large" wrap>
          <Form.Item name="name" label="任务名称" rules={[{ required: true }]}>
            <Input style={{ width: 280 }} />
          </Form.Item>
          <Form.Item name="datasource_id" label="数据源(方言自动匹配)" rules={[{ required: true }]}>
            <Select
              style={{ width: 240 }}
              options={datasources.map((d) => ({ value: d.id, label: `${d.name} (${d.engine})` }))}
            />
          </Form.Item>
          <Form.Item name="domain" label="业务域">
            <Input style={{ width: 140 }} />
          </Form.Item>
        </Space>
        <Form.Item name="description" label="说明">
          <Input.TextArea rows={2} />
        </Form.Item>
        <Form.Item name="sql_text" label="SQL(:变量 是值列表占位符;正选写 字段 IN (:name),反选写 字段 NOT IN (:name))" rules={[{ required: true }]}>
          <Input.TextArea rows={7} style={{ fontFamily: "monospace" }} />
        </Form.Item>

        <Divider orientation="left">
          参数定义 <Button size="small" style={{ marginLeft: 8 }} onClick={parse}>解析变量</Button>
        </Divider>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          点「解析变量」自动带出 SQL 里的变量,类型由写法自动判定:<code>字段 IN (:x)</code> / <code>NOT IN (:x)</code> →
          <b>列表</b>(业务多选);<code>= / &gt;= / &lt;= / LIKE :x</code> → <b>单值</b>(时间范围就写两个单值,如
          <code>&lt;= :d_max AND &gt; :d_min</code>)。测试值仅供下方测试运行,不保存、不展示给业务。
        </Typography.Text>
        <Form.List name="params">
          {(fields, { add, remove }) => (
            <div style={{ marginTop: 8 }}>
              {fields.map((f) => (
                <Card
                  key={f.key}
                  size="small"
                  style={{ marginBottom: 10 }}
                  extra={<Button danger type="link" size="small" onClick={() => remove(f.name)}>删除</Button>}
                  title={
                    <Space wrap align="end">
                      <Form.Item {...f} name={[f.name, "name"]} label="变量名" rules={[{ required: true }]} style={{ marginBottom: 0 }}>
                        <Input placeholder=":name" style={{ width: 160 }} />
                      </Form.Item>
                      <Form.Item {...f} name={[f.name, "label"]} label="展示名称" style={{ marginBottom: 0 }}>
                        <Input placeholder="给业务看的名字" style={{ width: 160 }} />
                      </Form.Item>
                    </Space>
                  }
                >
                  <Form.Item noStyle shouldUpdate>
                    {() => {
                      const type = detectType(form.getFieldValue("sql_text"), form.getFieldValue(["params", f.name, "name"]));
                      const k = String(f.key);
                      const sqlKey = `${k}:sql`;
                      const descKey = `${k}:desc`;
                      const pasteKey = `${k}:paste`;
                      const sqlOpen = isExpanded(sqlKey, !!form.getFieldValue(["params", f.name, "enum_sql"]));
                      const descOpen = isExpanded(descKey, !!form.getFieldValue(["params", f.name, "description"]));
                      const pasteOpen = isExpanded(pasteKey, false);
                      return (
                        <Space direction="vertical" size={8} style={{ width: "100%" }}>
                          {type === "multi_enum" ? (
                            <>
                              {sqlOpen ? (
                                <div>
                                  <div style={{ marginBottom: 4 }}>
                                    <span style={{ fontSize: 13 }}>枚举值获取SQL</span>
                                    <Button type="link" size="small" loading={enumSqlTesting === f.name} onClick={() => testEnumSql(f.name)}>
                                      测试
                                    </Button>
                                    <Button type="link" size="small" onClick={() => setExpanded(sqlKey, false)}>收起</Button>
                                  </div>
                                  <Form.Item {...f} name={[f.name, "enum_sql"]} noStyle>
                                    <Input.TextArea
                                      rows={2}
                                      style={{ fontFamily: "monospace" }}
                                      placeholder="SELECT DISTINCT user_id FROM dim_user ORDER BY 1 —— 业务点「获取枚举值」时跑,返回一列候选"
                                    />
                                  </Form.Item>
                                  {enumSample[f.name] && (
                                    <div style={{ marginTop: 6, fontSize: 12, color: "#52c41a" }}>
                                      ✓ 取到 {enumSample[f.name].values.length} 个候选值
                                      {enumSample[f.name].truncated ? "(已截断)" : ""},已作为下方「测试枚举值」的可选项,可直接勾选
                                    </div>
                                  )}
                                </div>
                              ) : (
                                <Button type="link" size="small" style={{ padding: 0 }} onClick={() => setExpanded(sqlKey, true)}>
                                  + 配置枚举值获取SQL
                                </Button>
                              )}
                              <div>
                                <div style={{ marginBottom: 4 }}>
                                  <span style={{ fontSize: 13 }}>测试枚举值(仅测试运行用,不保存)</span>{" "}
                                  {pasteOpen ? (
                                    <>
                                      <PasteListButton
                                        onAdd={(vals) => {
                                          const cur = form.getFieldValue(["params", f.name, "test_values"]) || [];
                                          form.setFieldValue(
                                            ["params", f.name, "test_values"],
                                            Array.from(new Set([...cur, ...vals]))
                                          );
                                        }}
                                      />
                                      <Button type="link" size="small" onClick={() => setExpanded(pasteKey, false)}>收起</Button>
                                    </>
                                  ) : (
                                    <Button type="link" size="small" style={{ padding: 0 }} onClick={() => setExpanded(pasteKey, true)}>
                                      + 上传/粘贴列表
                                    </Button>
                                  )}
                                </div>
                                <Form.Item {...f} name={[f.name, "test_values"]} noStyle>
                                  <Select
                                    mode="tags"
                                    allowClear
                                    showSearch
                                    style={{ width: "100%" }}
                                    placeholder="从上方「测试」取到的候选里勾选,或直接输入 / 上传"
                                    options={(enumSample[f.name]?.values || []).map((v) => ({ value: v, label: v }))}
                                  />
                                </Form.Item>
                              </div>
                            </>
                          ) : (
                            <Form.Item
                              {...f}
                              name={[f.name, "test_value"]}
                              label="测试值(仅测试运行用,不保存)"
                              style={{ marginBottom: 0 }}
                            >
                              <Input style={{ width: 260 }} placeholder="样例值(如 2026-07-01 或 100)" />
                            </Form.Item>
                          )}

                          {descOpen ? (
                            <div>
                              <div style={{ marginBottom: 4 }}>
                                <span style={{ fontSize: 13 }}>说明(业务填参时显示)</span>
                                <Button type="link" size="small" onClick={() => setExpanded(descKey, false)}>收起</Button>
                              </div>
                              <Form.Item {...f} name={[f.name, "description"]} noStyle>
                                <Input.TextArea rows={2} placeholder="给业务的填写提示,例如字段含义、格式要求、示例值" />
                              </Form.Item>
                            </div>
                          ) : (
                            <Button type="link" size="small" style={{ padding: 0 }} onClick={() => setExpanded(descKey, true)}>
                              + 添加说明
                            </Button>
                          )}
                        </Space>
                      );
                    }}
                  </Form.Item>
                </Card>
              ))}
              <Button onClick={() => add({ test_values: [] })}>+ 手动加变量</Button>
            </div>
          )}
        </Form.List>

        <Divider orientation="left">
          试跑预览 <Button size="small" type="primary" ghost loading={testing} style={{ marginLeft: 8 }} onClick={doTestRun}>
            {testing ? "试跑中…" : "测试运行"}
          </Button>
        </Divider>
        {preview && (
          <>
            <div style={{ margin: "4px 0", color: "#52c41a" }}>
              ✓ 返回 {preview.row_count} 行 · {preview.columns.length} 列
              {preview.executed_sql && (
                <Button type="link" size="small" onClick={() => setShowSql(true)}>查看执行SQL</Button>
              )}
            </div>
            <ResultPreviewTable columns={preview.columns} rows={preview.rows} pageSize={5} />
          </>
        )}
      </Form>
      <SqlModal sql={showSql ? preview?.executed_sql || "" : null} onClose={() => setShowSql(false)} />
    </Modal>
  );
}
