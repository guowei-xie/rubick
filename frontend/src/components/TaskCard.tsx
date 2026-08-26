import { Avatar, Button, Dropdown, Tooltip } from "antd";
import { ClockCircleOutlined, MoreOutlined, UserOutlined, WarningOutlined } from "@ant-design/icons";
import { CREDENTIAL_STATUS, TEMPLATE_STATUS } from "./StatusTag";
import { fmtTime } from "../format";
import {
  AuthorizedAvatars,
  credentialWarnText,
  GrantButton,
  runHint,
  scheduleHint,
  scheduleLabel,
  showCredentialWarn,
  stop,
  TaskHandlers,
  taskMenuItems,
  taskTimeMeta,
} from "./taskActions";

const fmt = (t: string) => fmtTime(t, false);

// 数据源 / 团队两个小标签共用:flex 子项 + minWidth:0,两个都长时按内容比例收缩、
// 各自省略号截断并排一行;只有一个时它能占满整行不被无谓截断。
const chipStyle: React.CSSProperties = {
  flex: "0 1 auto",
  minWidth: 0,
  maxWidth: "100%",
  fontSize: 12,
  fontWeight: 500,
  color: "var(--ink-secondary)",
  background: "var(--app-bg)",
  borderRadius: 6,
  padding: "2px 8px",
  overflow: "hidden",
  whiteSpace: "nowrap",
  textOverflow: "ellipsis",
};

export default function TaskCard({
  task: r,
  h,
}: {
  task: any;
  h: TaskHandlers;
}) {
  const users: any[] = r.authorized_users || [];
  // 卡头时间:最后运行 → 最后编辑 → 创建(口径与列表视图共用,见 taskActions)
  const { value: timeVal, label: timeLabel } = taskTimeMeta(r);

  const meta = TEMPLATE_STATUS[r.status];

  // ⋮ 菜单与列表视图共用一份构造,见 taskActions.taskMenuItems
  const moreItems = taskMenuItems(r, h);

  return (
    <div
      className={r.can_run ? "rk-lift" : undefined}
      onClick={() => r.can_run && h.onRun(r)}
      title={runHint(r)}
      style={{
        background: "#fff",
        border: "1px solid #edf0f7",
        borderRadius: 16,
        padding: "14px 16px",
        height: "100%",
        display: "flex",
        flexDirection: "column",
        boxShadow: "var(--card-shadow)",
        cursor: r.can_run ? "pointer" : "default",
        opacity: r.can_run ? 1 : 0.85,
      }}
    >
      {/* 卡头:状态色点 + 创建时间 + ⋮ */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: 8,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          {/* 状态只用颜色表达(绿=已上线、黄=草稿、红=已下线),不再占文字位置。
              语义由悬停 title 兜底;title 挂在色点自身上,会盖住卡片外层那句
              「点击填参取数」,两者不会同时弹出(换成 Tooltip 则会叠加)。
              aria-label 是给读屏的:颜色不能是唯一的信息载体。 */}
          <span
            role="img"
            title={meta?.label ?? r.status}
            aria-label={`状态:${meta?.label ?? r.status}`}
            style={{
              width: 8,
              height: 8,
              borderRadius: "50%",
              background: meta?.dot ?? "#bfbfbf",
              display: "inline-block",
              flexShrink: 0,
            }}
          />
          <Tooltip title={`${timeLabel} · ${fmt(timeVal)}`}>
            <span style={{ color: "#9aa0b5", fontSize: 12 }}>{fmt(timeVal)}</span>
          </Tooltip>
          {/* 挂不挂告警的口径见 taskActions.showCredentialWarn */}
          {showCredentialWarn(r) && (
            <Tooltip title={credentialWarnText(r)}>
              <span
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 4,
                  padding: "2px 8px",
                  borderRadius: 10,
                  background: CREDENTIAL_STATUS.unconfigured.tint,
                  color: CREDENTIAL_STATUS.unconfigured.dot,
                  fontSize: 12,
                  fontWeight: 600,
                }}
              >
                <WarningOutlined />
                缺取数账号
              </span>
            </Tooltip>
          )}
        </div>
        <span onClick={stop}>
          <Dropdown menu={{ items: moreItems }} trigger={["click"]} placement="bottomRight">
            <Button
              type="text"
              size="small"
              icon={<MoreOutlined />}
              style={{ color: "#8a90a6" }}
            />
          </Dropdown>
        </span>
      </div>

      {/* 卡身:任务名 + 极简 engine 小标签。flex 列 + 标签行 marginTop:auto ⇒
          标签始终贴在卡身底部,描述有无、一行两行都不会让标签行上下漂移,
          同一行相邻卡片的标签落在同一水平线上。 */}
      <div style={{ flex: 1, display: "flex", flexDirection: "column", minWidth: 0 }}>
        <div
          title={r.name}
          style={{
            fontSize: 15,
            fontWeight: 700,
            color: "var(--ink)",
            lineHeight: "22px",
            marginBottom: 4,
            display: "-webkit-box",
            WebkitLineClamp: 2,
            WebkitBoxOrient: "vertical",
            overflow: "hidden",
          }}
        >
          {r.name}
        </div>
        {/* 任务说明:浅一层的次级信息,列表页扫一眼就知道这任务干什么;
            无说明的任务不占位,超一行即省略号、悬停看全文。
            单行而非两行:管理页一屏能多放一行卡片,说明本就是次级信息。
            title 用原生而非 Tooltip:卡片外层挂着「点击填参取数」的原生 title,
            子元素自带 title 才能盖住它 —— 换 Tooltip 会两个提示一起弹。 */}
        {r.description && (
          <div
            title={r.description}
            style={{
              fontSize: 13,
              color: "var(--ink-secondary)",
              lineHeight: "20px",
              overflow: "hidden",
              whiteSpace: "nowrap",
              textOverflow: "ellipsis",
            }}
          >
            {r.description}
          </div>
        )}
        {(r.datasource_name || r.team_name || r.subscribe_enabled) && (
          <div
            style={{
              display: "flex",
              flexWrap: "wrap",
              gap: 6,
              marginTop: "auto",
              paddingTop: 8,
            }}
          >
            {r.datasource_name && (
              <span style={chipStyle} title={r.datasource_name}>
                {r.datasource_name}
              </span>
            )}
            {/* 团队标签:跨团队互不可见之后,平台管理员(看全部)与多团队开发者都需要一眼分辨归属 */}
            {r.team_name && (
              <span style={chipStyle} title={`所属团队:${r.team_name}`}>
                {r.team_name}
              </span>
            )}
            {/* 订阅标签:计划描述由后端拼好(schedule_desc),前端不自己算频次语义。
                已订阅时换品牌色,一眼分清「任务可订」与「我订了」。 */}
            {r.subscribe_enabled && (
              <span
                style={{
                  ...chipStyle,
                  ...(r.subscribed
                    ? { background: "#e9e7fd", color: "var(--brand)", fontWeight: 600 }
                    : {}),
                }}
                title={scheduleHint(r)}
              >
                <ClockCircleOutlined style={{ marginRight: 4 }} />
                {scheduleLabel(r)}
              </span>
            )}
          </div>
        )}
      </div>

      {/* 卡脚:作者 + 参与者头像组 + 授权 */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginTop: 10,
          paddingTop: 8,
          borderTop: "1px solid #f0f2f8",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0 }}>
          <Avatar size={24} icon={<UserOutlined />} style={{ background: "#e9e7fd", color: "var(--brand)" }} />
          <span
            style={{
              fontSize: 13,
              color: "#4a4a4a",
              overflow: "hidden",
              whiteSpace: "nowrap",
              textOverflow: "ellipsis",
            }}
            title={r.author_name}
          >
            {r.author_name}
          </span>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 6 }} onClick={stop}>
          {users.length > 0 && <AuthorizedAvatars users={users} />}
          <GrantButton task={r} h={h} />
        </div>
      </div>
    </div>
  );
}
