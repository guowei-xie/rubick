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
/** 免鉴权的登录页配置:展示哪些登录方式,以及登不进来时去哪儿申请权限。
 *  三项都可能是 null —— 没配飞书、没开 mock、没配申请链接,对应的入口就整个不渲染。 */
export interface AuthConfig {
  mock_auth: boolean;
  feishu_authorize_url: string | null;
  feishu_apply_url: string | null;
  /** 平台对外规范地址(后端 APP_BASE_URL,可能含子路径)。生成给外部用的绝对链接必须用它,
   *  而不是 window.location —— 用户可能正通过内网 IP 或反代访问。 */
  app_base_url: string;
}
export const getAuthConfig = () => http.get("/auth/config").then((r) => r.data as AuthConfig);
export const mockLogin = (feishu_open_id: string) =>
  http.post("/auth/mock-login", { feishu_open_id }).then((r) => r.data);
export const feishuCallback = (code: string) =>
  http.post("/auth/feishu/callback", { code }).then((r) => r.data);
export const getMe = () => http.get("/auth/me").then((r) => r.data as User);

// ---- API Token(开放 API 的个人凭证)----
// 明文只在生成/重置那一刻返回一次,此后服务端只肯说「有没有、何时签发、最近何时用过」,
// 前端拿不回也**不该**拿回 token 本体 —— 故「查看 token」这个入口不存在。
export interface ApiTokenInfo {
  exists: boolean;
  issued_at: string | null;
  last_used_at: string | null;
}
export const getApiToken = () => http.get("/auth/api-token").then((r) => r.data as ApiTokenInfo);
/** 生成或重置(旧 token 立即失效)。返回的 token 是唯一一次明文,展示完就丢。 */
export const createApiToken = () =>
  http.post("/auth/api-token").then((r) => r.data as { token: string; issued_at: string });
export const revokeApiToken = () => http.delete("/auth/api-token").then((r) => r.data);

// ---- templates ----
export const getTemplate = (id: number) => http.get(`/templates/${id}`).then((r) => r.data);
export const createTemplate = (data: any) => http.post("/templates", data).then((r) => r.data);
export const updateTemplate = (id: number, data: any) =>
  http.put(`/templates/${id}`, data).then((r) => r.data);
export const publishTemplate = (id: number, note?: string) =>
  http.post(`/templates/${id}/publish`, { note }).then((r) => r.data);
export const archiveTemplate = (id: number) =>
  http.post(`/templates/${id}/archive`).then((r) => r.data);
// 回收站 → 草稿。与「重新上线」(publishTemplate)是回收站的两个出口,故意分两个接口:
// 这条不让任务对业务可运行,也不过取数账号卡点
export const unarchiveTemplate = (id: number) =>
  http.post(`/templates/${id}/unarchive`).then((r) => r.data);
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
/** 任务列表的一行(后端 schemas/query.TaskOut)。
 *
 *  **有类型的理由**:这些行此前在 TaskCard / TaskTable / taskActions 里一路是 `any`,
 *  于是「读一个后端根本不下发的字段」编译期查不出来 —— allow_api 加在了 TemplateOut 上
 *  而列表走的是 TaskOut,卡片与表格上新加的「API」标记因此从上线起就没渲染过一次,
 *  肉眼看不出与「这批任务恰好都没开 API」的区别。
 *
 *  按需声明、不照抄后端全字段(同本文件 Job 的写法):列的是界面真读的那些,
 *  后端加了新字段而界面用不上时,这里不必跟着长。 */
/** 被授权可运行某任务的人(后端 schemas/query.AuthorizedUserOut),卡片上的参与者头像。 */
export interface AuthorizedUser {
  id: number;
  name?: string | null;
  avatar?: string | null;
}

export interface Task {
  id: number;
  name: string;
  /** 草稿 / 已上线 / 已下线(archived = 回收站,见 models/template.STATUS_*) */
  status: "draft" | "published" | "archived";
  description: string | null;
  team_id: number | null;
  team_name: string | null;
  author_name: string | null;
  datasource_name: string | null;
  created_at: string | null;
  updated_at: string | null;
  last_run_at: string | null;
  schedule_desc: string | null;
  /** 距今多少天没运行过;**非已上线任务恒为 null**(草稿/已下线不参与闲置判定) */
  idle_days: number | null;
  // 上面那组是**值**可能为 null(后端列可空),下面这组连 null 都不会有:它们在
  // TaskOut 上都带默认值,恒随每一行下发。两组都不写成可选(`?:`)—— pydantic 恒把键
  // 序列化出来,「键不存在」这一半永远不成立,写了只会逼每个使用点补一次没用的兜底,
  // 而那些兜底掩盖的恰恰是「后端真没给」这种问题
  /** 允许被开放 API 触发(运行闸)。卡片与表格据此显示「API」标记 */
  allow_api: boolean;
  can_manage: boolean;
  can_run: boolean;
  can_view_detail: boolean;
  can_subscribe: boolean;
  can_transfer_author: boolean;
  developed_by_me: boolean;
  credential_ready: boolean;
  is_idle: boolean;
  idle_threshold_days: number;
  subscribe_enabled: boolean;
  subscribed: boolean;
  subscriber_count: number;
  authorized_users: AuthorizedUser[];
}
export const listTasks = () => http.get("/tasks").then((r) => r.data as Task[]);
export const taskRunRecords = (id: number) =>
  http.get(`/tasks/${id}/jobs`).then((r) => r.data);
/** 补推:把一条已确认的运行结果推给该任务的全部订阅者,作为本期订阅结果。
 *  资格由服务端判(subscription_service.push_block),与列表行上的 can_push/push_hint 同源 */
export const pushJobToSubscribers = (taskId: number, jobId: number) =>
  http
    .post(`/tasks/${taskId}/jobs/${jobId}/push`)
    .then((r) => r.data as { job: any; replaced: boolean; notified: number });

// ---- 任务订阅(定时自动运行)----
// 订阅计划(daily/weekly/monthly + days + "HH:MM")随任务保存提交(create/updateTemplate
// 的 payload.subscription),不单独开端点;这里只有订阅关系的自助操作与管理侧查询。
export interface SubscriberRow {
  user_id: number;
  name?: string;
  avatar?: string;
  miss_streak: number; // 连续未消费的成功期数
  created_at?: string; // 订阅时间
  added_by?: number | null; // 代订阅的操作者;空 = 本人自助订阅
  added_by_name?: string | null;
}
/** 代订阅的目标。与授权同一形状:已落库的人传 subject_id,通讯录搜出来的传 open_id + 展示资料
 *  (邮箱不回传,服务端自己从通讯录取)。 */
export interface SubscribeForSubject {
  subject_id?: string;
  subject_open_id?: string;
  subject_name?: string;
  subject_avatar?: string;
}
export interface SubscriptionEvent {
  id: number;
  user_id: number;
  user_name?: string;
  action: string;
  action_label: string; // 后端译好的中文动作(订阅/退订/自动退订…)
  operator_id?: number;
  operator_name?: string;
  detail?: any;
  created_at?: string;
}
export const subscribeTask = (id: number) =>
  http.put(`/tasks/${id}/subscription`).then((r) => r.data);
export const unsubscribeTask = (id: number) =>
  http.delete(`/tasks/${id}/subscription`).then((r) => r.data);
export const taskSubscribers = (id: number) =>
  http
    .get(`/tasks/${id}/subscribers`)
    .then((r) => r.data as { threshold: number; items: SubscriberRow[] });
export const taskSubscriptionEvents = (id: number) =>
  http.get(`/tasks/${id}/subscription-events`).then((r) => r.data as SubscriptionEvent[]);
/** 代订阅(全成功才生效)。created/skipped/granted 都是 user_id 清单;
 *  名单本身由调用方重拉 taskSubscribers 刷新,免得行形状在两处各维护一份。 */
export const subscribeTaskFor = (id: number, subjects: SubscribeForSubject[]) =>
  http
    .post(`/tasks/${id}/subscribers`, { subjects })
    .then(
      (r) =>
        r.data as { created: number[]; skipped: number[]; granted: number[] }
    );
export const removeTaskSubscriber = (id: number, userId: number) =>
  http.delete(`/tasks/${id}/subscribers/${userId}`).then((r) => r.data);
export const previewJob = (id: number) => http.get(`/jobs/${id}/preview`).then((r) => r.data);

// ---- query ----
/** 一次运行的状态。queue_ahead 仅在 status=queued 时有值:排在前面还有几个。 */
export interface Job {
  id: number;
  status: "queued" | "running" | "success" | "failed";
  queue_ahead?: number | null;
  row_count?: number | null;
  error?: string | null;
  executed_sql?: string | null;
}
export const runQuery = (template_id: number, values: any) =>
  http.post("/run", { template_id, values }).then((r) => r.data as Job);
export const getJob = (jobId: number) => http.get(`/jobs/${jobId}`).then((r) => r.data as Job);
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

// ---- 任务作者转移(离职交接)----
/** 可以接手这个任务的人:该任务所属团队的**在职成员**,排除当前作者。
 *  候选集的三条规则由服务端算好 —— 在前端复述一遍就是两份会漂移的规则,
 *  而漂移的表现是「下拉里选得到、点了报错」。 */
export const taskAuthorCandidates = (templateId: number) =>
  http.get(`/tasks/${templateId}/author-candidates`).then((r) => r.data as TeamMember[]);
/** 转移任务作者。发起人 = 作者本人 / 该团队的团队管理员 / 平台管理员
 *  (**不含**被授予该任务编辑权的人,见 TaskOut.can_transfer_author)。 */
export const transferTaskAuthor = (templateId: number, userId: number) =>
  http.put(`/tasks/${templateId}/author`, { user_id: userId }).then((r) => r.data);

// ---- 批量转移作者(多选离职交接)----
// 与上面单任务那两个是同一件事的两个方向:那边「一个任务 → 一批人」,这边「一批任务 →
// 每个人各自能接哪些」。两者最终落在服务端同一句规则上,所以下拉里选得到的与点下去
// 放行的永远一致 —— 前端**不要**用 team_id / author_id 自己再推一遍。

/** 某个候选人接不了的一组任务,以及服务端写好的整句理由(前端原样显示,不自己编)。 */
export interface BlockedGroup {
  code: string;
  message: string;
  template_ids: number[];
}
export interface AuthorTransferCandidate {
  user_id: number;
  name: string;
  avatar?: string | null;
  email?: string | null;
  /** 选了他之后可勾选的任务 */
  eligible_template_ids: number[];
  /** 我有处分权、但不能给他的那些,按理由分组 */
  blocked: BlockedGroup[];
}
export interface AuthorTransferCandidates {
  /** 与接手人**无关**的置灰理由(我根本没有处分权、或任务无主),服务端只算一次 */
  blocked: BlockedGroup[];
  candidates: AuthorTransferCandidate[];
}
/** 批量交接的第一步:我能把任务交给谁,以及选了他之后哪些能勾、哪些要置灰。 */
export const authorTransferCandidates = () =>
  http.get("/tasks/author-transfer/candidates").then((r) => r.data as AuthorTransferCandidates);

export interface BatchAuthorTransferResult {
  /** 审计页上「我刚才那一批」的检索号 */
  batch_id: string;
  to_user_id: number;
  to_user_name: string;
  count: number;
}
/** 批量转移作者。**全成功才生效**:任一条不合法则整批 400,一个任务都不会被改动,
 *  detail 是一段多行说明(每条拒绝自成一行),按 errMsg 取出后需以 pre-line 渲染。 */
export const batchTransferTaskAuthor = (userId: number, templateIds: number[]) =>
  http
    .post("/tasks/author-transfer", { user_id: userId, template_ids: templateIds })
    .then((r) => r.data as BatchAuthorTransferResult);

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

// ---- analytics(运营分析)----
//
// 给平台管理员与团队管理员看平台被用得怎么样。各板块自己一个接口:切时间范围时治理块
// 不必重算、某块慢不拖累整页、团队管理员被隐藏的块直接不请求。
//
// 两条贯穿全模块的约定,读这些类型前先知道:
//   · 每个标量都是 Metric,**不是裸数字**。`value: null` 是「算不出来」(没有样本),
//     与 0 完全是两回事 —— 都渲染成 0,新部署的人会以为失败率完美;
//   · `windowed` 区分「本区间」与「此刻」。任务总数、缺账号数这类不吃时间范围,
//     切了范围纹丝不动,卡片上必须挂「此刻」标记,否则第一次切范围就会被当成 bug。

/** 一个指标。三种状态:有值 / 值为 0 但历史上有过 / 从来没有过。 */
export interface Metric {
  value: number | null;
  /** 历史上有没有过数据。与 value 是否为 0 无关 —— 前者决定说「0 次失败」还是「还没有数据」 */
  has_data: boolean;
  /** true=吃时间范围;false=「此刻」的快照 */
  windowed: boolean;
  last_event_at?: string | null;
  prev_value?: number | null;
}

export interface AnalyticsScopeInfo {
  level: "platform" | "team";
  team_id: number | null;
  team_name: string | null;
}

export interface AnalyticsWindow {
  start: string;
  end: string;
  days: number;
}

/** 板块响应的统一外壳 —— 范围与时间窗永远跟着数字一起下发,分两处取迟早会配错。 */
export interface AnalyticsEnvelope {
  scope: AnalyticsScopeInfo;
  window: AnalyticsWindow;
}

export interface ScopeOption {
  team_id: number | null; // null = 全平台,只会出现在平台管理员的列表里
  name: string;
}

export interface MetricNote {
  key: string;
  label: string;
  windowed: boolean;
  note: string;
}

export interface AnalyticsMeta {
  scope_options: ScopeOption[];
  /** 进来默认看哪个范围(null = 全平台)。**与 scope_options 同源** —— 前端别再从
   *  /auth/me 的团队顺序里自己挑一个,两边挑出不同的队时界面与数据会对不上 */
  default_team_id: number | null;
  /** 时间范围快捷档(天)。与后端的 PRESET_DAYS 同源,前端不再硬编码一份 */
  presets: number[];
  /** 每条指标的中文口径。**服务端下发** —— 前端不自己维护一份,否则口径改了文案不改 */
  metric_notes: MetricNote[];
  /** 平台还没被用起来 ⇒ 整页换成一句话 + 下一步指引,而不是四屏「—」 */
  bootstrapped: boolean;
  counts: { teams: number; templates: number; runs: number };
}

export interface DailyPoint {
  date: string;
  run: number;
  test: number;
  subscribe: number;
}

export interface AdoptionData extends AnalyticsEnvelope {
  run_jobs: Metric;
  test_jobs: Metric;
  scheduled_jobs: Metric;
  active_users: Metric;
  active_authors: Metric;
  download_events: Metric;
  download_per_success: Metric;
  self_service_ratio: Metric;
  reuse_multiple: Metric;
  automation_ratio: Metric;
  daily_series: DailyPoint[];
  /** 仅平台视角。团队视角下这两个键**不存在**(不是 null)—— 前端据此整块不渲染 */
  new_users?: Metric;
  retention_rate?: Metric;
  /** 仅团队视角:窗口内首次跑过本团队任务的人 */
  new_task_users?: Metric;
}

export interface SourceRate {
  total: number;
  success: number;
  failed: number;
  /** 分母只含终态;0 次取数时是 null 而不是 0 —— 「没得算」不等于「0%」 */
  success_rate: number | null;
}

export interface HealthData extends AnalyticsEnvelope {
  by_source: Record<"run" | "test" | "subscribe", SourceRate>;
  /** 顶部那几张卡。**服务端已经包成信封** —— has_data 是全期口径,前端拿 by_source /
   *  queue 里的裸数字自己拼,会把「从来没跑过」渲染成一个绿色的 0 */
  run_success_rate: Metric;
  run_failed: Metric;
  queue_p50_ms: Metric;
  queue_over_60s: Metric;
  queued_now: Metric;
  daily_series: { date: string; success: number; failed: number }[];
  duration_by_engine: Record<
    string,
    {
      samples: number;
      p50_ms: number | null;
      p90_ms: number | null;
      p95_ms: number | null;
      buckets: { label: string; count: number }[];
    }
  >;
  queue: {
    samples: number;
    /** 有排队记录的样本占比。偏低时页面要说明「排队数据自 X 起可用」 */
    coverage: number | null;
    stats_since: string | null;
    p50_ms: number | null;
    p90_ms: number | null;
    p95_ms: number | null;
    over_60s: number;
  };
  zero_row_jobs: Metric;
  failure_buckets: { code: string; label: string; count: number }[];
  /** other 桶的去重样例 —— 让失败归因规则表能继续演进 */
  unbucketed_samples: string[];
  failure_truncated: boolean;
  /** 「此刻」的快照,不吃时间范围 */
  in_flight: { queued: number; running: number; oldest_queued_at: string | null };
  by_datasource: {
    datasource_id: number;
    name: string;
    engine: string;
    total: number;
    success: number;
    failed: number;
    fail_rate: number | null;
  }[];
}

/** 任务排行/明细里「这是哪张任务」的三列。三个板块的排行表共用同一份 ——
 *  任务被硬删后名字取不到、编号仍在,所以后两列可空。 */
export interface TaskRankRow {
  template_id: number;
  name: string | null;
  team_name: string | null;
}

export interface TopTemplate extends TaskRankRow {
  author_name: string | null;
  run_count: number;
  /** 几个不同的人在跑。只有作者自己跑 = 没被业务用起来,比「跑了多少次」更能说明问题 */
  distinct_users: number;
  success_rate: number | null;
}

export interface AssetsData extends AnalyticsEnvelope {
  as_of: {
    total: Metric;
    published: Metric;
    draft: Metric;
    archived: Metric;
    idle: Metric;
    /** 0 = 闲置提示功能关闭 ⇒ 前端必须把「闲置」卡与它的下钻一起隐藏,否则点过去是空列表 */
    idle_threshold_days: number;
    never_run: Metric;
    schedules_enabled: Metric;
    subscribers: Metric;
    at_risk_subscriptions: Metric;
  };
  window_changes: {
    new_templates: Metric;
    new_versions: Metric;
    publishes?: Metric; // 仅平台视角(上线次数只能从审计里数)
  };
  top_templates: TopTemplate[];
  top10_share: Metric;
  tail_count: Metric;
  idle_list: {
    template_id: number;
    name: string;
    team_name: string | null;
    author_name: string | null;
    idle_days: number;
    last_run_at: string | null;
  }[];
}

export interface GovernanceData extends AnalyticsEnvelope {
  as_of: {
    grants_total: Metric;
    dormant_grants: Metric;
    dormant_ratio: Metric;
    stale_edit_grants: Metric;
    credentials: {
      configured: number;
      required: number;
      /** 此刻就跑不动的已上线任务。口径由 credential_service.not_ready_templates 独家持有 */
      not_ready: Metric;
      /** 已配置 ÷ 需要配置。分母为 0 时是 null(「没得算」),不是 0% */
      coverage: Metric;
    };
  };
  dormant_detail: {
    user_id: number;
    user_name: string | null;
    template_id: number;
    template_name: string | null;
    team_name: string | null;
    granted_by_name: string | null;
    granted_at: string | null;
  }[];
  wide_access_tasks: (TaskRankRow & { granted_users: number })[];
  downloads: {
    total: Metric;
    top_users: { user_id: number; user_name: string | null; downloads: number; max_rows: number | null }[];
    concentration: Metric;
  };
  /** 仅平台视角。团队视角下这个键**不存在** */
  platform?: {
    role_distribution: Record<string, number>;
    teams_total: number;
    teams_without_admin: Metric;
    audit_actions: { action: string; label: string; count: number }[];
  };
}

export interface ApiUsageData extends AnalyticsEnvelope {
  /** 此刻口径。团队视角按**团队成员**收窄,与运行/下载的任务归属口径不同 */
  tokens_issued: Metric;
  /** 固定回看 7 天,**不吃上方的时间范围**(windowed=false) */
  tokens_active_7d: Metric;
  api_runs: Metric;
  /** 分母为 0 时是 null(「没得算」),不是 0 */
  api_run_share: Metric;
  api_downloads: Metric;
  /** 排行,最多 10 条。**不是 Metric**,没有三态 —— 空列表就是空列表 */
  top_tasks: (TaskRankRow & { run_count: number })[];
}

/** 各板块共用的查询参数。team_id 省略时:平台管理员看全平台,团队管理员看自己的团队。 */
export interface AnalyticsQuery {
  team_id?: number | null;
  start?: string;
  end?: string;
  days?: number;
}

// signal 一路传到 axios:快速连点「近7天/近30天/近90天」会并发出三个请求,光靠丢弃过期响应
// 只是不让界面错乱,那几个请求仍在库上跑。带上 signal 才是真的取消掉。
const analyticsParams = (q: AnalyticsQuery, signal?: AbortSignal) => ({
  params: {
    team_id: q.team_id ?? undefined,
    start: q.start || undefined,
    end: q.end || undefined,
    days: q.days || undefined,
  },
  signal,
});

export const analyticsMeta = (signal?: AbortSignal) =>
  http.get("/analytics/meta", { signal }).then((r) => r.data as AnalyticsMeta);
export const analyticsAdoption = (q: AnalyticsQuery = {}, signal?: AbortSignal) =>
  http.get("/analytics/adoption", analyticsParams(q, signal)).then((r) => r.data as AdoptionData);
export const analyticsHealth = (q: AnalyticsQuery = {}, signal?: AbortSignal) =>
  http.get("/analytics/health", analyticsParams(q, signal)).then((r) => r.data as HealthData);
export const analyticsAssets = (q: AnalyticsQuery = {}, signal?: AbortSignal) =>
  http.get("/analytics/assets", analyticsParams(q, signal)).then((r) => r.data as AssetsData);
export const analyticsGovernance = (q: AnalyticsQuery = {}, signal?: AbortSignal) =>
  http.get("/analytics/governance", analyticsParams(q, signal)).then((r) => r.data as GovernanceData);
export const analyticsApi = (q: AnalyticsQuery = {}, signal?: AbortSignal) =>
  http.get("/analytics/api", analyticsParams(q, signal)).then((r) => r.data as ApiUsageData);
