import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 部署基路径。默认 "/"(独占域名/端口);挂在网关子路径下时,由 deploy.sh 按
// config.ini 的 APP_BASE_URL 派生出 VITE_BASE_PATH 传入(如 "/rubick/")。
// 静态资源前缀、前端路由 basename、/api 前缀都跟随它,单一来源不漂移。
export default defineConfig({
  base: process.env.VITE_BASE_PATH || "/",
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://localhost:8000",
    },
  },
});
