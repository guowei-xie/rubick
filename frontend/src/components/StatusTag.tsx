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

// 运行来源(QueryJob.source):正式取数 / 编辑器试跑 / 订阅定时运行
export const JOB_SOURCE: TagMap = {
  run: { color: "blue", label: "正式" },
  test: { color: "orange", label: "试跑" },
  subscribe: { color: "purple", label: "定时" },
};

export const TEMPLATE_STATUS: TagMap = {
  draft: { color: "gold", label: "草稿", dot: "#faad14", tint: "#fdf3e2" },
  published: { color: "green", label: "已上线", dot: "#52c41a", tint: "#e8f6ec" },
  archived: { color: "red", label: "已下线", dot: "#ff4d4f", tint: "#fdecec" },
};

export const ROLE: TagMap = {
  user: { color: "default", label: "普通用户" },
  developer: { color: "geekblue", label: "开发者" },
  admin: { color: "red", label: "管理员" },
};

// 团队内的角色。团队页、团队管理页、我的团队页共用,免得几处各写一份「紫色 = 团队管理员」
export const TEAM_ROLE: TagMap = {
  member: { color: "default", label: "成员" },
  team_admin: { color: "purple", label: "团队管理员" },
};

// 任务编辑权的来源。author / team_admin 是身份的推论(隐式,不可撤销),granted 才是授权行。
// 三个值的标签、配色、说明都在这里定义一次 —— 面板的表格列与弹窗都读它。
export const EDITOR_SOURCE: TagMap = {
  author: { color: "default", label: "任务作者" },
  team_admin: { color: "default", label: "团队管理员" },
  granted: { color: "green", label: "已授予" },
};

// 编辑权来源的悬停说明(与 EDITOR_SOURCE 同键)
export const EDITOR_SOURCE_HINT: Record<string, string> = {
  author: "任务作者(天然可编辑,不可撤销)",
  team_admin: "团队管理员(天然可编辑本团队全部任务,不可撤销)",
  granted: "被授予了该任务的编辑权",
};

// 团队取数账号的三态。dot/tint 供任务卡片的告警胶囊复用(同 TEMPLATE_STATUS 的用法)。
// 注意只有 unconfigured 才意味着「跑不动」:测试连接是可选自检,「已配置 · 未验过」照样能跑,
// 故它用中性的蓝而不是橙 —— 橙色会被读成「有问题待处理」。
export const CREDENTIAL_STATUS: TagMap = {
  unconfigured: { color: "default", label: "未配置", dot: "#fa8c16", tint: "#fff2e8" },
  unverified: { color: "blue", label: "已配置 · 未验过", dot: "#1677ff", tint: "#e6f4ff" },
  verified: { color: "green", label: "已测通", dot: "#52c41a", tint: "#e8f6ec" },
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
