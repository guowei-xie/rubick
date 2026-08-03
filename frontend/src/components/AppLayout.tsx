import { Layout, Menu, Dropdown, Avatar, Space, Tag } from "antd";
import { UserOutlined } from "@ant-design/icons";
import { useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../auth";
import NotificationBell from "./NotificationBell";
import { ROLE } from "./StatusTag";

const { Header, Content } = Layout;

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const { user, logout } = useAuth();
  const nav = useNavigate();
  const loc = useLocation();

  const items = [{ key: "/tasks", label: "任务列表" }];
  if (user?.role === "admin")
    items.push(
      { key: "/admin/users", label: "用户管理" },
      { key: "/admin/datasources", label: "数据源" },
      { key: "/admin/audit", label: "审计" }
    );

  const selected = items.map((i) => i.key).filter((k) => loc.pathname.startsWith(k));

  return (
    <Layout style={{ minHeight: "100vh" }}>
      <Header style={{ display: "flex", alignItems: "center", gap: 24 }}>
        <div style={{ color: "#fff", fontWeight: 700, fontSize: 18 }}>拉比克 Rubick</div>
        <Menu
          theme="dark"
          mode="horizontal"
          selectedKeys={selected}
          items={items}
          onClick={(e) => nav(e.key)}
          style={{ flex: 1, minWidth: 0 }}
        />
        <NotificationBell />
        <Dropdown
          menu={{ items: [{ key: "logout", label: "退出登录", onClick: logout }] }}
        >
          <Space style={{ color: "#fff", cursor: "pointer" }}>
            <Avatar size="small" icon={<UserOutlined />} />
            {user?.name}
            <Tag color="blue">{ROLE[user?.role || "user"]?.label}</Tag>
          </Space>
        </Dropdown>
      </Header>
      <Content style={{ padding: 24 }}>{children}</Content>
    </Layout>
  );
}
