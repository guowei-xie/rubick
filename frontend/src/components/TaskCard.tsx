import { Avatar, Button, Dropdown, Tooltip } from "antd";
import {
  ClockCircleOutlined,
  MoreOutlined,
  PlusOutlined,
  UserOutlined,
  WarningOutlined,
} from "@ant-design/icons";
import { CREDENTIAL_STATUS, TEMPLATE_STATUS } from "./StatusTag";
import { fmtTime } from "../format";

const fmt = (t: string) => fmtTime(t, false);
const stop = (e: React.MouseEvent) => e.stopPropagation();

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

export type TaskCardHandlers = {
  onRun: (r: any) => void;
  onEdit: (r: any) => void;
  onGrant: (r: any) => void;
  onRecords: (r: any) => void;
  onPublish: (r: any) => void;
  onArchive: (r: any) => void;
  onSubscribeToggle: (r: any) => void; // 订阅/退订(按 r.subscribed 二态)
  onSubscribers: (r: any) => void; // 订阅者名单与订阅记录(can_manage)
};

export default function TaskCard({
  task: r,
  h,
}: {
  task: any;
  h: TaskCardHandlers;
}) {
  const users: any[] = r.authorized_users || [];
  // 卡头时间:最后运行 → 最后编辑 → 创建
  const timeVal = r.last_run_at || r.updated_at || r.created_at;
  const timeLabel = r.last_run_at ? "最后运行" : r.updated_at ? "最后编辑" : "创建于";

  const meta = TEMPLATE_STATUS[r.status];

  // ⋮ 菜单:运行记录(所有人)+ 管理项(仅 can_manage)
  // 动作动词三分:已上线→下线、已下线→重新上线、草稿→上线
  const moreItems: any[] = [
    { key: "records", label: "运行记录", onClick: () => h.onRecords(r) },
  ];
  // 订阅/退订:已订阅的人永远能退订(哪怕任务已下线/权限被撤);未订阅的按服务端
  // 算好的 can_subscribe 显示 —— 前端不自己算资格规则
  if (r.subscribed) {
    moreItems.push({ key: "unsubscribe", label: "退订", onClick: () => h.onSubscribeToggle(r) });
  } else if (r.can_subscribe) {
    moreItems.push({ key: "subscribe", label: "订阅本任务", onClick: () => h.onSubscribeToggle(r) });
  }
  if (r.can_manage) {
    moreItems.push(
      { type: "divider" },
      { key: "edit", label: "编辑", onClick: () => h.onEdit(r) },
      r.status === "published"
        ? { key: "archive", label: "下线", danger: true, onClick: () => h.onArchive(r) }
        : {
            key: "publish",
            label: r.status === "archived" ? "重新上线" : "上线",
            onClick: () => h.onPublish(r),
          }
    );
    if (r.subscribe_enabled) {
      moreItems.push({
        key: "subscribers",
        label: "订阅者名单",
        onClick: () => h.onSubscribers(r),
      });
    }
  }

  return (
    <div
      className={r.can_run ? "rk-lift" : undefined}
      onClick={() => r.can_run && h.onRun(r)}
      title={
        r.can_run
          ? "点击填参取数"
          : r.status !== "published"
            ? "任务未上线,暂不可取数"
            : "未授权,暂不可取数"
      }
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
          {/* 所属团队压根没登记该数据源的取数账号 ⇒ 这任务跑不动。只给管得着的人看:
              业务用户看一堆自己修不了的红字只会造成困扰(他们点运行时会拿到指名团队的报错)。
              「已配置但没点过测试连接」不在此列 —— 那种账号照样能跑,不该挂告警。 */}
          {r.can_manage && r.credential_ready === false && (
            <Tooltip
              title={`团队${r.team_name ? `《${r.team_name}》` : ""}尚未登记该数据源的取数账号 —— 该任务当前无法运行,请联系团队管理员`}
            >
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
        {/* 用 span 包住 Dropdown 并 stopPropagation:菜单项虽 DOM 上在 portal,但在 React 树里仍是本卡子节点,
            合成事件会冒泡到卡片 onClick(取数),这里拦在卡片之前。 */}
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
                title={
                  `定时运行:${r.schedule_desc || ""} · ${r.subscriber_count || 0} 人订阅` +
                  (r.subscribed ? "(含你)" : "")
                }
              >
                <ClockCircleOutlined style={{ marginRight: 4 }} />
                {r.subscribed ? "已订阅" : r.schedule_desc || "可订阅"}
                {r.subscriber_count > 0 ? ` · ${r.subscriber_count}` : ""}
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
          {users.length > 0 && (
            <Avatar.Group max={{ count: 3, style: { background: "#8a90a6", fontSize: 12 } }} size={24}>
              {users.map((u) => (
                <Tooltip key={u.id} title={u.name}>
                  <Avatar size={24} src={u.avatar || undefined}>
                    {(u.name || "?").slice(0, 1)}
                  </Avatar>
                </Tooltip>
              ))}
            </Avatar.Group>
          )}
          {r.can_manage && (
            <Tooltip title="授权用户">
              <Button
                shape="circle"
                size="small"
                icon={<PlusOutlined />}
                onClick={() => h.onGrant(r)}
                style={{ borderStyle: "dashed", color: "#8a90a6" }}
              />
            </Tooltip>
          )}
        </div>
      </div>
    </div>
  );
}
