import { Layout, Dropdown, Avatar, Input, Tag, Tooltip } from "antd";
import {
  UserOutlined,
  AppstoreOutlined,
  TeamOutlined,
  IdcardOutlined,
  UsergroupAddOutlined,
  DatabaseOutlined,
  AuditOutlined,
  SearchOutlined,
  LogoutOutlined,
  KeyOutlined,
} from "@ant-design/icons";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { isManager, useAuth } from "../auth";
import NotificationBell from "./NotificationBell";
import { ROLE } from "./StatusTag";

const { Sider, Header, Content } = Layout;

type NavItem = { key: string; label: string; icon: React.ReactNode };

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const { user, logout } = useAuth();
  const nav = useNavigate();
  const loc = useLocation();
  const [sp, setSp] = useSearchParams();

  // 管理者(开发者/管理员)= 能建任务的角色。团队入口只给他们:普通用户不入团队,
  // 他们拿的是任务级授权。
  const manager = isManager(user);

  const items: NavItem[] = [
    { key: "/tasks", label: "任务列表", icon: <AppstoreOutlined /> },
  ];
  // 侧边栏第一个「管理者可见但非管理员专属」的入口。团队页是一等公民(任务归属、
  // 团队账号、编辑权都在那儿),故上移到侧边栏而不是藏在头像下拉里。
  if (manager) items.push({ key: "/teams", label: "我的团队", icon: <TeamOutlined /> });
  if (user?.role === "admin")
    items.push(
      // 用户管理做的是身份/角色,让 TeamOutlined 归给团队
      { key: "/admin/users", label: "用户管理", icon: <IdcardOutlined /> },
      { key: "/admin/teams", label: "团队管理", icon: <UsergroupAddOutlined /> },
      { key: "/admin/datasources", label: "数据源", icon: <DatabaseOutlined /> },
      { key: "/admin/credentials", label: "取数账号", icon: <KeyOutlined /> },
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
          {/* 提示语要与 TasksPage 的 matchQ 覆盖面一致:任务编号 / 任务名 / 作者 / 被授权人 / 团队名。
              任务归属团队后多了团队名这一路匹配,不写出来没人会想到能这么搜。
              「编号」摆在最前:它是唯一能精确定位到一条的搜法,也是卡片上那个一键复制的去处
              (复制给的是纯数字,粘进来直接就能搜;写 #128 也认)。 */}
          <Input
            allowClear
            value={q}
            onChange={(e) => onSearch(e.target.value)}
            prefix={<SearchOutlined style={{ color: "#9aa0b5" }} />}
            placeholder="搜索编号 / 任务 / 人 / 团队"
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
                // 「我的取数账号」已随个人取数账号功能下线;团队账号在左侧「我的团队」里
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
