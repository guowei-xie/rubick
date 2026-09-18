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

/** 运行来源的**全称**。表格里的 Tag 要短(列宽有限),而运营分析的图例、卡片标题要能
 *  离开「来源」那一列独立读懂 ——「试跑」两个字单独摆在图例上没人知道在说什么。
 *  与 JOB_SOURCE 同键、紧挨着放:新增一种来源时两处一起补,不会漏。 */
export const JOB_SOURCE_LONG: Record<string, string> = {
  run: "正式取数",
  test: "作者试跑",
  subscribe: "定时运行",
};

export const TEMPLATE_STATUS: TagMap = {
  draft: { color: "gold", label: "草稿", dot: "#faad14", tint: "#fdf3e2" },
  published: { color: "green", label: "已上线", dot: "#52c41a", tint: "#e8f6ec" },
  archived: { color: "red", label: "已下线", dot: "#ff4d4f", tint: "#fdecec" },
};

/** 任务状态的**排列次序**:先看能跑的,再看没上线的,最后是回收站里的。
 *  与标签/配色同住一处 —— 按字母排会得到 archived/draft/published,读起来毫无道理;
 *  以后新增状态,顺序也只在这里补一次(列表排序、筛选片都读它)。 */
export const TEMPLATE_STATUS_ORDER = ["published", "draft", "archived"] as const;

/** 未知状态排在最后。 */
export function templateStatusRank(status: string): number {
  const i = TEMPLATE_STATUS_ORDER.indexOf(status as (typeof TEMPLATE_STATUS_ORDER)[number]);
  return i < 0 ? TEMPLATE_STATUS_ORDER.length : i;
}

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

/** 「闲置」(长期没人运行的已上线任务)的配色。它不是模型上的枚举,所以**不是 TagMeta**
 *  —— 从不经 <StatusTag> 渲染,给它 color / label 只会多两个永远没人读的成员。
 *  但卡片、列表行、标题栏筛选片三处要用同一套色,与其散在三个文件里,不如和别的状态色住一起。
 *  **刻意用灰而不是橙**:闲置不是故障 —— 它没坏、也没人做错什么,只是没人用。
 *  用 CREDENTIAL_STATUS.unconfigured 那种橙会被读成「现在就跑不动、要立刻修」,
 *  而这两件事的下一步恰好相反(一个是找团队管理员配账号,一个是考虑把它下掉)。 */
export const TASK_IDLE = { dot: "#8a90a6", tint: "#eef0f7" } as const;

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
