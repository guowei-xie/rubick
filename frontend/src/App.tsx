import { Navigate, Route, Routes } from "react-router-dom";
import { Spin } from "antd";
import { useAuth } from "./auth";
import AppLayout from "./components/AppLayout";
import LoginPage from "./pages/LoginPage";
import TasksPage from "./pages/TasksPage";
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
