import { Navigate, Route, Routes } from "react-router-dom";
import { Spin } from "antd";
import { useAuth } from "./auth";
import AppLayout from "./components/AppLayout";
import LoginPage from "./pages/LoginPage";
import TemplatesPage from "./pages/TemplatesPage";
import JobsPage from "./pages/JobsPage";
import StudioPage from "./pages/StudioPage";
import DatasourcesPage from "./pages/admin/DatasourcesPage";
import PermissionsPage from "./pages/admin/PermissionsPage";
import AuditPage from "./pages/admin/AuditPage";
import UsersPage from "./pages/admin/UsersPage";

function Protected({ children, roles }: { children: React.ReactNode; roles?: string[] }) {
  const { user, loading } = useAuth();
  if (loading) return <Spin style={{ margin: 80 }} />;
  if (!user) return <Navigate to="/login" replace />;
  if (roles && !roles.includes(user.role)) return <Navigate to="/templates" replace />;
  return <AppLayout>{children}</AppLayout>;
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/auth/callback" element={<LoginPage />} />
      <Route path="/templates" element={<Protected><TemplatesPage /></Protected>} />
      <Route path="/jobs" element={<Protected><JobsPage /></Protected>} />
      <Route
        path="/studio"
        element={<Protected roles={["analyst", "admin"]}><StudioPage /></Protected>}
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
        path="/admin/permissions"
        element={<Protected roles={["admin"]}><PermissionsPage /></Protected>}
      />
      <Route
        path="/admin/audit"
        element={<Protected roles={["admin"]}><AuditPage /></Protected>}
      />
      <Route path="*" element={<Navigate to="/templates" replace />} />
    </Routes>
  );
}
