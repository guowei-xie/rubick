import { useState } from "react";
import { Button, DatePicker, Form, Input, InputNumber, message, Modal, Select, Space, Tag, Tooltip, Upload } from "antd";
import { CloudUploadOutlined, ThunderboltOutlined } from "@ant-design/icons";
import dayjs from "dayjs";
import { errMsg, ParamDef, taskEnumValues } from "../api";

const { RangePicker } = DatePicker;

// 「全选」哨兵:该字段不筛选(后端把谓词中和为 1=1),不跑 SQL、不生成 IN
export const ALL_VALUES = "__RUBIC_ALL__";

// 兼容旧类型名(把历史类型别名归一到当前取值方式);多处复用
export const norm = (t?: string): string =>
  (({ string: "text", daterange: "date_range" } as any)[t || "text"] || t || "text");

// 把粘贴/上传的文本解析成去重的值列表(按换行/逗号/分号/空白分隔)
function parseIdList(text: string): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const raw of text.split(/[\n,;\t ]+/)) {
    const v = raw.trim();
    if (v && !seen.has(v)) {
      seen.add(v);
      out.push(v);
    }
  }
  return out;
}

const LIST_CAP = 5000;

/** 「上传/粘贴列表」按钮 + 弹窗:解析成值列表后回调 onAdd。业务填参与代码编辑测试值共用。 */
export function PasteListButton({ onAdd }: { onAdd: (vals: string[]) => void }) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const apply = () => {
    const vals = parseIdList(text);
    if (!vals.length) return message.warning("没解析到有效内容");
    onAdd(vals);
    message.success(`已加入 ${vals.length} 个值`);
    setText("");
    setOpen(false);
  };
  return (
    <>
      <Button size="small" icon={<CloudUploadOutlined />} onClick={() => setOpen(true)}>
        上传/粘贴列表
      </Button>
      <Modal title="上传或粘贴列表" open={open} onCancel={() => setOpen(false)} onOk={apply} okText="加入">
        <Upload
          accept=".csv,.txt"
          maxCount={1}
          showUploadList={false}
          beforeUpload={(file) => {
            const reader = new FileReader();
            reader.onload = () => setText((prev) => (prev ? prev + "\n" : "") + String(reader.result || ""));
            reader.readAsText(file);
            return false; // 阻止自动上传,仅本地读取
          }}
        >
          <Button icon={<CloudUploadOutlined />}>选择 .csv / .txt 文件</Button>
        </Upload>
        <Input.TextArea
          style={{ marginTop: 8 }}
          rows={8}
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="一行一个 id,或用逗号/空格分隔。也可先选文件再补充。"
        />
      </Modal>
    </>
  );
}

/** 枚举/列表多选控件:自由输入 / 获取枚举值 / 上传粘贴 / 全选(=不筛该字段)。 */
function MultiEnumField({
  pd,
  templateId,
  value,
  onChange,
}: {
  pd: ParamDef;
  templateId?: number;
  value?: string[] | string; // 数组=具体值;ALL_VALUES=全选(不筛)
  onChange?: (v: string[] | string) => void;
}) {
  const [options, setOptions] = useState<string[]>(pd.options || []);
  const [loading, setLoading] = useState(false);

  const isAll = value === ALL_VALUES;
  const list: string[] = Array.isArray(value) ? value : [];

  const merge = (vals: string[]) => {
    const merged = Array.from(new Set([...list, ...vals]));
    if (merged.length > LIST_CAP) message.warning(`已超过 ${LIST_CAP} 个,过多可能导致查询过慢或失败`);
    onChange?.(merged);
  };

  // 获取候选值供勾选(跑分析师配的 enum_sql)
  const fetchEnum = async () => {
    if (!templateId) return;
    setLoading(true);
    try {
      const res = await taskEnumValues(templateId, pd.name);
      setOptions(res.values);
      if (!res.values.length) message.info("没有取到候选值");
      else message.success(`取到 ${res.values.length} 个候选值${res.truncated ? "(已截断)" : ""},请勾选`);
    } catch (e: any) {
      message.error(errMsg(e, "获取枚举值失败"));
    } finally {
      setLoading(false);
    }
  };

  // 全选态:不展示输入框,只提示「全部(不筛选)」
  if (isAll) {
    return (
      <Space>
        <Tag color="blue">全部(不筛选此字段)</Tag>
        <Button size="small" onClick={() => onChange?.([])}>改为选择具体值</Button>
      </Space>
    );
  }

  return (
    <div>
      <Select
        mode="tags"
        allowClear
        style={{ width: "100%" }}
        placeholder="可直接输入值,或获取候选/上传列表后选择"
        value={list}
        onChange={onChange}
        options={options.map((o) => ({ value: o, label: o }))}
      />
      <Space style={{ marginTop: 6 }} wrap>
        {pd.enum_sql && (
          <Button size="small" icon={<ThunderboltOutlined />} loading={loading} onClick={fetchEnum}>
            获取枚举值
          </Button>
        )}
        <PasteListButton onAdd={merge} />
        <Tooltip title="不对该字段做筛选(相当于全部);不跑 SQL、不生成大 IN">
          <Button size="small" onClick={() => onChange?.(ALL_VALUES)}>全选</Button>
        </Tooltip>
        {list.length ? <span style={{ color: "#888", fontSize: 12 }}>已选 {list.length} 个</span> : null}
      </Space>
    </div>
  );
}

/** 数字范围控件:值为 [min, max],交给外层 Form 收集。 */
function NumberRange({ value, onChange }: { value?: any[]; onChange?: (v: any[]) => void }) {
  const [min, max] = value || [];
  return (
    <Space>
      <InputNumber placeholder="最小" value={min} onChange={(v) => onChange?.([v, max])} />
      <span>~</span>
      <InputNumber placeholder="最大" value={max} onChange={(v) => onChange?.([min, v])} />
    </Space>
  );
}

/** 按参数取值方式渲染一个 Form.Item。 */
export function ParamField({ pd, templateId }: { pd: ParamDef; templateId?: number }) {
  const baseLabel = pd.label || pd.name;
  const kind = norm(pd.type);
  // required===false(或旧的 all_when_empty)= 选填:留空即不筛选
  const optional = pd.required === false || pd.all_when_empty;
  const rules = optional ? [] : [{ required: true, message: `请填写${baseLabel}` }];
  const label = `${baseLabel}${optional ? "(选填)" : ""}`;

  // 枚举/列表筛选:输入/获取枚举/全选/上传,统一按正选 IN 筛选
  if (kind === "multi_enum") {
    return (
      <Form.Item
        name={pd.name}
        label={label}
        required={!optional}
        extra={pd.description || undefined}
        rules={optional ? [] : [{ required: true, message: `请为「${baseLabel}」选值/填写,或点全选` }]}
      >
        <MultiEnumField pd={pd} templateId={templateId} />
      </Form.Item>
    );
  }

  let control: React.ReactNode;
  switch (kind) {
    case "number":
      control = <InputNumber style={{ width: "100%" }} />;
      break;
    case "date":
      control = <DatePicker style={{ width: "100%" }} format="YYYY-MM-DD" />;
      break;
    case "date_range":
      control = <RangePicker style={{ width: "100%" }} format="YYYY-MM-DD" />;
      break;
    case "number_range":
      control = <NumberRange />;
      break;
    case "enum":
      control = (
        <Select
          allowClear
          showSearch
          options={(pd.options || []).map((o) => ({ value: o, label: o }))}
        />
      );
      break;
    default:
      control = <Input />;
  }
  return (
    <Form.Item name={pd.name} label={label} rules={rules} extra={pd.description || undefined}>
      {control}
    </Form.Item>
  );
}

/** 把 Ant Design 表单值转成后端期望的 JSON(日期转字符串、范围转 [a,b])。 */
export function serializeValues(defs: ParamDef[], values: any): Record<string, any> {
  const out: Record<string, any> = {};
  for (const pd of defs) {
    const v = values[pd.name];
    if (v == null) continue;
    const kind = norm(pd.type);
    if (kind === "date") out[pd.name] = dayjs(v).format("YYYY-MM-DD");
    else if (kind === "date_range")
      out[pd.name] = [dayjs(v[0]).format("YYYY-MM-DD"), dayjs(v[1]).format("YYYY-MM-DD")];
    else if (kind === "number_range") out[pd.name] = [v[0], v[1]];
    else out[pd.name] = v;
  }
  return out;
}

/** 把默认值填入 Form 的 initialValues(含各多选变量的正/反选默认)。 */
export function initialValues(defs: ParamDef[]): any {
  const out: any = {};
  for (const pd of defs) {
    if (pd.default == null) continue;
    const kind = norm(pd.type);
    if (kind === "date") out[pd.name] = dayjs(pd.default);
    else if (kind === "multi_enum") out[pd.name] = Array.isArray(pd.default) ? pd.default : [pd.default];
    else out[pd.name] = pd.default;
  }
  return out;
}
