import { Navigate, Route, Routes } from "react-router-dom";
import { Spin } from "antd";
import { MANAGER_ROLES, useAuth } from "./auth";
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

function Protected({ children, roles }: { children: React.ReactNode; roles?: string[] }) {
  const { user, loading } = useAuth();
  if (loading) return <Spin style={{ margin: 80 }} />;
  if (!user) return <Navigate to="/login" replace />;
  if (roles && !roles.includes(user.role)) return <Navigate to="/tasks" replace />;
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
