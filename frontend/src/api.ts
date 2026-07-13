import axios from "axios";

export const http = axios.create({ baseURL: "/api" });

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
      if (!location.pathname.startsWith("/login")) location.href = "/login";
    }
    return Promise.reject(err);
  }
);

export interface ParamDef {
  name: string;
  type: "string" | "number" | "date" | "daterange" | "enum" | "multi_enum";
  label?: string;
  required?: boolean;
  default?: any;
  options?: string[];
}

export interface User {
  id: number;
  name: string;
  role: "user" | "analyst" | "admin";
  email?: string;
  department_id?: number;
}

// ---- auth ----
export const getAuthConfig = () => http.get("/auth/config").then((r) => r.data);
export const mockLogin = (feishu_open_id: string) =>
  http.post("/auth/mock-login", { feishu_open_id }).then((r) => r.data);
export const feishuCallback = (code: string) =>
  http.post("/auth/feishu/callback", { code }).then((r) => r.data);
export const getMe = () => http.get("/auth/me").then((r) => r.data as User);

// ---- templates ----
export const listTemplates = (mine = false) =>
  http.get("/templates", { params: { mine } }).then((r) => r.data);
export const getTemplate = (id: number) => http.get(`/templates/${id}`).then((r) => r.data);
export const createTemplate = (data: any) => http.post("/templates", data).then((r) => r.data);
export const updateTemplate = (id: number, data: any) =>
  http.put(`/templates/${id}`, data).then((r) => r.data);
export const publishTemplate = (id: number, note?: string) =>
  http.post(`/templates/${id}/publish`, { note }).then((r) => r.data);
export const archiveTemplate = (id: number) =>
  http.post(`/templates/${id}/archive`).then((r) => r.data);
export const testRun = (data: any) => http.post("/templates/test-run", data).then((r) => r.data);

// ---- query ----
export const runQuery = (template_id: number, values: any) =>
  http.post("/run", { template_id, values }).then((r) => r.data);
export const listJobs = () => http.get("/jobs").then((r) => r.data);
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
export const lookupDepartments = (q?: string) =>
  http.get("/lookup/departments", { params: { q } }).then((r) => r.data);

// ---- audit ----
export const listAuditLogs = (params: any = {}) =>
  http.get("/audit/logs", { params }).then((r) => r.data);

// ---- admin: users & 飞书同步 ----
export const listUsers = (q?: string) =>
  http.get("/admin/users", { params: { q } }).then((r) => r.data);
export const setUserRole = (userId: number, role: string) =>
  http.post(`/admin/users/${userId}/role`, { role }).then((r) => r.data);
export const syncContacts = () => http.post("/admin/sync-contacts").then((r) => r.data);
