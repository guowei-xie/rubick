import { useState } from "react";
import { Button, Form, Input, message, Modal, Select, Space, Upload } from "antd";
import { CloudUploadOutlined, ThunderboltOutlined } from "@ant-design/icons";
import { errMsg, ParamDef, taskEnumValues } from "../api";

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

/** 值列表多选控件:自由输入 / 获取枚举值 / 上传粘贴。筛选方向(IN 包含 / NOT IN 排除)由模板 SQL 决定。 */
function ListField({
  pd,
  templateId,
  value,
  onChange,
}: {
  pd: ParamDef;
  templateId?: number;
  value?: string[];
  onChange?: (v: string[]) => void;
}) {
  const [options, setOptions] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);

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

  return (
    <div>
      <Select
        mode="tags"
        allowClear
        style={{ width: "100%" }}
        placeholder="可直接输入值,或获取候选后选择"
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
        {/* 上传/粘贴仅在编辑者为该变量开启时提供 */}
        {pd.allow_bulk_input && <PasteListButton onAdd={merge} />}
        {list.length ? <span style={{ color: "#888", fontSize: 12 }}>已选 {list.length} 个</span> : null}
      </Space>
    </div>
  );
}

/** 填写示例(取自作者配的测试值),作为 extra 提示;变量说明已作字段名展示。 */
function paramExtra(pd: ParamDef): string | undefined {
  const example = Array.isArray(pd.test_value) ? pd.test_value.join(", ") : pd.test_value;
  return example ? `示例:${example}` : undefined;
}

/** 按参数形态渲染一个 Form.Item(一律必填):list=值列表多选,其余=单值文本框。 */
export function ParamField({ pd, templateId }: { pd: ParamDef; templateId?: number }) {
  const label = pd.label || pd.name;

  if (pd.kind === "list") {
    return (
      <Form.Item
        name={pd.name}
        label={label}
        extra={paramExtra(pd)}
        rules={[{ required: true, message: `请为「${label}」选值或填写` }]}
      >
        <ListField pd={pd} templateId={templateId} />
      </Form.Item>
    );
  }

  return (
    <Form.Item
      name={pd.name}
      label={label}
      rules={[{ required: true, message: `请填写${label}` }]}
      extra={paramExtra(pd)}
    >
      <Input />
    </Form.Item>
  );
}
