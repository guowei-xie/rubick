import { useEffect, useState } from "react";
import { Alert, Button, Checkbox, Collapse, Divider, Form, Input, InputNumber, message, Modal, Segmented, Select, Space, Switch, Tag, TimePicker, Typography } from "antd";
import dayjs from "dayjs";
import {
  createTemplate,
  TeamCredentialStatus,
  EnumSample,
  errMsg,
  getTemplate,
  listDatasources,
  listTeams,
  myTeamCredentials,
  ParamDef,
  previewSql,
  runEnumSql,
  testRun,
  transferTaskTeam,
  updateTemplate,
  withBase,
} from "../api";
import ResultPreviewTable from "./ResultPreviewTable";
import SqlModal from "./SqlModal";
import SqlHighlightArea from "./SqlHighlightArea";
import { PasteListButton } from "./ParamForm";
import { isPlatformAdmin, isTeamAdminOf, myTeams, useAuth } from "../auth";
import { isListVar, parseVariables } from "../sqlParams";

/** 把一行变量配置转成发给后端的最小 def(name + kind + value_type)。kind 由 SQL 判定,
 *  value_type 兜底文本。试跑 / SQL 预览共用,避免形状漂移。 */
function toDef(sql: string, p: any): Pick<ParamDef, "name" | "kind" | "value_type"> {
  return { name: p.name, kind: isListVar(sql, p.name) ? "list" : "single", value_type: p.value_type || "text" };
}

/** 后端 subscription 配置 → 订阅区五个表单字段。编辑回填与新建默认(sub 缺省)共用,
 *  订阅计划加字段时只改这里 + collect 的组装,两个 setFieldsValue 分支不再各写一份。 */
function subFormFields(sub?: { enabled?: boolean; freq?: string; days?: number[]; at_time?: string }) {
  return {
    sub_enabled: !!sub?.enabled,
    sub_freq: sub?.freq || "daily",
    sub_weekdays: sub?.freq === "weekly" ? sub.days : [],
    sub_monthdays: sub?.freq === "monthly" ? sub.days : [],
    sub_time: dayjs(sub?.at_time || "09:00", "HH:mm"),
  };
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
  const { user } = useAuth();
  const [datasources, setDatasources] = useState<any[]>([]);
  const [preview, setPreview] = useState<any>(null);
  const [testing, setTesting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [editStatus, setEditStatus] = useState<string | null>(null); // 编辑对象原状态,用于保存提示如实反映“已上线编辑即更新线上”
  const [enumSqlTesting, setEnumSqlTesting] = useState<string | null>(null); // 正在测试 enum_sql 的变量
  // 各变量 enum_sql 测试结果(带测试时用的 SQL);保存时随任务落库,成为业务侧共享候选
  const [enumSample, setEnumSample] = useState<Record<string, EnumSample>>({});
  const [enumEnabled, setEnumEnabled] = useState<Record<string, boolean>>({}); // 作者是否已勾选「允许枚举 SQL」(仅控制编辑框展开)
  const [showSql, setShowSql] = useState(false);
  const [previewSqlText, setPreviewSqlText] = useState<string | null>(null); // SQL 预览浮窗内容
  const [activeKeys, setActiveKeys] = useState<string[]>([]); // 展开的变量卡(默认全部收起)
  // 我所在各团队 × 各数据源的账号就绪态:任务用**所属团队**的账号取数,
  // 选完团队与数据源要当场告诉他配没配 —— 而不是等点上线才被卡点拦下。
  // 这份数据**不含库用户名**(半机密,后端只对团队管理员返回),故 UI 也不展示账号名。
  const [teamCredentials, setTeamCredentials] = useState<TeamCredentialStatus[]>([]);
  // 编辑已有任务时进来的原团队。转移团队是独立的治理动作(独立端点 + 独立审计码 +
  // 连带撤销原团队的编辑权),PUT /templates/{id} 刻意不接受 team_id —— 故保存时分开发。
  const [originalTeamId, setOriginalTeamId] = useState<number | null>(null);
  // 订阅:在册订阅人数(关闭订阅前的预警)与打开编辑器时的原开关状态
  const [subscriberCount, setSubscriberCount] = useState(0);
  const [origSubEnabled, setOrigSubEnabled] = useState(false);
  const [form] = Form.useForm();
  const sqlWatch = Form.useWatch("sql_text", form);
  const dsWatch = Form.useWatch("datasource_id", form);
  const teamWatch = Form.useWatch("team_id", form);
  const paramsWatch = Form.useWatch("params", form);
  const subEnabledWatch = Form.useWatch("sub_enabled", form);
  const subFreqWatch = Form.useWatch("sub_freq", form);
  // 「仅无变量任务可开订阅」的前端呈现;服务端卡点在 template_service.add_version
  const hasParams = (paramsWatch || []).length > 0;
  const teamCred = teamCredentials.find(
    (c) => c.team_id === teamWatch && c.datasource_id === dsWatch
  );
  // 平台管理员不受团队约束,可为任意团队建任务;开发者只能选自己所属的。
  // 团队清单直接来自 /auth/me(user.teams),不额外请求;平台管理员另外拉一次全量。
  const [allTeams, setAllTeams] = useState<{ id: number; name: string }[]>([]);
  const teamOptions = (isPlatformAdmin(user) ? allTeams : myTeams(user)).map((t) => ({
    value: t.id,
    label: t.name,
  }));
  // 名字从下拉选项里取(而不是只从 user.teams):平台管理员选的团队他自己可能并不属于
  const teamLabel = teamOptions.find((o) => o.value === teamWatch)?.label ?? teamCred?.team_name;
  // 选完团队 + 数据源就当场提示账号登记了没 —— 别等点上线才被卡点拦下。
  // 只有「压根没账号」才拦上线;「有账号但没点过测试连接」照样能上线,故那种情况不报警,
  // 顶多在提示里带一句「还没验过」。
  // 刻意不显示库用户名:它是半机密(Hive auth=NONE 下就是完整凭证),只在团队管理员的配置页可见。
  const credHint =
    !teamWatch || !teamCred ? undefined : !teamCred.configured ? (
      <span style={{ color: "#d46b08" }}>
        团队《{teamLabel}》还没登记该数据源的取数账号,任务将无法上线 ——{" "}
        {isTeamAdminOf(user, teamWatch) ? (
          <a
            href={withBase(`/teams/${teamWatch}?tab=credentials`)}
            target="_blank"
            rel="noreferrer"
          >
            去配置
          </a>
        ) : (
          "请联系该团队的团队管理员"
        )}
      </span>
    ) : (
      <span style={{ color: "#8c8c8c" }}>
        取数将使用团队《{teamLabel}》的账号
        {teamCred.verified ? "(已测通)" : "(该账号还没点过「测试连接」,不影响上线)"}
      </span>
    );

  useEffect(() => {
    if (!open) return;
    listDatasources().then(setDatasources);
    myTeamCredentials().then(setTeamCredentials);
    if (isPlatformAdmin(user)) listTeams().then(setAllTeams);
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
          team_id: d.team_id,
          datasource_id: d.datasource_id,
          timeout_seconds: d.timeout_seconds,
          sql_text: v?.sql_text,
          params,
          ...subFormFields(d.subscription),
        });
        setOriginalTeamId(d.team_id ?? null);
        setSubscriberCount(d.subscriber_count || 0);
        setOrigSubEnabled(!!d.subscription?.enabled);
        setActiveKeys([]); // 变量卡默认全部收起
      });
    } else {
      form.resetFields();
      const mine = myTeams(user);
      // 只属于一个团队时(最常见)直接选上,省一次点击
      form.setFieldsValue({
        params: [],
        team_id: mine.length === 1 ? mine[0].id : undefined,
        ...subFormFields(),
      });
      setActiveKeys([]);
      setEditStatus(null);
      setOriginalTeamId(null);
      setSubscriberCount(0);
      setOrigSubEnabled(false);
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

  // 作者测试该变量的「枚举值获取 SQL」是否能跑、返回多少候选
  const testEnumSql = async (fieldIndex: number, varName: string) => {
    const team_id = form.getFieldValue("team_id");
    const datasource_id = form.getFieldValue("datasource_id");
    const sql = form.getFieldValue(["params", fieldIndex, "enum_sql"]);
    if (!team_id) return message.warning("请先选择所属团队");
    if (!datasource_id) return message.warning("请先选择数据源");
    if (!sql) return message.warning("请先填写枚举值获取 SQL");
    setEnumSqlTesting(varName);
    try {
      const res = await runEnumSql({ team_id, datasource_id, sql });
      // 连测试时用的 SQL 一起记:保存时后端要校验它与最终落库的 enum_sql 一致才采纳
      setEnumSample((s) => ({ ...s, [varName]: { ...res, source_sql: sql } }));
      // 落库这次测试的获取耗时,业务填参侧作参考展示
      form.setFieldValue(["params", fieldIndex, "enum_sql_duration_ms"], res.duration_ms);
      const took = res.duration_ms != null ? `,耗时 ${res.duration_ms} ms` : "";
      message.success(
        `取到 ${res.values.length} 个候选值${res.truncated ? "(已截断)" : ""}${took},保存后即成为业务侧共享候选`
      );
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
    // 随任务落库的候选值:只带「已勾选枚举 且 测试时的 SQL 与当前 SQL 逐字相同」的样本。
    // 测完又改了 SQL 就别带了(后端也会再校验一次,这里只是不做无用的传输)。
    const enum_samples: Record<string, EnumSample> = {};
    for (const p of v.params || []) {
      const s = enumSample[p?.name];
      if (!s) continue;
      if (!(isListVar(sql, p.name) && isEnumOn(p))) continue;
      if (s.source_sql !== (p.enum_sql || "")) continue;
      enum_samples[p.name] = s;
    }
    // 订阅计划:表单上的 sub_* 字段收拢成一个 subscription 对象,不把散字段发给后端
    const { sub_enabled, sub_freq, sub_weekdays, sub_monthdays, sub_time, ...rest } = v;
    const subscription = {
      enabled: !!sub_enabled,
      freq: sub_freq || "daily",
      days:
        sub_freq === "weekly"
          ? sub_weekdays || []
          : sub_freq === "monthly"
            ? sub_monthdays || []
            : [],
      at_time: sub_time ? sub_time.format("HH:mm") : "09:00",
    };
    if (subscription.enabled && subscription.freq !== "daily" && !subscription.days.length) {
      message.warning(
        subscription.freq === "weekly" ? "请为订阅计划选择每周几运行" : "请为订阅计划选择每月几号运行"
      );
      throw new Error("subscription-days-required");
    }
    return {
      ...rest,
      subscription,
      enum_samples,
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
    if (!v.team_id) return message.warning("请先选择所属团队");
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
        team_id: v.team_id,
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
      if (editingId) {
        await updateTemplate(editingId, payload);
        // 平台管理员在编辑器里改了「所属团队」⇒ 走转移团队端点。
        // 不这么做的话那个下拉是个静默无效的控件(后端会丢掉 team_id)。
        if (originalTeamId != null && payload.team_id !== originalTeamId) {
          await transferTaskTeam(editingId, payload.team_id);
        }
      } else await createTemplate(payload);
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
                {/* 刻意不用 SqlHighlightArea:枚举 SQL 由后端以空绑定执行(template_service.run_value_query),
                    本就不接受 :变量,高亮反而会声称存在一个并不存在的变量 */}
                <Form.Item {...f} name={[f.name, "enum_sql"]} noStyle>
                  <Input.TextArea
                    rows={2}
                    style={{ fontFamily: "var(--rk-mono)" }}
                    placeholder="SELECT DISTINCT user_id FROM dim_user ORDER BY 1"
                  />
                </Form.Item>
                {sample && (
                  <div style={{ marginTop: 6, fontSize: 12, color: "#52c41a" }}>
                    ✓ 取到 {sample.values.length} 个候选值{sample.truncated ? "(已截断)" : ""}
                    {sample.duration_ms != null ? ` · 获取耗时 ${sample.duration_ms} ms` : ""}
                    ,可用于上方测试值选择;保存后将作为业务侧共享候选项
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
      footer={
        // 说明文字与按钮组各占一栏(flex),而不是把说明 float:left 塞进按钮行 ——
        // Modal 页脚是 text-align:end 的行内流,浮动元素会挤占行盒、把按钮顶到半空错位。
        // 两栏后:说明在左侧自己的列宽里折行,按钮永远整齐贴右下角。
        <div
          style={{
            display: "flex",
            alignItems: "flex-end",
            justifyContent: "space-between",
            gap: 16,
            textAlign: "left",
          }}
        >
          {/* 团队化带来的真实改善,值得在这儿讲出来:个人账号时代试跑用本人、正式取数用作者,
              「试跑通过」并不代表「上线后能跑」。现在两者是同一套团队账号。
              但这句承诺只在**取数身份**这一维上成立:试跑还有一道 180 秒的前台上限
              (template_service.TEST_RUN_TIMEOUT_CEILING_SECONDS),不写出来的话,一个配了 30 分钟
              的任务在试跑里被砍,作者会以为 SQL 不行而去改一条本来没问题的任务。 */}
          <Typography.Text type="secondary" style={{ flex: 1, minWidth: 0, fontSize: 12, lineHeight: "18px" }}>
            试跑与业务正式取数使用同一套团队账号 —— 取数身份上试跑通过即代表上线后能跑;
            但试跑最多只跑 180 秒,长查询以任务自己配的超时为准
          </Typography.Text>
          {/* flexShrink:0:说明再长也不许压缩按钮组换行 */}
          <Space style={{ flexShrink: 0 }}>
            <Button onClick={onClose}>取消</Button>
            <Button onClick={doPreviewSql}>SQL预览</Button>
            <Button loading={testing} onClick={doTestRun}>{testing ? "试跑中…" : "测试运行"}</Button>
            <Button type="primary" loading={saving} onClick={save}>
              {editingId ? "保存" : "创建"}
            </Button>
          </Space>
        </div>
      }
    >
      <Form form={form} layout="vertical">
        <Divider orientation="left" style={{ marginTop: 0 }}>基本信息</Divider>
        {/* align="start":Space 水平方向默认 align:center,带 extra 说明的字段更高,
            会把同排没有说明的字段压成垂直居中 ⇒ 标签与控件错行。顶对齐后各字段控件同线。
            各 Form.Item 再固定成控件宽度:说明文字在列宽内折行,而不是把字段撑宽、
            导致提示出现/消失时旁边字段横向抽动。 */}
        <Space style={{ width: "100%" }} size="large" wrap align="start">
          <Form.Item
            name="name"
            label="任务名称"
            rules={[{ required: true }]}
            style={{ width: 300 }}
          >
            <Input />
          </Form.Item>
          {/* 团队放在数据源**之前**:团队决定用哪套库账号,账号决定这个数据源跑不跑得动 */}
          <Form.Item
            name="team_id"
            label="所属团队"
            rules={[{ required: true, message: "请选择所属团队" }]}
            tooltip="团队决定这个任务谁看得见,以及取数用哪套数据库账号"
            extra={
              editingId && !isPlatformAdmin(user) ? (
                <span style={{ color: "#8c8c8c" }}>
                  任务所属团队不可自行更改,需要转移请联系平台管理员
                </span>
              ) : editingId ? (
                <span style={{ color: "#d46b08" }}>
                  转移团队会同时改变任务的可见范围与取数账号
                </span>
              ) : undefined
            }
            style={{ width: 240 }}
          >
            <Select
              // 编辑已有任务时只有平台管理员能改(转移团队是跨组织的治理动作)
              disabled={!!editingId && !isPlatformAdmin(user)}
              placeholder={teamOptions.length ? "选择团队" : "你还不属于任何团队"}
              options={teamOptions}
              notFoundContent="你还不属于任何团队,请联系平台管理员"
            />
          </Form.Item>
          <Form.Item
            name="datasource_id"
            label="数据源"
            rules={[{ required: true }]}
            extra={credHint}
            style={{ width: 240 }}
          >
            <Select
              options={datasources.map((d) => ({ value: d.id, label: `${d.name} (${d.engine})` }))}
            />
          </Form.Item>
          <Form.Item
            name="timeout_seconds"
            label="查询超时(秒)"
            tooltip="留空按引擎默认:Hive 3600s(长批处理),MySQL 120s。超时会自动终止查询。"
            style={{ width: 140 }}
          >
            <InputNumber style={{ width: "100%" }} min={1} placeholder="默认" />
          </Form.Item>
        </Space>
        <Form.Item name="description" label="任务说明">
          <Input.TextArea rows={2} placeholder="这个取数任务是做什么的,给协作者/业务参考" />
        </Form.Item>
        <Divider orientation="left">SQL 语句</Divider>
        <Form.Item
          name="sql_text"
          label="SQL"
          tooltip="用 :变量 做占位符;写 字段 IN (:x) / NOT IN (:x) 的变量会让业务多选一组值,其余变量业务填单个值。所有变量运行时必填。注意 '%H:%i:%s' 这类字面量里的冒号也会被识别成变量,可改用 %T 等写法避开。"
          rules={[{ required: true }]}
          extra="带底色的行为「参数影响行」,行内高亮的即 :变量 占位符"
        >
          <SqlHighlightArea rows={7} placeholder="SELECT ... WHERE dt = :dt AND uid IN (:uids)" />
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

        <Divider orientation="left">订阅与定时运行</Divider>
        <Space direction="vertical" size={10} style={{ width: "100%" }}>
          <Space align="center" size={10}>
            <Form.Item name="sub_enabled" valuePropName="checked" noStyle>
              {/* 有变量时禁止**开启**;已开启的仍可关闭(那正是解决冲突的出路) */}
              <Switch disabled={hasParams && !subEnabledWatch} />
            </Form.Item>
            <span>允许订阅 —— 平台按计划自动运行,并把结果推送给订阅者</span>
            {!!editingId && subscriberCount > 0 && (
              <Tag color="purple">{subscriberCount} 人订阅中</Tag>
            )}
          </Space>
          {hasParams && !subEnabledWatch && (
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              仅无变量任务可开启订阅:定时运行没有人在场填参,而所有变量运行时必填
            </Typography.Text>
          )}
          {/* 冲突预警:已开订阅又写了 :变量 —— 服务端保存时会拒绝,这里先说清出路 */}
          {hasParams && subEnabledWatch && (
            <Alert
              type="error"
              showIcon
              message="已开启订阅的任务不能有变量:请去掉 SQL 里的 :变量,或先关闭订阅再保存"
            />
          )}
          {subEnabledWatch && !hasParams && (
            <>
              <Space wrap align="center" size={12}>
                <Form.Item name="sub_freq" noStyle initialValue="daily">
                  <Segmented
                    options={[
                      { label: "每天", value: "daily" },
                      { label: "每周", value: "weekly" },
                      { label: "每月", value: "monthly" },
                    ]}
                  />
                </Form.Item>
                {subFreqWatch === "weekly" && (
                  <Form.Item name="sub_weekdays" noStyle>
                    <Checkbox.Group
                      options={["一", "二", "三", "四", "五", "六", "日"].map((label, i) => ({
                        label: `周${label}`,
                        value: i + 1, // ISO:周一=1 … 周日=7,与后端约定一致
                      }))}
                    />
                  </Form.Item>
                )}
                {subFreqWatch === "monthly" && (
                  <Form.Item name="sub_monthdays" noStyle>
                    <Select
                      mode="multiple"
                      style={{ minWidth: 240 }}
                      placeholder="选择几号(可多选)"
                      maxTagCount="responsive"
                      options={Array.from({ length: 31 }, (_, i) => ({
                        value: i + 1,
                        label: `${i + 1}日`,
                      }))}
                    />
                  </Form.Item>
                )}
                <Form.Item name="sub_time" noStyle>
                  <TimePicker format="HH:mm" minuteStep={5} allowClear={false} />
                </Form.Item>
              </Space>
              {subFreqWatch === "monthly" && (
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  选 29/30/31 号时,遇到没有这一天的月份(如 2 月)自动顺延到当月最后一天运行
                </Typography.Text>
              )}
            </>
          )}
          {/* 关闭前的预警:保存即清退全部订阅者并逐一通知,这不是能悄悄发生的事 */}
          {origSubEnabled && !subEnabledWatch && subscriberCount > 0 && (
            <Alert
              type="warning"
              showIcon
              message={`保存后将关闭订阅,并自动取消 ${subscriberCount} 名订阅者的订阅(平台会通知他们本人)`}
            />
          )}
        </Space>

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
        sourceSql={sqlWatch}
        title="SQL 预览(参数已代入,未填变量保留 :变量)"
        onClose={() => setPreviewSqlText(null)}
      />
      <SqlModal
        sql={showSql ? preview?.executed_sql || "" : null}
        sourceSql={sqlWatch}
        onClose={() => setShowSql(false)}
      />
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
