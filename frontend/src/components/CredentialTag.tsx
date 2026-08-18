import { Tag, Tooltip } from "antd";
import { TeamCredentialStatus } from "../api";
import { CREDENTIAL_STATUS } from "./StatusTag";
import { fmtTime } from "../format";

/** 状态键:三态口径集中一处,供标签与任务卡片的告警胶囊共用。 */
export function credentialState(c: TeamCredentialStatus): keyof typeof CREDENTIAL_STATUS {
  if (!c.configured) return "unconfigured";
  return c.verified ? "verified" : "unverified";
}

/**
 * 团队取数账号的状态标签。团队页、平台管理员总览页与任务卡片的告警胶囊共用,
 * 免得同一份三态逻辑与配色各写一遍。
 *
 * withUsername:治理矩阵里一格要同时显示「用的哪个库账号」,故把用户名并进标签文本。
 * 但库用户名是半机密(Hive auth=NONE 下它就是完整凭证),后端只对团队管理员与平台管理员
 * 返回它 —— 拿不到时(username 为 null)自动退回纯状态标签,不显示空白。
 */
export default function CredentialTag({
  c,
  withUsername = false,
}: {
  c: TeamCredentialStatus;
  withUsername?: boolean;
}) {
  const meta = CREDENTIAL_STATUS[credentialState(c)];
  if (!c.configured) return <Tag color={meta.color}>{meta.label}</Tag>;

  const tip = c.verified
    ? `最近测通:${fmtTime(c.last_verified_at)}`
    : c.last_verify_error || "还没测过,或改过账号后需要重新测试";
  const text =
    withUsername && c.username
      ? `${c.username}${c.verified ? "" : " · 未测通"}`
      : meta.label;

  return (
    <Tooltip title={tip}>
      <Tag color={meta.color}>{text}</Tag>
    </Tooltip>
  );
}
