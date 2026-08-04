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
// 作者测试「枚举值获取 SQL」
export const runEnumSql = (data: { datasource_id: number; sql: string }) =>
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
  http.post(`/datasources/${id}/test`).then((r) => r.data);

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
