import { useEffect, useRef, useState } from "react";
import { Button, message, Select } from "antd";
import { useNavigate, useSearchParams } from "react-router-dom";
import { AuthConfig, feishuCallback, getAuthConfig, mockLogin } from "../api";
import { useAuth } from "../auth";
import "../styles/login.css";

const MOCK_USERS = [
  { open_id: "ou_admin", label: "管理员小A(admin)" },
  { open_id: "ou_analyst", label: "管理员小B(admin)" },
  { open_id: "ou_dev", label: "开发小D(developer)" },
  { open_id: "ou_viewer", label: "普通小C(user)" },
];

export default function LoginPage() {
  const { setToken, user } = useAuth();
  const nav = useNavigate();
  const [sp] = useSearchParams();
  const [cfg, setCfg] = useState<AuthConfig | null>(null);
  const [openId, setOpenId] = useState("ou_viewer");
  const [loading, setLoading] = useState(false);
  const codeHandled = useRef(false); // 防止 StrictMode 下用同一 code 重复换取(第二次必失败)

  useEffect(() => {
    if (user && !sp.get("code")) nav("/tasks");
  }, [user]);

  useEffect(() => {
    getAuthConfig().then(setCfg);
  }, []);

  // 飞书回调:URL 带 code 时换 token(仅执行一次)
  useEffect(() => {
    const code = sp.get("code");
    if (!code || codeHandled.current) return;
    codeHandled.current = true;
    feishuCallback(code)
      .then((res) => {
        setToken(res.access_token, res.user);
        message.success(`欢迎,${res.user.name}`);
        nav("/tasks");
      })
      .catch((e: any) => message.error(e.response?.data?.detail || "飞书登录失败"));
  }, [sp]);

  const doMockLogin = async () => {
    setLoading(true);
    try {
      const res = await mockLogin(openId);
      setToken(res.access_token, res.user);
      message.success(`欢迎,${res.user.name}`);
      nav("/tasks");
    } catch (e: any) {
      message.error(e.response?.data?.detail || "登录失败");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        minHeight: "100vh",
        padding: 24,
      }}
    >
      <div className="rk-login-title">
        <span>R</span>
        <span className="rk-login-bouncer-wrapper">
          <span className="rk-login-letter">u</span>
          <span className="rk-login-bouncer">🔍</span>
        </span>
        <span>bick</span>
      </div>
      <div className="rk-login-subtitle">拉比克</div>
      {/* 去掉卡片背景,只保留登录按钮;
          mock 与正式登录按钮占据相同位置,便于评估真实 UI 效果,
          具体演示身份的选择放到右上角不显眼的入口。 */}
      <div
        style={{ display: "flex", flexDirection: "column", gap: 12, width: 320 }}
      >
        {cfg?.feishu_authorize_url && (
          <Button
            block
            href={cfg.feishu_authorize_url}
            className="rk-login-btn rk-breathe"
          >
            飞书登录
          </Button>
        )}
        {cfg?.mock_auth && (
          <Button
            block
            loading={loading}
            onClick={doMockLogin}
            className="rk-login-btn rk-breathe"
          >
            mock 登录
          </Button>
        )}
      </div>

      {/* 没有飞书应用权限的人点上面那个按钮会被飞书挡回来,然后就退回到这一页 ——
          这条链接是他在这一页上唯一能自己走通的路,不必先在通讯录里找到一个已有权限的同事。
          是链接不是复制:他要的是「点过去申请」,「复制了发给别人」是任务列表页那枚按钮的事。
          样式次要,不与登录按钮争视线;没配 FEISHU_APP_APPLY_URL 就整行不出现。 */}
      {cfg?.feishu_apply_url && (
        <a
          className="rk-login-apply"
          href={cfg.feishu_apply_url}
          target="_blank"
          rel="noopener noreferrer"
        >
          没有权限，登不进去？申请加入这个飞书应用
        </a>
      )}

      {/* 右上角:仅提供 mock 身份选择,尽量不显眼的开发入口 */}
      {cfg?.mock_auth && (
        <div className="rk-mock-corner">
          <span className="rk-mock-corner-label">MOCK</span>
          <Select
            size="small"
            variant="borderless"
            value={openId}
            onChange={setOpenId}
            options={MOCK_USERS.map((u) => ({ value: u.open_id, label: u.label }))}
            style={{ width: 150 }}
          />
        </div>
      )}
    </div>
  );
}
