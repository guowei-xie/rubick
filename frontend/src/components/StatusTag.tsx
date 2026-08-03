import { Tag } from "antd";

// color=AntD 预设名(Tag 用);dot=状态圆点/实心色(卡片用);tint=浅色底(筛选片高亮用)
export type TagMeta = { color: string; label: string; dot?: string; tint?: string };
export type TagMap = Record<string, TagMeta>;

// 各类枚举 → 颜色/中文标签,集中一处,避免每个页面各写一份
export const JOB_STATUS: TagMap = {
  queued: { color: "default", label: "排队" },
  running: { color: "blue", label: "运行中" },
  success: { color: "green", label: "成功" },
  failed: { color: "red", label: "失败" },
};

export const TEMPLATE_STATUS: TagMap = {
  draft: { color: "gold", label: "待上线", dot: "#faad14", tint: "#fdf3e2" },
  published: { color: "green", label: "已上线", dot: "#52c41a", tint: "#e8f6ec" },
  archived: { color: "red", label: "已下线", dot: "#ff4d4f", tint: "#fdecec" },
};

export const ROLE: TagMap = {
  user: { color: "default", label: "普通用户" },
  developer: { color: "geekblue", label: "开发者" },
  admin: { color: "red", label: "管理员" },
};

export const NOTE_LEVEL: TagMap = {
  info: { color: "blue", label: "info" },
  success: { color: "green", label: "success" },
  error: { color: "red", label: "error" },
};

/** 按映射表把枚举值渲染成带色标签;未知值原样显示。 */
export default function StatusTag({ map, value }: { map: TagMap; value: string }) {
  const meta = map[value];
  return <Tag color={meta?.color}>{meta?.label ?? value}</Tag>;
}
