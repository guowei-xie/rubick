import { Avatar, Button, Dropdown, Tooltip } from "antd";
import { ClockCircleOutlined, MoreOutlined, UserOutlined, WarningOutlined } from "@ant-design/icons";
import { CREDENTIAL_STATUS, TEMPLATE_STATUS } from "./StatusTag";
import {
  AuthorizedAvatars,
  credentialWarnText,
  GrantButton,
  IDLE_TEXT,
  runHint,
  scheduleHint,
  scheduleLabel,
  showCredentialWarn,
  stop,
  TaskHandlers,
  taskMenuItems,
  taskTimeCell,
} from "./taskActions";

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
  // 卡头那一格:正常是时间(最后运行 → 最后编辑 → 创建),闲置时换成「闲置 167 天」。
  // 文案与判断都在 taskActions,与列表视图共用一份
  const timeCell = taskTimeCell(r);
  // 长期没人跑的已上线任务。给谁看的策略见 taskActions.showIdle(只给 can_manage 的人);
  // 排到列表末尾在 TasksPage 的 filtered 里做,这里只管这张卡自己怎么长
  const idle = timeCell.idle;

  const meta = TEMPLATE_STATUS[r.status];

  // ⋮ 菜单与列表视图共用一份构造,见 taskActions.taskMenuItems
  const moreItems = taskMenuItems(r, h);

  return (
    <div
      className={r.can_run ? "rk-lift" : undefined}
      onClick={() => r.can_run && h.onRun(r)}
      title={runHint(r)}
      style={{
        // 底色**不跟着变灰**:卡身的小标签(chipStyle)拿 var(--app-bg) 当自己的底,
        // 卡片底色一往灰里走,数据源 / 团队 / 订阅三枚标签就跟着消失了 —— 那不是降噪,是删信息
        background: "#fff",
        // 闲置**不用 opacity**:整卡半透明在本产品里已经专指 can_run=false「点不动」,
        // 两者还可能同时成立(一个闲置的已上线任务,对无权限的人同时是点不动的)。
        // 所以闲置走另外两根轴:深度(撤掉 --card-shadow 的浮起,这套界面的层级全靠它把白卡
        // 从 --app-bg 上托起来)与墨色(标题退到 --ink-secondary,见卡身)。两根轴与透明度
        // 互不干扰,叠在一起读出来仍是两句话:「点不动」+「没人用」。
        // 后退只发生在静止态 —— 鼠标移上去它照样浮起来(.rk-lift 仍只按 can_run 挂),
        // 这一条正是两种状态不会被读混的关键:闲置的卡片还是点得开取数的。
        // 影子撤掉后卡片边界会糊,边框往深里走半档把轮廓接回来(仍是实线 —— 虚线在本产品里
        // 是 GrantButton 的「可以添加」)。
        border: idle ? "1px solid #e4e8f3" : "1px solid #edf0f7",
        borderRadius: 16,
        padding: "14px 16px",
        height: "100%",
        display: "flex",
        flexDirection: "column",
        boxShadow: idle ? "none" : "var(--card-shadow)",
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
          <Tooltip title={timeCell.hint}>
            <span
              style={{
                color: "#9aa0b5",
                fontSize: 12,
                whiteSpace: "nowrap",
                ...(idle ? IDLE_TEXT : null),
              }}
            >
              {timeCell.text}
            </span>
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
            // 墨色是与「撤掉阴影」正交的第二根降噪轴:扫一排卡片时最先被读到的就是标题的
            // 字重与黑度,它退一档整张卡就往后站了,而对比度仍远高于可读阈值
            color: idle ? "var(--ink-secondary)" : "var(--ink)",
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
