import { useEffect, useState } from "react";
import { Button, Checkbox, Collapse, Divider, Form, Input, InputNumber, message, Modal, Segmented, Select, Space, Tag, Typography } from "antd";
import {
  createTemplate,
  errMsg,
  getTemplate,
  listDatasources,
  ParamDef,
  previewSql,
  runEnumSql,
  testRun,
  updateTemplate,
  ValueListOut,
} from "../api";
import ResultPreviewTable from "./ResultPreviewTable";
import SqlModal from "./SqlModal";
import { PasteListButton } from "./ParamForm";

/** 变量形态只判 值列表 / 单值:`字段 IN (:x)` / `NOT IN (:x)` → 值列表,其余 → 单值。
 *  与后端 params_service.detect_is_list 保持等价,改此正则需同步后端。 */
function isListVar(sql: string, name: string): boolean {
  if (!name) return false;
  const esc = name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return new RegExp(`\\bIN\\s*\\(\\s*:${esc}\\b`, "i").test(sql || "");
}

/** 把一行变量配置转成发给后端的最小 def(name + kind + value_type)。kind 由 SQL 判定,
 *  value_type 兜底文本。试跑 / SQL 预览共用,避免形状漂移。 */
function toDef(sql: string, p: any): Pick<ParamDef, "name" | "kind" | "value_type"> {
  return { name: p.name, kind: isListVar(sql, p.name) ? "list" : "single", value_type: p.value_type || "text" };
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
  const [editStatus, setEditStatus] = useState<string | null>(null); // 编辑对象原状态,用于保存提示如实反映“已上线编辑即更新线上”
  const [enumSqlTesting, setEnumSqlTesting] = useState<string | null>(null); // 正在测试 enum_sql 的变量
  const [enumSample, setEnumSample] = useState<Record<string, ValueListOut>>({}); // 各变量 enum_sql 测试结果
  const [enumEnabled, setEnumEnabled] = useState<Record<string, boolean>>({}); // 作者是否已勾选「允许枚举 SQL」(仅控制编辑框展开)
  const [showSql, setShowSql] = useState(false);
  const [previewSqlText, setPreviewSqlText] = useState<string | null>(null); // SQL 预览浮窗内容
  const [activeKeys, setActiveKeys] = useState<string[]>([]); // 展开的变量卡(默认全部收起)
  const [form] = Form.useForm();
  const sqlWatch = Form.useWatch("sql_text", form);

  useEffect(() => {
    if (!open) return;
    listDatasources().then(setDatasources);
    setPreview(null);
    setEnumSample({});
    setEnumEnabled({});
    if (editingId) {
      getTemplate(editingId).then((d) => {
        setEditStatus(d.status ?? null);
        const v = d.latest_version || d.published_version;
        const params = (v?.params || []).map((p: any) => ({
          name: p.name,
          label: p.label,
          value_type: p.value_type || "text",
          test_value: p.test_value,
          enum_sql: p.enum_sql,
          allow_bulk_input: p.allow_bulk_input,
          enum_sql_duration_ms: p.enum_sql_duration_ms,
        }));
        form.setFieldsValue({
          name: d.name,
          description: d.description,
          datasource_id: d.datasource_id,
          timeout_seconds: d.timeout_seconds,
          sql_text: v?.sql_text,
          params,
        });
        setActiveKeys([]); // 变量卡默认全部收起
      });
    } else {
      form.resetFields();
      form.setFieldsValue({ params: [] });
      setActiveKeys([]);
      setEditStatus(null);
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
      form.setFieldsValue({ params: vars.map((name) => byName[name] || { name, label: name, value_type: "text" }) });
      // 变量卡默认收起:卡头已展示 :名 + 单值/值列表 + 说明,要配再点开(不强制展开新增变量)
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
      setEnumSample((s) => ({ ...s, [varName]: res }));
      // 落库这次测试的获取耗时,业务填参侧作参考展示
      form.setFieldValue(["params", fieldIndex, "enum_sql_duration_ms"], res.duration_ms);
      const took = res.duration_ms != null ? `,耗时 ${res.duration_ms} ms` : "";
      message.success(`取到 ${res.values.length} 个候选值${res.truncated ? "(已截断)" : ""}${took},可作为测试值/业务候选`);
    } catch (e: any) {
      message.error(errMsg(e, "枚举 SQL 测试失败"));
    } finally {
      setEnumSqlTesting(null);
    }
  };

  // 该变量是否已开启枚举 SQL 配置:会话内勾选态优先,默认从已有 enum_sql 派生(仅 UI 层,取消勾选保存时才丢弃)
  const isEnumOn = (p: any) => enumEnabled[p.name] ?? !!p.enum_sql;

  const collect = async () => {
    const v = await form.validateFields();
    const sql = v.sql_text || "";
    return {
      ...v,
      // 落库参数:kind 由 SQL 判定;list 才带 enum_sql / allow_bulk_input;测试值兼作业务示例
      params: (v.params || []).map((p: any) => {
        const list = isListVar(sql, p.name);
        // 枚举 SQL 未勾选「允许」则不落库(取消勾选会话内仅收起编辑框,保存时才丢弃)
        const enumOn = list && isEnumOn(p);
        return {
          name: p.name,
          kind: list ? "list" : "single",
          value_type: p.value_type || "text",
          label: p.label || undefined,
          test_value: normalizeTestValue(p.test_value, list),
          enum_sql: enumOn ? p.enum_sql || undefined : undefined,
          allow_bulk_input: list ? !!p.allow_bulk_input : undefined,
          enum_sql_duration_ms: enumOn ? p.enum_sql_duration_ms ?? undefined : undefined,
        };
      }),
    };
  };

  const doTestRun = async () => {
    // 试跑只需数据源 + SQL,不强制整表校验(否则任务名等没填会静默不跑)
    const v = form.getFieldsValue(true);
    if (!v.datasource_id) return message.warning("请先选择数据源");
    if (!v.sql_text) return message.warning("请先填写 SQL");
    const params: any[] = v.params || [];
    // 所有变量运行时必填:试跑用各变量配置的「测试值」。一次遍历同时产出 defs 与 values
    const defs: Pick<ParamDef, "name" | "kind" | "value_type">[] = [];
    const values: any = {};
    for (const p of params) {
      const def = toDef(v.sql_text, p);
      const tv = normalizeTestValue(p.test_value, def.kind === "list");
      const empty = tv == null || tv === "" || (Array.isArray(tv) && !tv.length);
      if (empty) return message.warning(`请先给变量「${p.name}」填测试值`);
      defs.push(def);
      values[p.name] = tv;
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
      setPreview(res); // 结果浮窗由 preview 是否有值驱动
      message.success(`试跑成功,返回 ${res.row_count} 行`);
    } catch (e: any) {
      hide();
      message.error(errMsg(e, "试跑失败"));
    } finally {
      setTesting(false);
    }
  };

  // SQL 预览:代入当前测试值(含未填)渲染即将执行的 SQL,不连库、供 review。未填变量原样保留 :x
  const doPreviewSql = async () => {
    const v = form.getFieldsValue(true);
    if (!v.sql_text) return message.warning("请先填写 SQL");
    const params: any[] = v.params || [];
    const defs: Pick<ParamDef, "name" | "kind" | "value_type">[] = [];
    const values: any = {};
    for (const p of params) {
      const def = toDef(v.sql_text, p);
      defs.push(def);
      values[p.name] = normalizeTestValue(p.test_value, def.kind === "list"); // 空值照传,后端跳过、保留占位符
    }
    try {
      const res = await previewSql({ sql_text: v.sql_text, params: defs, values });
      setPreviewSqlText(res.rendered_sql);
    } catch (e: any) {
      message.error(errMsg(e, "SQL 预览失败"));
    }
  };

  const save = async () => {
    const payload = await collect().catch(() => null);
    if (!payload) return;
    setSaving(true);
    try {
      if (editingId) await updateTemplate(editingId, payload);
      else await createTemplate(payload);
      // 如实提示:编辑已上线任务会自动更新线上版本;新建为草稿
      message.success(
        !editingId
          ? "已创建(草稿)"
          : editStatus === "published"
            ? "已保存,线上版本已更新"
            : "已保存"
      );
      onSaved();
      onClose();
    } catch (e: any) {
      message.error(errMsg(e, "保存失败"));
    } finally {
      setSaving(false);
    }
  };

  // 单个变量折叠卡(默认展开):头部只读展示,内容体承载可编辑项
  function variablePanel(f: any) {
    const sql = form.getFieldValue("sql_text") || "";
    const p = form.getFieldValue(["params", f.name]) || {};
    const list = isListVar(sql, p.name);
    const sample = enumSample[p.name];
    const enumOn = isEnumOn(p); // 枚举 SQL 编辑框是否展开
    return {
      key: p.name,
      forceRender: true, // 折叠时也注册 Form.Item,保证保存不丢
      label: (
        <Space wrap size={6}>
          <code style={{ fontSize: 14 }}>:{p.name}</code>
          <Tag color={list ? "blue" : "default"}>{list ? "值列表" : "单值"}</Tag>
          {p.label && <Typography.Text type="secondary" style={{ fontSize: 12 }}>{p.label}</Typography.Text>}
        </Space>
      ),
      children: (
        <Space direction="vertical" size={12} style={{ width: "100%" }}>
          <div>
            <div style={{ fontSize: 13, marginBottom: 4 }}>
              类型 <Typography.Text type="secondary" style={{ fontSize: 12 }}>(数值型不加引号,用于 age &gt; :x、LIMIT :n 等;默认文本)</Typography.Text>
            </div>
            <Form.Item {...f} name={[f.name, "value_type"]} noStyle initialValue="text">
              <Segmented options={[{ label: "文本", value: "text" }, { label: "数值", value: "number" }]} />
            </Form.Item>
          </div>

          <div>
            <div style={{ fontSize: 13, marginBottom: 4 }}>变量说明(业务填参时显示为该字段名与提示)</div>
            <Form.Item {...f} name={[f.name, "label"]} noStyle>
              <Input placeholder="例如:开始日期(格式 yyyy-mm-dd)" />
            </Form.Item>
          </div>

          <div>
            <div style={{ fontSize: 13, marginBottom: 4 }}>
              测试值 <Typography.Text type="secondary" style={{ fontSize: 12 }}>(用于试跑,并作为业务填写示例)</Typography.Text>
            </div>
            {list ? (
              <Space wrap align="center" style={{ width: "100%" }}>
                <Form.Item {...f} name={[f.name, "test_value"]} noStyle>
                  <Select
                    mode="tags"
                    allowClear
                    style={{ minWidth: 320 }}
                    placeholder="输入若干示例值,或从候选/粘贴选取"
                    options={(sample?.values || []).map((v) => ({ value: v, label: v }))}
                  />
                </Form.Item>
                <PasteListButton
                  onAdd={(vals) => {
                    const cur: string[] = form.getFieldValue(["params", f.name, "test_value"]) || [];
                    form.setFieldValue(["params", f.name, "test_value"], Array.from(new Set([...cur, ...vals])));
                  }}
                />
              </Space>
            ) : (
              <Form.Item {...f} name={[f.name, "test_value"]} noStyle>
                <Input style={{ maxWidth: 360 }} placeholder="样例值(如 2026-07-01 或 100)" />
              </Form.Item>
            )}
          </div>

          {list && (
            <>
              <Form.Item {...f} name={[f.name, "allow_bulk_input"]} valuePropName="checked" noStyle>
                <Checkbox>允许业务「上传 / 粘贴」批量输入(勾选后业务填参才出现该入口)</Checkbox>
              </Form.Item>
              {/* 枚举 SQL 选填:仿「上传/粘贴」,勾选后再展开 SQL 编辑框 */}
              <Checkbox
                checked={enumOn}
                onChange={(e) => setEnumEnabled((s) => ({ ...s, [p.name]: e.target.checked }))}
              >
                允许以自定义 SQL 提供可选枚举值(勾选后展开 SQL 编辑框)
              </Checkbox>
              {/* 用 display 隐藏而非卸载,取消勾选仅收起、会话内保留已写 SQL */}
              <div style={{ display: enumOn ? undefined : "none" }}>
                <div style={{ fontSize: 13, marginBottom: 4 }}>
                  枚举值获取 SQL <Typography.Text type="secondary" style={{ fontSize: 12 }}>(业务点「获取枚举值」时跑,返回一列候选)</Typography.Text>
                  <Button type="link" size="small" loading={enumSqlTesting === p.name} onClick={() => testEnumSql(f.name, p.name)}>
                    测试
                  </Button>
                </div>
                <Form.Item {...f} name={[f.name, "enum_sql"]} noStyle>
                  <Input.TextArea
                    rows={2}
                    style={{ fontFamily: "monospace" }}
                    placeholder="SELECT DISTINCT user_id FROM dim_user ORDER BY 1"
                  />
                </Form.Item>
                {sample && (
                  <div style={{ marginTop: 6, fontSize: 12, color: "#52c41a" }}>
                    ✓ 取到 {sample.values.length} 个候选值{sample.truncated ? "(已截断)" : ""}
                    {sample.duration_ms != null ? ` · 获取耗时 ${sample.duration_ms} ms` : ""},可用于上方测试值选择
                  </div>
                )}
              </div>
            </>
          )}
        </Space>
      ),
    };
  }

  return (
    <Modal
      title={editingId ? "编辑任务" : "新建任务"}
      open={open}
      onCancel={onClose}
      width={880}
      styles={{ body: { maxHeight: "72vh", overflowY: "auto" } }}
      footer={[
        <Button key="cancel" onClick={onClose}>取消</Button>,
        <Button key="preview" onClick={doPreviewSql}>SQL预览</Button>,
        <Button key="test" loading={testing} onClick={doTestRun}>{testing ? "试跑中…" : "测试运行"}</Button>,
        <Button key="save" type="primary" loading={saving} onClick={save}>
          {editingId ? "保存" : "创建"}
        </Button>,
      ]}
    >
      <Form form={form} layout="vertical">
        <Divider orientation="left" style={{ marginTop: 0 }}>基本信息</Divider>
        <Space style={{ width: "100%" }} size="large" wrap>
          <Form.Item name="name" label="任务名称" rules={[{ required: true }]}>
            <Input style={{ width: 300 }} />
          </Form.Item>
          <Form.Item name="datasource_id" label="数据源" rules={[{ required: true }]}>
            <Select
              style={{ width: 240 }}
              options={datasources.map((d) => ({ value: d.id, label: `${d.name} (${d.engine})` }))}
            />
          </Form.Item>
          <Form.Item
            name="timeout_seconds"
            label="查询超时(秒)"
            tooltip="留空按引擎默认:Hive 3600s(长批处理),MySQL 120s。超时会自动终止查询。"
          >
            <InputNumber style={{ width: 140 }} min={1} placeholder="默认" />
          </Form.Item>
        </Space>
        <Form.Item name="description" label="任务说明">
          <Input.TextArea rows={2} placeholder="这个取数任务是做什么的,给协作者/业务参考" />
        </Form.Item>
        <Divider orientation="left">SQL 语句</Divider>
        <Form.Item
          name="sql_text"
          label="SQL"
          tooltip="用 :变量 做占位符;写 字段 IN (:x) / NOT IN (:x) 的变量会让业务多选一组值,其余变量业务填单个值。所有变量运行时必填。"
          rules={[{ required: true }]}
        >
          <Input.TextArea rows={7} style={{ fontFamily: "monospace" }} placeholder="SELECT ... WHERE dt = :dt AND uid IN (:uids)" />
        </Form.Item>

        <Divider orientation="left">变量配置</Divider>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          变量随 SQL 自动识别:<code>字段 IN (:x)</code> / <code>NOT IN (:x)</code> → 值列表(业务多选),其余 → 单值。
          每个变量可折叠;填「变量说明」与「测试值」即可。
        </Typography.Text>
        <Form.List name="params">
          {(fields) => (
            <div style={{ marginTop: 10 }}>
              {fields.length === 0 ? (
                <Typography.Text type="secondary">SQL 里还没有 :变量 占位符</Typography.Text>
              ) : (
                <Form.Item noStyle shouldUpdate>
                  {() => (
                    <Collapse
                      activeKey={activeKeys}
                      onChange={(k) => setActiveKeys(k as string[])}
                      items={fields.map(variablePanel)}
                    />
                  )}
                </Form.Item>
              )}
            </div>
          )}
        </Form.List>

      </Form>

      {/* 试跑结果浮窗:底部「测试运行」成功后弹出 */}
      <Modal
        title="试跑结果"
        open={!!preview}
        onCancel={() => setPreview(null)}
        width={820}
        footer={[<Button key="ok" type="primary" onClick={() => setPreview(null)}>关闭</Button>]}
      >
        {preview && (
          <>
            <div style={{ margin: "0 0 8px", color: "#52c41a" }}>
              ✓ 返回 {preview.row_count} 行 · {preview.columns.length} 列
              {preview.executed_sql && (
                <Button type="link" size="small" onClick={() => setShowSql(true)}>查看执行SQL</Button>
              )}
            </div>
            <ResultPreviewTable columns={preview.columns} rows={preview.rows} pageSize={5} />
          </>
        )}
      </Modal>

      {/* 只读 SQL 展示:SQL 预览(渲染即将执行,未填保留 :变量)与 查看执行SQL 各用一个 */}
      <SqlModal
        sql={previewSqlText}
        title="SQL 预览(参数已代入,未填变量保留 :变量)"
        onClose={() => setPreviewSqlText(null)}
      />
      <SqlModal sql={showSql ? preview?.executed_sql || "" : null} onClose={() => setShowSql(false)} />
    </Modal>
  );
}

/** 测试值归一:list → 字符串数组;single → 字符串。空值归一为 undefined。 */
function normalizeTestValue(tv: any, list: boolean): any {
  if (list) {
    if (Array.isArray(tv)) return tv.length ? tv : undefined;
    return tv == null || tv === "" ? undefined : [String(tv)];
  }
  if (Array.isArray(tv)) return tv.length ? String(tv[0]) : undefined;
  return tv == null || tv === "" ? undefined : String(tv);
}
