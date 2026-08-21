import axios from "axios";

/** 部署基路径(构建期 vite base 注入),末尾不带斜杠:根部署为 ""、子路径部署为 "/rubick"。 */
export const BASE = import.meta.env.BASE_URL.replace(/\/$/, "");
/** 把后端返回的根相对路径(如 /api/jobs/1/file)补成含基路径的可直接跳转地址。 */
export const withBase = (path: string) => `${BASE}${path}`;

export const http = axios.create({ baseURL: withBase("/api") });

/** 统一提取后端错误信息(后端错误体形如 {detail: "..."}),带兜底文案。 */
export const errMsg = (e: any, fallback = "操作失败"): string =>
  e?.response?.data?.detail || fallback;

http.interceptors.request.use((config) => {
  const token = localStorage.getItem("rubic_token");
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

http.interceptors.response.use(
  (r) => r,
  (err) => {
    if (err.response?.status === 401) {
      localStorage.removeItem("rubic_token");
      if (!location.pathname.startsWith(withBase("/login"))) location.href = withBase("/login");
    }
    return Promise.reject(err);
  }
);

// 参数只分两种形态,一律必填:single=单值文本;list=值列表(多选,执行前展开成 IN)。
// kind 由 SQL 写法判定(字段 IN (:x) / NOT IN (:x) → list,其余 → single)。
export interface ParamDef {
  name: string;
  kind?: "single" | "list";
  value_type?: "text" | "number"; // 值形态:text=文本(加引号),number=数值(绑定为 int/float,不加引号)
  label?: string; // 变量说明:给业务看的名字兼填参提示(合并了原 中文名+说明)
  test_value?: string | string[]; // 测试值:作者试跑用,兼作业务填参示例
  enum_sql?: string; // 仅 list:取候选值的独立 SELECT(业务点「获取枚举值」时跑)
  allow_bulk_input?: boolean; // 仅 list:是否允许业务「上传/粘贴」批量输入
  enum_sql_duration_ms?: number; // 仅 list:作者测试 enum_sql 时捕获的获取耗时(ms),供业务参考
}

// 枚举值获取结果:候选值 + 是否截断 + 本次获取耗时(ms)
export interface ValueListOut {
  values: string[];
  truncated: boolean;
  duration_ms?: number;
}

// 作者在编辑器测出来的一批候选值,随任务保存落库、成为业务侧共享候选。
// source_sql = 测这批值时用的那段 SQL:后端只在它与最终落库的 enum_sql 一致时才采纳。
export interface EnumSample extends ValueListOut {
  source_sql: string;
}

// 业务侧读到的共享候选值(ValueListOut 的严格超集)
export interface SharedEnumValues extends ValueListOut {
  cached: boolean; // 是否有可用的共享候选
  stale: boolean; // 作者改了 enum_sql / 数据源,旧候选已作废
  reused?: boolean; // 刚有人更新过,本次直接复用,没真跑 SQL
  updated_at?: string;
  updated_by?: number;
  updated_by_name?: string;
}

export interface User {
  id: number;
  name: string;
  role: "user" | "admin" | "developer";
  email?: string;
  /** 我所属的团队(随 /auth/me 一起下发,前端所有团队门禁零额外请求)。
   *  刻意不含「账号就绪没」——那只有编辑器需要,走 myTeamCredentials()。 */
  teams?: { id: number; name: string; is_team_admin: boolean }[];
}

// ---- auth ----
export const getAuthConfig = () => http.get("/auth/config").then((r) => r.data);
export const mockLogin = (feishu_open_id: string) =>
  http.post("/auth/mock-login", { feishu_open_id }).then((r) => r.data);
export const feishuCallback = (code: string) =>
  http.post("/auth/feishu/callback", { code }).then((r) => r.data);
export const getMe = () => http.get("/auth/me").then((r) => r.data as User);

// ---- templates ----
export const getTemplate = (id: number) => http.get(`/templates/${id}`).then((r) => r.data);
export const createTemplate = (data: any) => http.post("/templates", data).then((r) => r.data);
export const updateTemplate = (id: number, data: any) =>
  http.put(`/templates/${id}`, data).then((r) => r.data);
export const publishTemplate = (id: number, note?: string) =>
  http.post(`/templates/${id}/publish`, { note }).then((r) => r.data);
export const archiveTemplate = (id: number) =>
  http.post(`/templates/${id}/archive`).then((r) => r.data);
export const testRun = (data: any) => http.post("/templates/test-run", data).then((r) => r.data);
// SQL 预览:代入当前测试值渲染即将执行的 SQL(不执行),未填变量原样保留 :变量
export const previewSql = (data: { sql_text: string; params: any[]; values: any }) =>
  http.post("/templates/preview-sql", data).then((r) => r.data as { rendered_sql: string });
// 作者测试「枚举值获取 SQL」。team_id 必填:用哪个团队的账号跑,后端还会校验成员资格
export const runEnumSql = (data: { team_id: number; datasource_id: number; sql: string }) =>
  http.post("/templates/enum-sql", data).then((r) => r.data as ValueListOut);
// 业务填参:读某变量的共享候选值(纯读缓存,不跑 SQL,打开抽屉即可用)
export const taskEnumValues = (templateId: number, variable: string) =>
  http
    .get(`/tasks/${templateId}/enum-values`, { params: { variable } })
    .then((r) => r.data as SharedEnumValues);
// 业务填参:手动更新共享候选值(真跑一次 enum_sql,结果对该任务所有人生效)
export const refreshTaskEnumValues = (templateId: number, variable: string) =>
  http
    .post(`/tasks/${templateId}/enum-values/refresh`, { variable })
    .then((r) => r.data as SharedEnumValues);

// ---- tasks(统一任务列表)----
export const listTasks = () => http.get("/tasks").then((r) => r.data);
export const taskRunRecords = (id: number) =>
  http.get(`/tasks/${id}/jobs`).then((r) => r.data);
export const previewJob = (id: number) => http.get(`/jobs/${id}/preview`).then((r) => r.data);

// ---- query ----
export const runQuery = (template_id: number, values: any) =>
  http.post("/run", { template_id, values }).then((r) => r.data);
export const getJob = (jobId: number) => http.get(`/jobs/${jobId}`).then((r) => r.data);
export const downloadJob = (jobId: number) =>
  http.get(`/jobs/${jobId}/download`).then((r) => r.data);

// ---- notifications ----
export const listNotifications = (unreadOnly = false) =>
  http.get("/notifications", { params: { unread_only: unreadOnly } }).then((r) => r.data);
export const unreadCount = () =>
  http.get("/notifications/unread-count").then((r) => r.data.count as number);
export const readAllNotifications = () =>
  http.post("/notifications/read-all").then((r) => r.data);
export const readNotification = (id: number) =>
  http.post(`/notifications/${id}/read`).then((r) => r.data);

// ---- datasources ----
export const listDatasources = () => http.get("/datasources").then((r) => r.data);
export const createDatasource = (data: any) => http.post("/datasources", data).then((r) => r.data);
export const updateDatasource = (id: number, data: any) =>
  http.put(`/datasources/${id}`, data).then((r) => r.data);
export const deleteDatasource = (id: number) =>
  http.delete(`/datasources/${id}`).then((r) => r.data);
export const testDatasource = (id: number) =>
  http
    .post(`/datasources/${id}/test`)
    .then((r) => r.data as { ok: boolean; databases?: string[] });

// ---- teams(团队与成员)----
// 团队是任务的归属边界与取数身份边界:任务必属一个团队,同团队互相可见,
// 编辑权默认仅限作者(团队管理员可按任务授予)。
export interface TeamMember {
  user_id: number;
  name: string;
  avatar?: string | null;
  email?: string | null;
  role: string;
  is_team_admin: boolean;
  joined_at?: string | null;
}
export interface Team {
  id: number;
  name: string;
  description?: string | null;
  member_count: number;
  /** 任务数,**含回收站里已下线的** —— 删团队的卡点看的就是它 */
  template_count: number;
  admins: TeamMember[];
  created_at?: string | null;
}
export interface TeamDetail extends Team {
  members: TeamMember[];
}

export const listTeams = () => http.get("/teams").then((r) => r.data as Team[]);
export const getTeamDetail = (id: number) =>
  http.get(`/teams/${id}`).then((r) => r.data as TeamDetail);
export const createTeam = (data: { name: string; description?: string; admin_user_ids?: number[] }) =>
  http.post("/teams", data).then((r) => r.data as Team);
export const updateTeam = (id: number, data: { name?: string; description?: string }) =>
  http.put(`/teams/${id}`, data).then((r) => r.data as Team);
export const deleteTeam = (id: number) => http.delete(`/teams/${id}`).then((r) => r.data);
/** 可加入本团队的候选人:登录过的**开发者**且尚不在本团队(不走飞书通讯录)。 */
export const teamCandidates = (id: number, q?: string) =>
  http.get(`/teams/${id}/candidates`, { params: { q } }).then(
    (r) => r.data as { user_id: number; name: string; avatar?: string; email?: string }[]
  );
export const addTeamMember = (id: number, data: { user_id: number; is_team_admin?: boolean }) =>
  http.post(`/teams/${id}/members`, data).then((r) => r.data as TeamMember);
export const removeTeamMember = (id: number, userId: number) =>
  http.delete(`/teams/${id}/members/${userId}`).then((r) => r.data);
export const grantTeamAdmin = (id: number, userId: number) =>
  http.post(`/teams/${id}/members/${userId}/admin`).then((r) => r.data as TeamMember);
export const revokeTeamAdmin = (id: number, userId: number) =>
  http.delete(`/teams/${id}/members/${userId}/admin`).then((r) => r.data as TeamMember);

// ---- 任务编辑权(团队内)----
// 与业务授权(view/run/download)分开:那个面向业务方、从飞书通讯录选人;
// 这个面向团队成员、只能从本团队成员里选,由团队管理员授予。
export interface TaskEditor {
  user_id: number;
  name: string;
  avatar?: string | null;
  /** author / team_admin 是身份的推论(隐式,不可撤销);granted 才是一条授权行 */
  source: "author" | "team_admin" | "granted";
}
export const listTaskEditors = (templateId: number) =>
  http.get(`/tasks/${templateId}/editors`).then((r) => r.data as TaskEditor[]);
/** 本团队每个任务的编辑人,按任务 id 分组。**一次请求查完** ——
 *  逐任务调 listTaskEditors 会变成 N 个往返(团队页的「任务编辑权」面板一进就要整张表)。 */
export const teamTaskEditors = (teamId: number) =>
  http
    .get(`/teams/${teamId}/task-editors`)
    .then((r) => r.data as Record<string, TaskEditor[]>);
export const grantTaskEditor = (templateId: number, userId: number) =>
  http.post(`/tasks/${templateId}/editors`, { user_id: userId }).then((r) => r.data);
export const revokeTaskEditor = (templateId: number, userId: number) =>
  http.delete(`/tasks/${templateId}/editors/${userId}`).then((r) => r.data);
/** 转移任务所属团队(仅平台管理员):同时改变可见范围与取数身份。 */
export const transferTaskTeam = (templateId: number, teamId: number) =>
  http.put(`/tasks/${templateId}/team`, { team_id: teamId }).then((r) => r.data);

// ---- credentials(团队取数账号)----
// 任务用**所属团队**的库账号取数,数据权限交由数据库裁决。
// 密码只写不读:接口永不回传。库用户名是半机密(Hive auth=NONE 下它本身就是完整凭证),
// 只有该团队的团队管理员与平台管理员看得到 —— 其它人拿到的 username 恒为 null。
export interface TeamCredentialStatus {
  team_id: number;
  team_name?: string | null;
  datasource_id: number;
  datasource_name: string;
  engine: string;
  host?: string | null;
  port?: number | null;
  database?: string | null;
  configured: boolean; // 是否已登记账号
  username?: string | null; // 仅团队管理员 / 平台管理员可见
  verified: boolean; // 最近一次连接测试是否通过。纯提示 —— 不影响任务能否上线/运行
  last_verified_at?: string | null;
  last_verify_error?: string | null;
  updated_by?: number | null;
  updated_by_name?: string | null;
  updated_at?: string | null;
}
/** 「测试连接」成功后的提示:连通只是底线,真正有用的是「这个账号能取哪些库的数」。
 *  库多时只列前几个 —— 提示条塞不下几百个库名,给出个数与样例就够定位问题了。 */
export const connectOkMsg = (databases?: string[]): string => {
  const dbs = databases ?? [];
  if (!dbs.length) return "连接成功,账号可登录(未能列出可访问的库)";
  const head = dbs.slice(0, 6).join("、");
  return `连接成功。该账号可访问 ${dbs.length} 个库:${head}${dbs.length > 6 ? " 等" : ""}`;
};

/** 「测试连接」的响应:状态行 + 这个账号能访问的库(与数据源配的默认库无关)。 */
export interface TeamCredentialVerify extends TeamCredentialStatus {
  databases?: string[];
}
export interface NotReadyTemplate {
  template_id: number;
  template_name: string;
  team_id?: number | null;
  team_name?: string | null;
  author_id: number;
  author_name?: string | null;
  datasource_id: number;
  datasource_name?: string | null;
  reason: string; // 未配置 / 无所属团队
}
export interface CredentialOverview {
  datasources: { id: number; name: string; engine: string }[];
  teams: {
    team_id: number;
    team_name: string;
    member_count: number;
    admins: TeamMember[];
    credentials: TeamCredentialStatus[];
  }[];
  /** 非空 ⇒ 这些已上线任务**此刻就跑不动**(不再是「切开关前要清零的清单」) */
  not_ready_templates: NotReadyTemplate[];
}

/** 我所在各团队 × 各数据源的账号状态(配没配 / 验过没)。给任务编辑器用,**永不含库用户名**。 */
export const myTeamCredentials = () =>
  http.get("/credentials/my-teams").then((r) => r.data as TeamCredentialStatus[]);
export const listTeamCredentials = (teamId: number) =>
  http.get(`/credentials/teams/${teamId}`).then((r) => r.data as TeamCredentialStatus[]);
/** 登记/修改团队在某数据源上的账号;password 留空表示保留原密码。
 *  改动会清空「已测通」这条自检痕迹,但**不影响任务能不能跑** —— 测试连接是可选的。 */
export const saveTeamCredential = (
  teamId: number,
  dsId: number,
  data: { username: string; password?: string }
) => http.put(`/credentials/teams/${teamId}/${dsId}`, data).then((r) => r.data as TeamCredentialStatus);
export const testTeamCredential = (teamId: number, dsId: number) =>
  http.post(`/credentials/teams/${teamId}/${dsId}/test`).then((r) => r.data as TeamCredentialVerify);
export const deleteTeamCredential = (teamId: number, dsId: number) =>
  http.delete(`/credentials/teams/${teamId}/${dsId}`).then((r) => r.data);
export const credentialOverview = () =>
  http.get("/credentials/overview").then((r) => r.data as CredentialOverview);

// ---- permissions ----
export const listPermissions = (resource_id?: string) =>
  http.get("/permissions", { params: { resource_type: "template", resource_id } }).then((r) => r.data);
export const grantPermission = (data: any) => http.post("/permissions", data).then((r) => r.data);
export const revokePermission = (id: number) =>
  http.delete(`/permissions/${id}`).then((r) => r.data);
export const lookupUsers = (q?: string) =>
  http.get("/lookup/users", { params: { q } }).then((r) => r.data);

// ---- audit ----
export interface AuditLogRow {
  id: number;
  user_id: number | null;
  user_name: string | null;
  action: string;
  resource_type: string | null;
  resource_id: string | null;
  resource_name: string | null;
  detail: any;
  ip: string | null;
  created_at: string;
}
/** 服务端分页信封 */
export interface AuditLogPage {
  total: number;
  items: AuditLogRow[];
}
/** 动作码/资源类型的中文标签,由后端 models.audit 单一维护,前端不再重列一份 */
export interface AuditMeta {
  actions: { code: string; label: string; group: string }[];
  resource_types: { code: string; label: string }[];
}

export const getAuditMeta = () => http.get("/audit/meta").then((r) => r.data as AuditMeta);
export const listAuditLogs = (params: any = {}) =>
  http.get("/audit/logs", { params }).then((r) => r.data as AuditLogPage);
export const exportAuditLogs = (params: any = {}) =>
  http.get("/audit/logs/export", { params, responseType: "blob" }).then((r) => r.data as Blob);

// ---- admin: users & 飞书同步 ----
export const listUsers = (q?: string) =>
  http.get("/admin/users", { params: { q } }).then((r) => r.data);
export const setUserRole = (userId: number, role: string) =>
  http.post(`/admin/users/${userId}/role`, { role }).then((r) => r.data);
