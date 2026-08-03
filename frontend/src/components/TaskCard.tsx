import { Avatar, Button, Dropdown, Tooltip } from "antd";
import {
  MoreOutlined,
  PlusOutlined,
  UserOutlined,
} from "@ant-design/icons";
import { TEMPLATE_STATUS } from "./StatusTag";

const fmt = (t: string) => (t ? t.replace("T", " ").slice(0, 16) : "-");
const stop = (e: React.MouseEvent) => e.stopPropagation();

export type TaskCardHandlers = {
  onRun: (r: any) => void;
  onEdit: (r: any) => void;
  onGrant: (r: any) => void;
  onRecords: (r: any) => void;
  onPublish: (r: any) => void;
  onArchive: (r: any) => void;
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

  // ⋮ 菜单:运行记录(所有人)+ 管理项(仅 can_manage)
  const moreItems: any[] = [
    { key: "records", label: "运行记录", onClick: () => h.onRecords(r) },
  ];
  if (r.can_manage) {
    moreItems.push(
      { type: "divider" },
      { key: "edit", label: "编辑", onClick: () => h.onEdit(r) },
      r.status === "published"
        ? { key: "archive", label: "下线", danger: true, onClick: () => h.onArchive(r) }
        : { key: "publish", label: "上线", onClick: () => h.onPublish(r) }
    );
  }

  return (
    <div
      className={r.can_run ? "rk-lift" : undefined}
      onClick={() => r.can_run && h.onRun(r)}
      title={r.can_run ? "点击填参取数" : "未上线或未授权,暂不可取数"}
      style={{
        background: "#fff",
        border: "1px solid #edf0f7",
        borderRadius: 20,
        padding: 18,
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
          marginBottom: 12,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <Tooltip title={TEMPLATE_STATUS[r.status]?.label ?? r.status}>
            <span
              style={{
                width: 9,
                height: 9,
                borderRadius: "50%",
                background: TEMPLATE_STATUS[r.status]?.dot ?? "#bfbfbf",
                display: "inline-block",
              }}
            />
          </Tooltip>
          <Tooltip title={`${timeLabel} · ${fmt(timeVal)}`}>
            <span style={{ color: "#9aa0b5", fontSize: 12 }}>{fmt(timeVal)}</span>
          </Tooltip>
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

      {/* 卡身:任务名 + 极简 engine 小标签 */}
      <div style={{ flex: 1 }}>
        <div
          style={{
            fontSize: 16,
            fontWeight: 700,
            color: "var(--ink)",
            lineHeight: "23px",
            marginBottom: 8,
            display: "-webkit-box",
            WebkitLineClamp: 2,
            WebkitBoxOrient: "vertical",
            overflow: "hidden",
            minHeight: 46,
          }}
        >
          {r.name}
        </div>
        {r.datasource_name && (
          <span
            style={{
              display: "inline-block",
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
              verticalAlign: "bottom",
            }}
            title={r.datasource_name}
          >
            {r.datasource_name}
          </span>
        )}
      </div>

      {/* 卡脚:作者 + 参与者头像组 + 授权 */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginTop: 16,
          paddingTop: 12,
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
