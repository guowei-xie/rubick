import { Layout, Dropdown, Avatar, Tag, Tooltip } from "antd";
import {
  UserOutlined,
  AppstoreOutlined,
  TeamOutlined,
  IdcardOutlined,
  UsergroupAddOutlined,
  DatabaseOutlined,
  AuditOutlined,
  LogoutOutlined,
  KeyOutlined,
  LineChartOutlined,
} from "@ant-design/icons";
import { useLocation, useNavigate } from "react-router-dom";
import { canSeeAnalytics, isManager, useAuth } from "../auth";
import NotificationBell from "./NotificationBell";
import { ROLE } from "./StatusTag";

const { Sider, Header, Content } = Layout;

type NavItem = { key: string; label: string; icon: React.ReactNode };

/** 侧边导航:谁看得见哪些入口,以及当前在哪一个。
 *
 *  **读 URL 的 hook 收在这一层,不放 AppLayout** —— 任务列表里逐字符写 ?q= 会让每个
 *  LocationContext 的消费者跟着重渲染。AppLayout 自己不订阅,它与 Header 那半边
 *  (头像、下拉菜单)就整棵 bail out,只剩这几枚图标重跑。
 *  注意这条收益是**易碎**的:`App` / `Protected` 里任何一个读 location 的 hook 都会
 *  把整个顶栏重新拉进重渲染路径。(NotificationBell 因为自己调 useNavigate —— v6 里它
 *  内部就读 location —— 本来就不在这份收益里,它的 Popover 内容要另外 memo 才省得下。)
 */
function SideNav() {
  const { user } = useAuth();
  const nav = useNavigate();
  const loc = useLocation();
  const isActive = (key: string) => loc.pathname.startsWith(key);

  // 管理者(开发者/管理员)= 能建任务的角色。团队入口只给他们:普通用户不入团队,
  // 他们拿的是任务级授权。
  const manager = isManager(user);

  const items: NavItem[] = [
    { key: "/tasks", label: "任务列表", icon: <AppstoreOutlined /> },
  ];
  // 侧边栏第一个「管理者可见但非管理员专属」的入口。团队页是一等公民(任务归属、
  // 团队账号、编辑权都在那儿),故上移到侧边栏而不是藏在头像下拉里。
  if (manager) items.push({ key: "/teams", label: "我的团队", icon: <TeamOutlined /> });
  // 运营分析:平台管理员看全平台,团队管理员只看自己的队。判据与路由守卫共用
  // canSeeAnalytics,菜单与路由不会漂移。放在团队之后、管理员那批之前 ——
  // 它不是管理员专属,但也不是所有人都有。
  if (canSeeAnalytics(user))
    items.push({ key: "/analytics", label: "运营分析", icon: <LineChartOutlined /> });
  if (user?.role === "admin")
    items.push(
      // 用户管理做的是身份/角色,让 TeamOutlined 归给团队
      { key: "/admin/users", label: "用户管理", icon: <IdcardOutlined /> },
      { key: "/admin/teams", label: "团队管理", icon: <UsergroupAddOutlined /> },
      { key: "/admin/datasources", label: "数据源", icon: <DatabaseOutlined /> },
      { key: "/admin/credentials", label: "取数账号", icon: <KeyOutlined /> },
      { key: "/admin/audit", label: "审计", icon: <AuditOutlined /> }
    );

  return (
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
  );
}

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const { user, logout } = useAuth();

  return (
    <Layout style={{ minHeight: "100vh", background: "var(--app-bg)" }}>
      <Sider
        width={88}
        style={{ background: "transparent", paddingTop: 8 }}
        breakpoint="lg"
        collapsedWidth={0}
      >
        <SideNav />
      </Sider>

      <Layout style={{ background: "transparent" }}>
        {/* 全局 chrome 只放**跨页面**的东西:此刻是谁(头像)、跨任务的通知(铃铛)。
            只服务某一页的控件放那一页 —— 任务搜索框就是因此下沉到任务列表里的:
            它在别的页面上既没东西可搜,输入还会把人弹走。 */}
        <Header
          style={{
            background: "transparent",
            display: "flex",
            alignItems: "center",
            justifyContent: "flex-end",
            gap: 20,
            padding: "16px 24px 8px 0",
            height: "auto",
            lineHeight: "normal",
          }}
        >
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
                boxShadow: "var(--pill-shadow)",
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
