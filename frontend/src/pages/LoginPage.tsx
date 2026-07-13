import { useEffect, useRef, useState } from "react";
import { Button, Card, Divider, message, Select, Space, Typography } from "antd";
import { useNavigate, useSearchParams } from "react-router-dom";
import { feishuCallback, getAuthConfig, mockLogin } from "../api";
import { useAuth } from "../auth";

const MOCK_USERS = [
  { open_id: "ou_admin", label: "管理员小A(admin)" },
  { open_id: "ou_analyst", label: "商分小B(analyst)" },
  { open_id: "ou_viewer", label: "业务小C(user)" },
];

export default function LoginPage() {
  const { setToken, user } = useAuth();
  const nav = useNavigate();
  const [sp] = useSearchParams();
  const [cfg, setCfg] = useState<any>(null);
  const [openId, setOpenId] = useState("ou_viewer");
  const [loading, setLoading] = useState(false);
  const codeHandled = useRef(false); // 防止 StrictMode 下用同一 code 重复换取(第二次必失败)

  useEffect(() => {
    if (user && !sp.get("code")) nav("/templates");
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
        nav("/templates");
      })
      .catch((e: any) => message.error(e.response?.data?.detail || "飞书登录失败"));
  }, [sp]);

  const doMockLogin = async () => {
    setLoading(true);
    try {
      const res = await mockLogin(openId);
      setToken(res.access_token, res.user);
      message.success(`欢迎,${res.user.name}`);
      nav("/templates");
    } catch (e: any) {
      message.error(e.response?.data?.detail || "登录失败");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ display: "flex", justifyContent: "center", paddingTop: 120 }}>
      <Card title="登录拉比克 Rubick" style={{ width: 420 }}>
        {cfg?.feishu_authorize_url && (
          <Space direction="vertical" style={{ width: "100%" }}>
            <Typography.Text type="secondary">使用企业飞书账号登录</Typography.Text>
            <Button type="primary" block href={cfg.feishu_authorize_url}>
              飞书登录
            </Button>
          </Space>
        )}
        {cfg?.feishu_authorize_url && cfg?.mock_auth && <Divider plain>或</Divider>}
        {cfg?.mock_auth && (
          <Space direction="vertical" style={{ width: "100%" }}>
            <Typography.Text type="secondary">
              开发模式(MOCK_AUTH):选择一个演示身份登录
            </Typography.Text>
            <Select
              style={{ width: "100%" }}
              value={openId}
              onChange={setOpenId}
              options={MOCK_USERS.map((u) => ({ value: u.open_id, label: u.label }))}
            />
            <Button block loading={loading} onClick={doMockLogin}>
              mock 登录
            </Button>
          </Space>
        )}
        <Divider />
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          未授权用户登录后将看不到任何模板。
        </Typography.Text>
      </Card>
    </div>
  );
}
