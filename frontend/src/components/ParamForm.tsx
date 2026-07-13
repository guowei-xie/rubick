import { DatePicker, Form, Input, InputNumber, Select } from "antd";
import dayjs from "dayjs";
import { ParamDef } from "../api";

const { RangePicker } = DatePicker;

/** 按参数定义渲染一个 Form.Item。value 由外层 Form 收集。 */
export function ParamField({ pd }: { pd: ParamDef }) {
  const label = pd.label || pd.name;
  const rules = pd.required ? [{ required: true, message: `请填写${label}` }] : [];

  let control: React.ReactNode;
  switch (pd.type) {
    case "number":
      control = <InputNumber style={{ width: "100%" }} />;
      break;
    case "date":
      control = <DatePicker style={{ width: "100%" }} format="YYYY-MM-DD" />;
      break;
    case "daterange":
      control = <RangePicker style={{ width: "100%" }} format="YYYY-MM-DD" />;
      break;
    case "enum":
      control = <Select options={(pd.options || []).map((o) => ({ value: o, label: o }))} />;
      break;
    case "multi_enum":
      control = (
        <Select mode="multiple" options={(pd.options || []).map((o) => ({ value: o, label: o }))} />
      );
      break;
    default:
      control = <Input />;
  }
  return (
    <Form.Item name={pd.name} label={label} rules={rules}>
      {control}
    </Form.Item>
  );
}

/** 把 Ant Design 表单值转成后端期望的 JSON(日期转字符串)。 */
export function serializeValues(defs: ParamDef[], values: any): Record<string, any> {
  const out: Record<string, any> = {};
  for (const pd of defs) {
    const v = values[pd.name];
    if (v == null) continue;
    if (pd.type === "date") out[pd.name] = dayjs(v).format("YYYY-MM-DD");
    else if (pd.type === "daterange")
      out[pd.name] = [dayjs(v[0]).format("YYYY-MM-DD"), dayjs(v[1]).format("YYYY-MM-DD")];
    else out[pd.name] = v;
  }
  return out;
}

/** 把默认值填入 Form 的 initialValues。 */
export function initialValues(defs: ParamDef[]): any {
  const out: any = {};
  for (const pd of defs) {
    if (pd.default == null) continue;
    if (pd.type === "date") out[pd.name] = dayjs(pd.default);
    else out[pd.name] = pd.default;
  }
  return out;
}
