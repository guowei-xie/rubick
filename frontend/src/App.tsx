import { Suspense, lazy } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { Spin } from "antd";
import { User } from "./api";
import { MANAGER_ROLES, canSeeAnalytics, useAuth } from "./auth";
import AppLayout from "./components/AppLayout";
import LoginPage from "./pages/LoginPage";
import TasksPage from "./pages/TasksPage";
import MyTeamsPage from "./pages/MyTeamsPage";
import TeamPage from "./pages/TeamPage";
import AdminCredentialsPage from "./pages/admin/CredentialsPage";
import AdminTeamsPage from "./pages/admin/TeamsPage";
import DatasourcesPage from "./pages/admin/DatasourcesPage";
import AuditPage from "./pages/admin/AuditPage";
import UsersPage from "./pages/admin/UsersPage";

// 运营分析页单独走 lazy:它是全站唯一引图表库(echarts)的页面,而这页只给两类管理员看。
// 不拆出去的话,从不打开它的业务用户也要跟着下载那一份 —— 本仓库其余部分是单 bundle,
// 这是刻意开的第一处代码分割。
// 注意:动态 chunk 的路径跟随 vite 的 base(VITE_BASE_PATH),子路径部署时要实地验一次。
const AnalyticsPage = lazy(() => import("./pages/analytics/AnalyticsPage"));

function Protected({
  children,
  roles,
  allow,
}: {
  children: React.ReactNode;
  roles?: string[];
  /** 角色白名单表达不了的守卫。团队管理员的 role 就是 developer,只按 roles 放行会把
   *  **所有**开发者放进来 —— 这类判定交给谓词(与菜单共用同一个函数,不会漂移)。 */
  allow?: (user: User) => boolean;
}) {
  const { user, loading } = useAuth();
  if (loading) return <Spin style={{ margin: 80 }} />;
  if (!user) return <Navigate to="/login" replace />;
  if (roles && !roles.includes(user.role)) return <Navigate to="/tasks" replace />;
  if (allow && !allow(user)) return <Navigate to="/tasks" replace />;
  return <AppLayout>{children}</AppLayout>;
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/auth/callback" element={<LoginPage />} />
      <Route path="/tasks" element={<Protected><TasksPage /></Protected>} />
      {/* 团队:只有会建任务的角色才需要(普通用户不入团队,他们拿的是任务级授权)。
          页面内还会再按「是不是本团队成员/团队管理员」收窄可见与可写。 */}
      <Route
        path="/teams"
        element={
          <Protected roles={[...MANAGER_ROLES]}>
            <MyTeamsPage />
          </Protected>
        }
      />
      <Route
        path="/teams/:teamId"
        element={
          <Protected roles={[...MANAGER_ROLES]}>
            <TeamPage />
          </Protected>
        }
      />
      {/* 运营分析:不是管理员专属 —— 团队管理员也进得来,但只看得到自己的队。
          闸门在后端 analytics_service.resolve_scope,这里只挡住不该看见入口的人。
          **只用 allow、不叠 roles**:团队管理员的平台角色不受限(set_team_admin 没有角色门槛),
          再叠一层角色白名单就是第二条谁也没写全的规则 —— 而 canSeeAnalytics 与菜单共用。 */}
      <Route
        path="/analytics"
        element={
          <Protected allow={canSeeAnalytics}>
            <Suspense fallback={<Spin style={{ margin: 80 }} />}>
              <AnalyticsPage />
            </Suspense>
          </Protected>
        }
      />
      <Route
        path="/admin/teams"
        element={<Protected roles={["admin"]}><AdminTeamsPage /></Protected>}
      />
      <Route
        path="/admin/credentials"
        element={<Protected roles={["admin"]}><AdminCredentialsPage /></Protected>}
      />
      <Route
        path="/admin/users"
        element={<Protected roles={["admin"]}><UsersPage /></Protected>}
      />
      <Route
        path="/admin/datasources"
        element={<Protected roles={["admin"]}><DatasourcesPage /></Protected>}
      />
      <Route
        path="/admin/audit"
        element={<Protected roles={["admin"]}><AuditPage /></Protected>}
      />
      <Route path="*" element={<Navigate to="/tasks" replace />} />
    </Routes>
  );
}
