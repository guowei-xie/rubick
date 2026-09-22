import { useEffect, useState } from "react";
import {
  Alert,
  Button,
  Input,
  Modal,
  Popconfirm,
  Space,
  Typography,
  message,
} from "antd";
import { CopyOutlined } from "@ant-design/icons";

import { BASE, getApiToken, getAuthConfig } from "../api";
import { copyText } from "../clipboard";
import { useApiTokenIssue } from "./useApiTokenIssue";

/** 「Agent Skill」按钮弹出的安装指令面板。
 *
 *  指令是给用户本机的 AI Agent 看的一句话:Agent 读到地址后自取 skill 文件并完成安装
 *  (skill 文件开头有给安装者 Agent 的指引,链路在那里闭环)。所以 URL 必须是
 *  **对外规范地址**(后端 APP_BASE_URL,经 /auth/config 下发)——用户此刻可能正通过
 *  内网 IP 或反代访问平台,浏览器地址栏不一定是 Agent 到得了的地址;配置没取回来才
 *  回落 window.location。
 *
 *  指令还能带上一枚 API Token(签发语义见 useApiTokenIssue)。已有 token 的人走的是
 *  **重置**,会掐断他正在跑的脚本,所以那一侧套 Popconfirm;从没签过的人无物可坏,直接点。
 *  不签发也照常能复制 —— 带 token 是增强,不是前置条件。
 *
 *  为什么做成「展示 + 手动点复制」而不是点了按钮直接写剪贴板:剪贴板静默改写是不可见的
 *  副作用,用户理应在复制前看到自己即将发出去的内容(里面含平台地址,可能还含 token)。
 *
 *  地址与 token 状态**自己在打开时取**,不由页面传进来:任务页只替管理者取 config
 *  (为了申请链接),把这两样挂到页面 state 上等于让不点开弹窗的人也替它付请求。 */
export default function AgentSkillModal({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const [appBaseUrl, setAppBaseUrl] = useState("");
  // null = 状态不明(还没问出来,或没问到):此时**不渲染任何签发按钮**,免得「生成」(不确认)
  // 错盖到「重置」(要确认)的位置上,手快的人一点就把旧 token 作废了
  const [hasToken, setHasToken] = useState<boolean | null>(null);
  const { freshToken, issuing, issue, reset } = useApiTokenIssue();

  useEffect(() => {
    if (!open) return;
    reset();
    // 两处失败都**不弹错**,弹窗降级但仍可用:地址回落到当前来源,签发区整块不渲染。
    // 地址只取一次 —— 它由后端配置决定,不重启不会变,每次重开都再问一遍是白跑一趟
    if (!appBaseUrl) {
      getAuthConfig()
        .then((c) => setAppBaseUrl(c.app_base_url))
        .catch(() => {});
    }
    getApiToken()
      .then((i) => setHasToken(i.exists))
      .catch(() => setHasToken(null));
  }, [open]);

  const base = appBaseUrl || `${window.location.origin}${BASE}`;
  // 前半句两种情形共用,免得改文案时漏掉另一份。平台地址除了拼进文件 URL 还单独写一次:
  // 子路径部署时从文件 URL 反推 BASE_URL 是 Agent 最容易掉的坑(skill 文件里另写了推导规则,
  // 那是给从别处拿到文件、手上没有这句指令的 Agent 兜底的)
  const instruction =
    `帮我安装 skill,地址:${base}/rubick-skill.md` +
    (freshToken
      ? `;平台地址 ${base},我的 API Token 是 ${freshToken},装好后配到环境变量 RUBICK_BASE_URL / RUBICK_TOKEN,不要写进代码或日志。`
      : "");

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

  // 签发过就不能让弹窗静悄悄关掉:明文只有这一份,关掉即永久丢失 —— 而对「已有 token」
  // 的用户,刚才那一下还是重置,白丢的同时旧 token 也已经作废了。复制成功走的是
  // doCopy 里的 onClose(),不经过这里,所以点了复制的人不会被多问一句。
  // ApiTokenModal 没有这道拦截是有意的:那里用户**专程来拿 token**,不会顺手关掉;
  // 这里他是来拿一句安装指令的,签发只是半路发生的事,随手关窗才是常态。
  const requestClose = () => {
    if (!freshToken) {
      onClose();
      return;
    }
    Modal.confirm({
      title: "还没复制,确定关闭?",
      content: "这句话里的 Token 只显示这一次,关掉就再也看不到了 —— 下次只能重新签发一枚,旧的随之失效。",
      okText: "仍然关闭",
      cancelText: "回去复制",
      onOk: onClose,
    });
  };

  // 套在 Popconfirm 里时不能自带 onClick:Popconfirm 会给子节点挂自己的,两者并存等于
  // 「弹出确认框的同时已经签发了」
  const issueBtn = (
    <Button loading={issuing} onClick={hasToken ? undefined : issue}>
      {hasToken ? "重置 Token 并附带" : "生成 Token 并附带"}
    </Button>
  );

  return (
    <Modal
      title="安装 Agent Skill"
      open={open}
      onCancel={requestClose}
      footer={
        <Space size={8}>
          <Button onClick={requestClose}>取消</Button>
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
        {freshToken && (
          <Alert
            type="warning"
            showIcon
            message="这句话里带着你的 API Token"
            description="Token 等同你本人的身份,只发给你自己信任的 AI Agent —— 别贴进公开群、工单或截图。它只显示这一次,关闭弹窗后无法再查看。"
          />
        )}
        {/* readOnly 而非 disabled:置灰的输入框在多数浏览器里选不中、复制不走。
            带上 token 后这句话一行装不下,交给 TextArea 自动长高 */}
        <Input.TextArea
          value={instruction}
          readOnly
          autoSize={{ minRows: 1, maxRows: 6 }}
          onFocus={(e) => e.target.select()}
        />
        {!freshToken && hasToken !== null && (
          <Space size={8} align="start">
            {hasToken ? (
              <Popconfirm
                title="重置 API Token 并附带?"
                description="将生成新 Token 附进指令,旧 Token 立即失效"
                okText="重置"
                cancelText="取消"
                onConfirm={issue}
              >
                {issueBtn}
              </Popconfirm>
            ) : (
              issueBtn
            )}
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {hasToken
                ? "平台只存 Token 的哈希、不留明文,已签发的那枚取不回来 —— 要附带就得换一枚新的。"
                : "Agent 还需要一枚 Token 才能取数,附在同一句话里发给它即可。"}
            </Typography.Text>
          </Space>
        )}
      </Space>
    </Modal>
  );
}
