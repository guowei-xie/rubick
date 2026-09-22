import { Button, Input, Modal, Space, Typography, message } from "antd";
import { CopyOutlined } from "@ant-design/icons";

import { withBase } from "../api";
import { copyText } from "../clipboard";

/** 「Agent Skill」按钮弹出的安装指令面板。
 *
 *  指令是给用户本机的 AI Agent 看的一句话:Agent 读到地址后自取 skill 文件并完成安装
 *  (skill 文件开头有给安装者 Agent 的指引,链路在那里闭环)。所以 URL 必须是
 *  **对外规范地址**(后端 APP_BASE_URL,经 /auth/config 下发)——用户此刻可能正通过
 *  内网 IP 或反代访问平台,浏览器地址栏不一定是 Agent 到得了的地址;配置没取回来才
 *  回落 window.location。
 *
 *  为什么做成「展示 + 手动点复制」而不是点了按钮直接写剪贴板:剪贴板静默改写是不可见的
 *  副作用,用户理应在复制前看到自己即将发出去的内容(里面含平台地址)。 */
export default function AgentSkillModal({
  open,
  onClose,
  appBaseUrl,
}: {
  open: boolean;
  onClose: () => void;
  appBaseUrl: string;
}) {
  const url = appBaseUrl
    ? `${appBaseUrl}/rubick-skill.md`
    : `${window.location.origin}${withBase("/rubick-skill.md")}`;
  const instruction = `帮我安装 skill,地址:${url}`;

  const doCopy = async () => {
    // 成功与否以 copyText 的返回为准(它为什么必须返回布尔,见 clipboard.ts)
    if (await copyText(instruction)) {
      message.success("已复制,粘贴给你的 AI Agent 即可自动安装");
      onClose();
    } else {
      // 失败时留着弹窗:输入框里的内容还能手动选中复制
      message.error("复制失败,请手动选中复制");
    }
  };

  return (
    <Modal
      title="安装 Agent Skill"
      open={open}
      onCancel={onClose}
      footer={
        <Space size={8}>
          <Button onClick={onClose}>取消</Button>
          <Button type="primary" icon={<CopyOutlined />} onClick={doCopy}>
            复制
          </Button>
        </Space>
      }
      width={520}
    >
      <Space direction="vertical" size={12} style={{ width: "100%" }}>
        <Typography.Text type="secondary">
          把下面这句话粘贴给你的 AI Agent,它会自动访问地址下载并安装 rubick-skill:
        </Typography.Text>
        {/* readOnly 而非 disabled:置灰的输入框在多数浏览器里选不中、复制不走 */}
        <Input value={instruction} readOnly onFocus={(e) => e.target.select()} />
      </Space>
    </Modal>
  );
}
