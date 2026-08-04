import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { ConfigProvider, theme as antdTheme } from "antd";
import zhCN from "antd/locale/zh_CN";
import App from "./App";
import { AuthProvider } from "./auth";
import "./styles/global.css";

const FONT_FAMILY =
  "'DM Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', sans-serif";

const theme = {
  algorithm: antdTheme.defaultAlgorithm,
  token: {
    colorPrimary: "#4f3ff0",
    colorInfo: "#4f3ff0",
    colorTextBase: "#1f1c2e",
    colorBgLayout: "#f3f6fd",
    fontFamily: FONT_FAMILY,
    borderRadius: 12,
    controlHeight: 38,
    boxShadow: "0 10px 30px -12px rgba(71, 82, 107, 0.16)",
    boxShadowSecondary: "0 12px 24px -14px rgba(71, 82, 107, 0.28)",
  },
  components: {
    Layout: {
      headerBg: "transparent",
      bodyBg: "#f3f6fd",
      siderBg: "transparent",
    },
    Card: {
      borderRadiusLG: 20,
      boxShadow: "0 10px 30px -12px rgba(71, 82, 107, 0.16)",
      headerFontSize: 18,
    },
    Button: {
      borderRadius: 10,
      controlHeight: 38,
      fontWeight: 500,
      // 主按钮:柔和的品牌色投影,替代生硬的实心块
      primaryShadow: "0 8px 20px -10px rgba(79, 63, 240, 0.55)",
      // 默认按钮:浅紫描边 + 极轻投影,与浅色卡片同一质感
      defaultShadow: "0 2px 6px 0 rgba(136, 148, 171, 0.16)",
      defaultBg: "rgba(255, 255, 255, 0.9)",
      defaultColor: "#302d40",
      defaultBorderColor: "rgba(79, 63, 240, 0.18)",
      defaultHoverBg: "#ffffff",
      defaultHoverColor: "#4f3ff0",
      defaultHoverBorderColor: "rgba(79, 63, 240, 0.5)",
      defaultActiveColor: "#3d2fc7",
      defaultActiveBorderColor: "rgba(79, 63, 240, 0.7)",
    },
    Table: {
      headerBg: "#f7f8fc",
      headerColor: "#6b6880",
      borderRadiusLG: 16,
      rowHoverBg: "#f7f8ff",
      headerSplitColor: "transparent",
    },
    Input: { borderRadius: 10 },
    Select: { borderRadius: 10 },
    Modal: { borderRadiusLG: 20 },
    Tag: { borderRadiusSM: 8 },
    Segmented: { borderRadius: 10 },
  },
};

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ConfigProvider locale={zhCN} theme={theme}>
      <BrowserRouter basename={import.meta.env.BASE_URL}>
        <AuthProvider>
          <App />
        </AuthProvider>
      </BrowserRouter>
    </ConfigProvider>
  </React.StrictMode>
);
