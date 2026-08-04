import { useEffect, useMemo, useState } from "react";
import { Button, Form, Input, message, Modal, Select, Space, Upload } from "antd";
import { CloudUploadOutlined, ThunderboltOutlined } from "@ant-design/icons";
import {
  errMsg,
  ParamDef,
  refreshTaskEnumValues,
  SharedEnumValues,
  taskEnumValues,
} from "../api";
import { fmtTime } from "../format";

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

// 辅助说明文字的两种色调:灰=信息,橙=需要注意
const HINT = { color: "#888", fontSize: 12 } as const;
const WARN = { color: "#d46b08", fontSize: 12 } as const;

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

/** 值列表多选控件:自由输入 / 共享候选值 / 上传粘贴。筛选方向(IN 包含 / NOT IN 排除)由任务 SQL 决定。 */
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
  const [meta, setMeta] = useState<SharedEnumValues | null>(null);
  const [loading, setLoading] = useState(false);

  const list: string[] = Array.isArray(value) ? value : [];
  const cached = !!meta?.cached;
  // 候选值只有一个来源(共享缓存),不另存一份 options,免得两处状态漂移
  const candidates = meta?.values ?? [];
  const options = useMemo(() => candidates.map((o) => ({ value: o, label: o })), [candidates]);

  const merge = (vals: string[]) => {
    const merged = Array.from(new Set([...list, ...vals]));
    if (merged.length > LIST_CAP) message.warning(`已超过 ${LIST_CAP} 个,过多可能导致查询过慢或失败`);
    onChange?.(merged);
  };

  // 打开即带出该任务的共享候选值:纯读缓存、不跑 SQL,所以可以无条件预取。
  // 刻意静默失败 —— 一个抽屉里有 N 个值列表变量,报错会叠 N 个 toast。
  useEffect(() => {
    if (!templateId || !pd.enum_sql) return;
    // alive 不是多余的:换任务会重跑本 effect,慢的旧响应不能盖掉新任务的候选
    let alive = true;
    taskEnumValues(templateId, pd.name)
      .then((res) => alive && setMeta(res))
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [templateId, pd.name, pd.enum_sql]);

  // 手动更新共享候选值:真跑一次 enum_sql,结果对该任务所有人生效
  const doRefresh = async () => {
    if (!templateId) return;
    setLoading(true);
    try {
      const res = await refreshTaskEnumValues(templateId, pd.name);
      setMeta(res);
      if (!res.values.length) message.info("没有取到候选值");
      else if (res.reused) message.success(`刚刚已有人更新过,已复用最新的 ${res.values.length} 个候选值`);
      else
        message.success(
          `取到 ${res.values.length} 个候选值${res.truncated ? "(已截断)" : ""},已同步给该任务的其他使用者`
        );
    } catch (e: any) {
      message.error(errMsg(e, "更新枚举值失败"));
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
        placeholder="可直接输入值,或从候选中选择"
        value={list}
        onChange={onChange}
        options={options}
      />
      <Space style={{ marginTop: 6 }} wrap>
        {/* 整块只在作者配了枚举 SQL 时存在 */}
        {pd.enum_sql && (
          <>
            <Button size="small" icon={<ThunderboltOutlined />} loading={loading} onClick={doRefresh}>
              {cached ? "更新枚举值" : "获取枚举值"}
            </Button>
            {/* 三态互斥:有共享候选 / 已作废 / 还没取过 */}
            {cached ? (
              <span style={HINT}>
                候选 {candidates.length} 个
                {meta?.updated_by_name ? ` · ${meta.updated_by_name}` : ""}
                {meta?.updated_at ? ` 更新于 ${fmtTime(meta.updated_at, false)}` : ""}
                {meta?.duration_ms != null ? ` · 上次耗时 ${meta.duration_ms} ms` : ""}
              </span>
            ) : meta?.stale ? (
              <span style={WARN}>作者已更新配置,请点「获取枚举值」</span>
            ) : pd.enum_sql_duration_ms != null ? (
              // 还没有共享候选时,用作者测试的耗时做等待预期管理
              <span style={HINT}>作者测试约 {pd.enum_sql_duration_ms} ms,供参考</span>
            ) : null}
            {/* 截断是常态化展示的:必须说清「候选不全,可以手输」 */}
            {meta?.truncated && <span style={WARN}>已截断,可直接输入未列出的值</span>}
          </>
        )}
        {/* 上传/粘贴仅在编辑者为该变量开启时提供 */}
        {pd.allow_bulk_input && <PasteListButton onAdd={merge} />}
        {list.length ? <span style={HINT}>已选 {list.length} 个</span> : null}
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
