import { Layout, Dropdown, Avatar, Input, Tag, Tooltip } from "antd";
import {
  UserOutlined,
  AppstoreOutlined,
  TeamOutlined,
  DatabaseOutlined,
  AuditOutlined,
  DeleteOutlined,
  SearchOutlined,
  LogoutOutlined,
} from "@ant-design/icons";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { useAuth } from "../auth";
import NotificationBell from "./NotificationBell";
import { ROLE } from "./StatusTag";

const { Sider, Header, Content } = Layout;

type NavItem = { key: string; label: string; icon: React.ReactNode };

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const { user, logout } = useAuth();
  const nav = useNavigate();
  const loc = useLocation();
  const [sp, setSp] = useSearchParams();

  const items: NavItem[] = [
    { key: "/tasks", label: "任务列表", icon: <AppstoreOutlined /> },
  ];
  // 管理者(管理员/开发者)可见回收站,收纳已下线任务
  if (user?.role === "admin" || user?.role === "developer")
    items.push({ key: "/recycle", label: "回收站", icon: <DeleteOutlined /> });
  if (user?.role === "admin")
    items.push(
      { key: "/admin/users", label: "用户管理", icon: <TeamOutlined /> },
      { key: "/admin/datasources", label: "数据源", icon: <DatabaseOutlined /> },
      { key: "/admin/audit", label: "审计", icon: <AuditOutlined /> }
    );

  const isActive = (key: string) => loc.pathname.startsWith(key);
  const onTasks = loc.pathname.startsWith("/tasks");
  const q = sp.get("q") ?? "";

  const onSearch = (v: string) => {
    if (!onTasks) {
      nav(v ? `/tasks?q=${encodeURIComponent(v)}` : "/tasks");
      return;
    }
    if (v) sp.set("q", v);
    else sp.delete("q");
    setSp(sp, { replace: true });
  };

  return (
    <Layout style={{ minHeight: "100vh", background: "var(--app-bg)" }}>
      <Sider
        width={88}
        style={{ background: "transparent", paddingTop: 8 }}
        breakpoint="lg"
        collapsedWidth={0}
      >
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            padding: "28px 0",
          }}
        >
          {items.map((it) => (
            <Tooltip key={it.key} title={it.label} placement="right">
              <button
                className={`rk-nav-link${isActive(it.key) ? " active" : ""}`}
                onClick={() => nav(it.key)}
                aria-label={it.label}
              >
                {it.icon}
              </button>
            </Tooltip>
          ))}
        </div>
      </Sider>

      <Layout style={{ background: "transparent" }}>
        <Header
          style={{
            background: "transparent",
            display: "flex",
            alignItems: "center",
            gap: 20,
            padding: "16px 24px 8px 0",
            height: "auto",
            lineHeight: "normal",
          }}
        >
          <Input
            allowClear
            value={q}
            onChange={(e) => onSearch(e.target.value)}
            prefix={<SearchOutlined style={{ color: "#9aa0b5" }} />}
            placeholder="搜索任务 / 人"
            variant="borderless"
            style={{
              maxWidth: 420,
              borderRadius: 20,
              height: 40,
              background: "#fff",
              boxShadow: "var(--search-shadow)",
            }}
          />
          <div style={{ flex: 1 }} />
          <NotificationBell />
          <Dropdown
            menu={{
              items: [
                {
                  key: "logout",
                  label: "退出登录",
                  icon: <LogoutOutlined />,
                  onClick: logout,
                },
              ],
            }}
          >
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 8,
                cursor: "pointer",
                padding: "4px 12px 4px 4px",
                borderRadius: 20,
                background: "#fff",
                boxShadow: "var(--search-shadow)",
              }}
            >
              <Avatar size={30} icon={<UserOutlined />} />
              <span style={{ color: "var(--ink)", fontWeight: 600 }}>
                {user?.name}
              </span>
              <Tag color={ROLE[user?.role || "user"]?.color} style={{ margin: 0 }}>
                {ROLE[user?.role || "user"]?.label}
              </Tag>
            </div>
          </Dropdown>
        </Header>

        <Content style={{ padding: "8px 24px 24px 0" }}>{children}</Content>
      </Layout>
    </Layout>
  );
}
