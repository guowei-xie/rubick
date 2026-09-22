import { useEffect, useState } from "react";
import { Alert, Button, Input, Modal, Popconfirm, Space, Typography, message } from "antd";
import { CopyOutlined } from "@ant-design/icons";
import { ApiTokenInfo, errMsg, getApiToken, revokeApiToken } from "../api";
import { copyText } from "../clipboard";
import { fmtTime } from "../format";
import { useApiTokenIssue } from "./useApiTokenIssue";

/** 开放 API 的个人凭证管理(头像下拉 → API Token)。
 *
 *  三种形态对应服务端的三条规则:
 *   · 还没生成过 → 一个「生成 Token」按钮;
 *   · 刚生成/重置完 → 一次性展示明文 + 复制,关闭弹窗后**再也看不到**(服务端不回传本体);
 *   · 已存在 → 只看签发/最近使用时间,重置与吊销各自带二次确认。 */
export default function ApiTokenModal({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const [info, setInfo] = useState<ApiTokenInfo | null>(null);
  const [loading, setLoading] = useState(false);
  const [revoking, setRevoking] = useState(false);
  // 签发流程(含「刚拿到的明文只活一次弹窗会话」)与 Agent Skill 弹窗共用一处
  const { freshToken, issuing, issue, reset } = useApiTokenIssue();

  const load = () => {
    setLoading(true);
    getApiToken()
      .then(setInfo)
      .catch((e) => message.error(errMsg(e, "查询 API Token 失败")))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    if (!open) return;
    reset();
    load();
  }, [open]);

  const doRevoke = async () => {
    setRevoking(true);
    try {
      await revokeApiToken();
      message.success("API Token 已吊销");
      // 吊销按钮只在状态视图里(freshToken 为空),无需再清一次
      setInfo({ exists: false, issued_at: null, last_used_at: null });
    } catch (e: any) {
      message.error(errMsg(e, "吊销 API Token 失败"));
    } finally {
      setRevoking(false);
    }
  };

  const doCopy = async () => {
    if (!freshToken) return;
    // 成功与否以 copyText 的返回为准(它为什么必须返回布尔,见 clipboard.ts)
    (await copyText(freshToken))
      ? message.success("已复制,请妥善保存")
      : message.error("复制失败,请手动选中复制");
  };

  return (
    <Modal
      title="API Token"
      open={open}
      onCancel={onClose}
      footer={
        <Button type="primary" onClick={onClose}>
          关闭
        </Button>
      }
      width={520}
    >
      {freshToken ? (
        <Space direction="vertical" size={12} style={{ width: "100%" }}>
          <Alert
            type="warning"
            showIcon
            message="Token 只显示这一次,关闭弹窗后将无法再查看"
          />
          <Space.Compact style={{ width: "100%" }}>
            {/* readOnly 而非 disabled:置灰的输入框在多数浏览器里选不中、复制不走 */}
            <Input value={freshToken} readOnly onFocus={(e) => e.target.select()} />
            <Button icon={<CopyOutlined />} onClick={doCopy}>
              复制
            </Button>
          </Space.Compact>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            调用开放 API 时放在请求头:Authorization: Bearer &lt;token&gt;
          </Typography.Text>
        </Space>
      ) : (
        <Space direction="vertical" size={12} style={{ width: "100%" }} align="start">
          {info?.exists ? (
            <>
              <Typography.Text>
                签发时间:{fmtTime(info.issued_at)} · 最近使用:{fmtTime(info.last_used_at)}
              </Typography.Text>
              <Space size={12}>
                <Popconfirm
                  title="重置 API Token?"
                  description="将生成新 Token,旧 Token 立即失效"
                  okText="重置"
                  cancelText="取消"
                  onConfirm={issue}
                >
                  <Button loading={issuing}>重置</Button>
                </Popconfirm>
                <Popconfirm
                  title="吊销 API Token?"
                  description="吊销后该 Token 立即失效,开放 API 将无法再调用"
                  okText="吊销"
                  okButtonProps={{ danger: true }}
                  cancelText="取消"
                  onConfirm={doRevoke}
                >
                  <Button danger loading={revoking}>
                    吊销
                  </Button>
                </Popconfirm>
              </Space>
            </>
          ) : (
            <>
              <Typography.Text type="secondary">
                还没有 API Token。生成后即可通过开放 API 触发已允许 API 调用的任务运行。
                若是要交给自己的 AI Agent,走任务列表页右上的「Agent Skill」更省事 ——
                那里一次给齐安装指令与 Token。
              </Typography.Text>
              <Button type="primary" loading={loading || issuing} onClick={issue}>
                生成 Token
              </Button>
            </>
          )}
        </Space>
      )}
    </Modal>
  );
}
