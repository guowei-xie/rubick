import { useEffect, useState } from "react";
import { Button, Card, Divider, Form, Input, InputNumber, message, Modal, Select, Space, Tag, Typography } from "antd";
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

// 变量形态由 SQL 写法判定:IN / NOT IN → 值列表(业务多选),其余 → 单值文本
function deriveKind(sql: string, name: string): "single" | "list" {
  return listKind(sql, name) ? "list" : "single";
}

// 落库/试跑用的参数形态:kind 与 list_mode 都来自 SQL 写法,一次判定复用(避免重复扫描)
function deriveDef(sql: string, name: string): { name: string; kind: "single" | "list"; list_mode?: "in" | "not_in" } {
  const lk = listKind(sql, name);
  return { name, kind: lk ? "list" : "single", list_mode: lk ?? undefined };
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

/** 变量形态徽标:单值 / 列表(含方向)。 */
function KindTag({ sql, name }: { sql: string; name: string }) {
  const lk = listKind(sql, name);
  if (!lk) return <Tag>单值</Tag>;
  return lk === "not_in" ? <Tag color="red">列表 · 排除</Tag> : <Tag color="blue">列表 · 包含</Tag>;
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
  const [enumSqlTesting, setEnumSqlTesting] = useState<string | null>(null); // 正在测试 enum_sql 的变量
  const [enumSample, setEnumSample] = useState<Record<string, { values: string[]; truncated: boolean }>>({}); // 各变量 enum_sql 测试结果
  const [testValues, setTestValues] = useState<Record<string, any>>({}); // 试跑用的测试值(不入库)
  const [showSql, setShowSql] = useState(false);
  const [expand, setExpand] = useState<Record<string, boolean>>({}); // 卡片里可折叠区(enum_sql / 说明)的展开态
  const [form] = Form.useForm();
  const sqlWatch = Form.useWatch("sql_text", form);

  // 折叠区是否展开:显式点过则用点过的;否则字段有值就默认展开
  const isExpanded = (key: string, hasValue: boolean) => (key in expand ? expand[key] : hasValue);
  const setExpanded = (key: string, open: boolean) => setExpand((e) => ({ ...e, [key]: open }));

  useEffect(() => {
    if (!open) return;
    listDatasources().then(setDatasources);
    setPreview(null);
    setTestValues({});
    setEnumSample({});
    setExpand({});
    if (editingId) {
      getTemplate(editingId).then((d) => {
        const v = d.latest_version || d.published_version;
        form.setFieldsValue({
          name: d.name,
          domain: d.domain,
          description: d.description,
          datasource_id: d.datasource_id,
          timeout_seconds: d.timeout_seconds,
          sql_text: v?.sql_text,
          params: (v?.params || []).map((p: any) => ({
            name: p.name,
            label: p.label,
            description: p.description,
            enum_sql: p.enum_sql,
          })),
        });
      });
    } else {
      form.resetFields();
      form.setFieldsValue({ params: [] });
    }
  }, [open, editingId]);

  // 变量自动同步:SQL 停止输入 400ms 后重扫,与已有配置合并(保留已配、新增新变量、移除已消失的)
  useEffect(() => {
    if (!open) return;
    const t = setTimeout(() => {
      const sql = form.getFieldValue("sql_text") || "";
      const vars = parseVariables(sql);
      const existing: any[] = form.getFieldValue("params") || [];
      if (vars.join(",") === existing.map((p) => p?.name).join(",")) return;
      const byName = Object.fromEntries(existing.map((p) => [p?.name, p]));
      form.setFieldsValue({ params: vars.map((name) => byName[name] || { name, label: name }) });
    }, 400);
    return () => clearTimeout(t);
  }, [sqlWatch, open]);

  // 分析师测试该变量的「枚举值获取 SQL」是否能跑、返回多少候选
  const testEnumSql = async (fieldIndex: number, varName: string) => {
    const datasource_id = form.getFieldValue("datasource_id");
    const sql = form.getFieldValue(["params", fieldIndex, "enum_sql"]);
    if (!datasource_id) return message.warning("请先选择数据源");
    if (!sql) return message.warning("请先填写枚举值获取 SQL");
    setEnumSqlTesting(varName);
    try {
      const res = await runEnumSql({ datasource_id, sql });
      setEnumSample((s) => ({ ...s, [varName]: { values: res.values, truncated: res.truncated } }));
      message.success(`取到 ${res.values.length} 个候选值${res.truncated ? "(已截断)" : ""},可在下方试跑面板勾选`);
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
      // 落库的参数:形态(kind/list_mode)由 SQL 写法判定;另留 展示名/说明/枚举获取SQL
      params: (v.params || []).map((p: any) => {
        const def = deriveDef(v.sql_text, p.name);
        return {
          ...def,
          label: p.label,
          description: p.description || undefined,
          enum_sql: def.kind === "list" ? p.enum_sql || undefined : undefined,
        };
      }),
    };
  };

  const doTestRun = async () => {
    // 试跑只需数据源 + SQL,不强制整表校验(否则任务名等没填会静默不跑)
    const v = form.getFieldsValue(true);
    if (!v.datasource_id) return message.warning("请先选择数据源");
    if (!v.sql_text) return message.warning("请先填写 SQL");
    const defs = (v.params || []).map((p: any) => deriveDef(v.sql_text, p.name));
    // 所有变量运行时必填:试跑同样要求填全测试值
    const values: any = {};
    for (const d of defs) {
      const tv = testValues[d.name];
      const empty = tv == null || tv === "" || (Array.isArray(tv) && !tv.length);
      if (empty) return message.warning(`请先填写变量「${d.name}」的测试值`);
      values[d.name] = tv;
    }
    setTesting(true);
    setPreview(null);
    const hide = message.loading("试跑中,可能要数十秒…", 0);
    try {
      const res = await testRun({
        datasource_id: v.datasource_id,
        sql_text: v.sql_text,
        params: defs,
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

  const doSave = async (payload: any, publish: boolean) => {
    setSaving(true);
    try {
      let id = editingId;
      if (id) await updateTemplate(id, payload);
      else id = (await createTemplate(payload)).id;
      if (publish) await publishTemplate(id!, "编辑器保存并上线");
      message.success(publish ? "已保存并上线" : "已保存为草稿");
      onSaved();
      onClose();
    } catch (e: any) {
      message.error(errMsg(e, "保存失败"));
    } finally {
      setSaving(false);
    }
  };

  const save = async (publish: boolean) => {
    const payload = await collect().catch(() => null);
    if (!payload) return;
    if (!publish) return doSave(payload, false);
    Modal.confirm({
      title: "保存并上线?",
      content: "上线后,被授权的业务用户即可运行该任务的最新版本。",
      okText: "上线",
      onOk: () => doSave(payload, true),
    });
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
        <Button key="publish" type="primary" loading={saving} onClick={() => save(true)}>保存并上线</Button>,
      ]}
    >
      <Form form={form} layout="vertical">
        <Space style={{ width: "100%" }} size="large" wrap>
          <Form.Item name="name" label="任务名称" rules={[{ required: true }]}>
            <Input style={{ width: 280 }} />
          </Form.Item>
          <Form.Item name="datasource_id" label="数据源" rules={[{ required: true }]}>
            <Select
              style={{ width: 240 }}
              options={datasources.map((d) => ({ value: d.id, label: `${d.name} (${d.engine})` }))}
            />
          </Form.Item>
          <Form.Item name="domain" label="业务域">
            <Input style={{ width: 140 }} />
          </Form.Item>
          <Form.Item
            name="timeout_seconds"
            label="查询超时(秒)"
            tooltip="留空按引擎默认:Hive 3600s(长批处理),MySQL 120s。超时会自动终止查询。"
          >
            <InputNumber style={{ width: 140 }} min={1} placeholder="默认" />
          </Form.Item>
        </Space>
        <Form.Item name="description" label="说明">
          <Input.TextArea rows={2} />
        </Form.Item>
        <Form.Item
          name="sql_text"
          label="SQL"
          tooltip="用 :变量 做占位符;写 字段 IN (:x) / NOT IN (:x) 的变量会让业务多选一组值,其余变量业务填单个值。所有变量运行时必填。"
          rules={[{ required: true }]}
        >
          <Input.TextArea rows={7} style={{ fontFamily: "monospace" }} />
        </Form.Item>

        <Divider orientation="left">变量</Divider>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          变量随 SQL 自动识别:<code>字段 IN (:x)</code> / <code>NOT IN (:x)</code> → 列表(业务多选),
          其余 → 单值文本。给变量起个业务能看懂的展示名即可;试跑用的测试值在下方「试跑预览」里填。
        </Typography.Text>
        <Form.List name="params">
          {(fields) => (
            <div style={{ marginTop: 8 }}>
              {fields.length === 0 && (
                <Typography.Text type="secondary">SQL 里还没有 :变量 占位符</Typography.Text>
              )}
              {fields.map((f) => (
                <Card key={f.key} size="small" style={{ marginBottom: 10 }}>
                  <Form.Item noStyle shouldUpdate>
                    {() => {
                      const sql = form.getFieldValue("sql_text") || "";
                      const p = form.getFieldValue(["params", f.name]) || {};
                      const kind = deriveKind(sql, p.name);
                      const sqlKey = `${p.name}:sql`;
                      const descKey = `${p.name}:desc`;
                      const sqlOpen = isExpanded(sqlKey, !!p.enum_sql);
                      const descOpen = isExpanded(descKey, !!p.description);
                      return (
                        <Space direction="vertical" size={8} style={{ width: "100%" }}>
                          <Space wrap align="center">
                            <code style={{ fontSize: 14 }}>:{p.name}</code>
                            <KindTag sql={sql} name={p.name} />
                            <Form.Item {...f} name={[f.name, "label"]} noStyle>
                              <Input placeholder="展示名(给业务看)" style={{ width: 200 }} />
                            </Form.Item>
                            {kind === "list" && !sqlOpen && (
                              <Button type="link" size="small" style={{ padding: 0 }} onClick={() => setExpanded(sqlKey, true)}>
                                + 配置枚举值获取SQL
                              </Button>
                            )}
                            {!descOpen && (
                              <Button type="link" size="small" style={{ padding: 0 }} onClick={() => setExpanded(descKey, true)}>
                                + 添加说明
                              </Button>
                            )}
                          </Space>

                          {kind === "list" && sqlOpen && (
                            <div>
                              <div style={{ marginBottom: 4 }}>
                                <span style={{ fontSize: 13 }}>枚举值获取SQL</span>
                                <Button type="link" size="small" loading={enumSqlTesting === p.name} onClick={() => testEnumSql(f.name, p.name)}>
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
                              {enumSample[p.name] && (
                                <div style={{ marginTop: 6, fontSize: 12, color: "#52c41a" }}>
                                  ✓ 取到 {enumSample[p.name].values.length} 个候选值
                                  {enumSample[p.name].truncated ? "(已截断)" : ""},已作为下方试跑面板的可选项
                                </div>
                              )}
                            </div>
                          )}

                          {descOpen && (
                            <div>
                              <div style={{ marginBottom: 4 }}>
                                <span style={{ fontSize: 13 }}>说明(业务填参时显示)</span>
                                <Button type="link" size="small" onClick={() => setExpanded(descKey, false)}>收起</Button>
                              </div>
                              <Form.Item {...f} name={[f.name, "description"]} noStyle>
                                <Input.TextArea rows={2} placeholder="给业务的填写提示,例如字段含义、格式要求、示例值" />
                              </Form.Item>
                            </div>
                          )}
                        </Space>
                      );
                    }}
                  </Form.Item>
                </Card>
              ))}
            </div>
          )}
        </Form.List>

        <Divider orientation="left">
          试跑预览 <Button size="small" type="primary" ghost loading={testing} style={{ marginLeft: 8 }} onClick={doTestRun}>
            {testing ? "试跑中…" : "测试运行"}
          </Button>
        </Divider>
        <Form.Item noStyle shouldUpdate>
          {() => {
            const sql = form.getFieldValue("sql_text") || "";
            const defs: any[] = form.getFieldValue("params") || [];
            if (!defs.length) return null;
            return (
              <Space direction="vertical" size={6} style={{ width: "100%", marginBottom: 8 }}>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  测试值仅供试跑,不保存、不展示给业务:
                </Typography.Text>
                {defs.map((p) => {
                  const kind = deriveKind(sql, p?.name);
                  return (
                    <Space key={p?.name} wrap align="center">
                      <code style={{ width: 140, display: "inline-block" }}>:{p?.name}</code>
                      {kind === "list" ? (
                        <>
                          <Select
                            mode="tags"
                            allowClear
                            showSearch
                            style={{ width: 360 }}
                            placeholder="勾选枚举候选,或直接输入 / 上传"
                            value={testValues[p?.name] || []}
                            onChange={(vals) => setTestValues((s) => ({ ...s, [p?.name]: vals }))}
                            options={(enumSample[p?.name]?.values || []).map((v) => ({ value: v, label: v }))}
                          />
                          <PasteListButton
                            onAdd={(vals) =>
                              setTestValues((s) => ({
                                ...s,
                                [p?.name]: Array.from(new Set([...(s[p?.name] || []), ...vals])),
                              }))
                            }
                          />
                        </>
                      ) : (
                        <Input
                          style={{ width: 360 }}
                          placeholder="样例值(如 2026-07-01 或 100)"
                          value={testValues[p?.name]}
                          onChange={(e) => setTestValues((s) => ({ ...s, [p?.name]: e.target.value }))}
                        />
                      )}
                    </Space>
                  );
                })}
              </Space>
            );
          }}
        </Form.Item>
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
