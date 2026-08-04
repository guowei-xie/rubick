import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Button, Card, Empty, Modal, Space, Tooltip } from "antd";
import { DeleteOutlined, PlusOutlined } from "@ant-design/icons";
import { archiveTemplate, listTasks, publishTemplate } from "../api";
import { useAuth } from "../auth";
import TaskEditor from "../components/TaskEditor";
import RunDrawer from "../components/RunDrawer";
import RunRecordsDrawer from "../components/RunRecordsDrawer";
import GrantModal from "../components/GrantModal";
import TaskCard, { TaskCardHandlers } from "../components/TaskCard";
import { TEMPLATE_STATUS } from "../components/StatusTag";

/** 顶部可点击的状态筛选小片:点击切换只看该状态,再点或点「总数」清除。 */
function StatChip({
  n,
  label,
  active,
  activeBg,
  onClick,
}: {
  n: number;
  label: string;
  active: boolean;
  activeBg: string;
  onClick: () => void;
}) {
  return (
    <span
      onClick={onClick}
      style={{
        cursor: "pointer",
        padding: "3px 12px",
        borderRadius: 12,
        background: active ? activeBg : "transparent",
        color: active ? "var(--ink)" : "var(--ink-secondary)",
        fontWeight: active ? 600 : 400,
        transition: ".15s",
        userSelect: "none",
      }}
    >
      <b style={{ color: "var(--ink)" }}>{n}</b> {label}
    </span>
  );
}

export default function TasksPage() {
  const { user } = useAuth();
  const canCreate = user?.role === "admin" || user?.role === "developer";

  const [tasks, setTasks] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [editorId, setEditorId] = useState<number | null | undefined>(undefined); // undefined=关闭
  const [runTarget, setRunTarget] = useState<any>(null);
  const [grantTarget, setGrantTarget] = useState<any>(null);
  const [recordsTarget, setRecordsTarget] = useState<any>(null);
  const [sp, setSp] = useSearchParams();
  // 状态筛选与搜索词一样走 URL(?status=),刷新/深链可保留,与 ?q= 同一套来源
  const statusFilter = sp.get("status"); // null=全部
  const setStatusFilter = (s: string | null) => {
    if (s) sp.set("status", s);
    else sp.delete("status");
    setSp(sp, { replace: true });
  };
  // 回收站视图:同页切换(?recycle=1),只看已下线任务;与状态筛选互斥
  const showRecycle = sp.get("recycle") === "1";
  const toggleRecycle = () => {
    if (showRecycle) sp.delete("recycle");
    else {
      sp.set("recycle", "1");
      sp.delete("status");
    }
    setSp(sp, { replace: true });
  };

  const load = () => {
    setLoading(true);
    listTasks()
      .then(setTasks)
      .finally(() => setLoading(false));
  };
  useEffect(load, []);

  // 从通知深链进来(/tasks?records=<taskId>):任务加载后打开对应运行记录抽屉,并清掉参数
  useEffect(() => {
    const rid = sp.get("records");
    if (!rid || !tasks.length) return;
    const t = tasks.find((x) => String(x.id) === rid);
    if (t) setRecordsTarget(t);
    sp.delete("records");
    setSp(sp, { replace: true });
  }, [tasks]);

  const doArchive = (row: any) =>
    Modal.confirm({
      title: `下线任务「${row.name}」?`,
      content: "下线后业务用户将不能再运行该任务。",
      onOk: () => archiveTemplate(row.id).then(load),
    });

  const doPublish = (row: any) => {
    const restore = row.status === "archived"; // 回收站里的下线任务:恢复=重新上线
    Modal.confirm({
      title: `${restore ? "重新上线" : "上线"}任务「${row.name}」?`,
      content: `${restore ? "重新上线" : "上线"}后,被授权的业务用户即可运行该任务的最新版本。`,
      okText: restore ? "重新上线" : "上线",
      onOk: () => publishTemplate(row.id, restore ? "回收站重新上线" : "任务列表上线").then(load),
    });
  };

  // 顶栏搜索:按 任务名 / 作者 / 被授权人 客户端过滤(大小写不敏感)。
  // 默认视图排除下线(archived)任务;回收站视图则只看下线任务。
  const q = (sp.get("q") ?? "").trim().toLowerCase();
  const filtered = useMemo(() => {
    const matchQ = (t: any) => {
      if (!q) return true;
      if ((t.name || "").toLowerCase().includes(q)) return true;
      if ((t.author_name || "").toLowerCase().includes(q)) return true;
      return (t.authorized_users || []).some((u: any) => (u.name || "").toLowerCase().includes(q));
    };
    return tasks.filter((t) =>
      showRecycle
        ? t.status === "archived" && matchQ(t)
        : t.status !== "archived" && matchQ(t) && (!statusFilter || t.status === statusFilter)
    );
  }, [tasks, q, statusFilter, showRecycle]);

  const summary = useMemo(() => {
    const s = { published: 0, draft: 0, archived: 0, total: 0 };
    for (const t of tasks) {
      if (t.status === "archived") {
        s.archived++; // 下线任务只进回收站,不计入本页统计
        continue;
      }
      s.total++;
      if (t.status === "published") s.published++;
      else if (t.status === "draft") s.draft++;
    }
    return s;
  }, [tasks]);

  // 顶部筛选片:已上线/待上线读共享状态色(tint),「总数」清除筛选
  const statChips: { key: string | null; label: string; n: number; tint: string }[] = [
    { key: "published", label: "已上线", n: summary.published, tint: TEMPLATE_STATUS.published.tint! },
    { key: "draft", label: "草稿", n: summary.draft, tint: TEMPLATE_STATUS.draft.tint! },
    { key: null, label: "总数", n: summary.total, tint: "#eef0f7" },
  ];

  const handlers: TaskCardHandlers = {
    onRun: setRunTarget,
    onEdit: (r) => setEditorId(r.id),
    onGrant: setGrantTarget,
    onRecords: setRecordsTarget,
    onPublish: doPublish,
    onArchive: doArchive,
  };

  return (
    <Card
      styles={{ body: { paddingTop: 12 } }}
      style={{ borderRadius: 28, minHeight: "calc(100vh - 120px)" }}
      loading={loading && !tasks.length}
      title={
        <Space size={20} align="center">
          <span style={{ fontSize: 22, fontWeight: 700 }}>{showRecycle ? "回收站" : "任务列表"}</span>
          {showRecycle ? (
            <span style={{ fontSize: 13, color: "var(--ink-secondary)" }}>
              <b style={{ color: "var(--ink)" }}>{summary.archived}</b> 个已下线任务
            </span>
          ) : (
            <Space size={8} style={{ fontSize: 13, fontWeight: 400 }}>
              {statChips.map((c) => (
                <StatChip
                  key={c.label}
                  n={c.n}
                  label={c.label}
                  active={statusFilter === c.key}
                  activeBg={c.tint}
                  onClick={() => setStatusFilter(statusFilter === c.key ? null : c.key)}
                />
              ))}
            </Space>
          )}
        </Space>
      }
      extra={
        canCreate && (
          <Space size={8}>
            <Tooltip title={showRecycle ? "返回任务列表" : "回收站(已下线任务)"}>
              <Button
                shape="circle"
                icon={<DeleteOutlined />}
                type={showRecycle ? "primary" : "default"}
                onClick={toggleRecycle}
                aria-label="回收站"
              />
            </Tooltip>
            {!showRecycle && (
              <Button type="primary" icon={<PlusOutlined />} onClick={() => setEditorId(null)}>
                新建任务
              </Button>
            )}
          </Space>
        )
      }
    >
      {filtered.length === 0 ? (
        <Empty
          style={{ padding: "48px 0" }}
          description={
            q
              ? `没有匹配「${sp.get("q")}」的任务`
              : showRecycle
                ? "回收站为空"
                : "暂无任务"
          }
        />
      ) : (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))",
            gap: 20,
          }}
        >
          {filtered.map((t) => (
            <TaskCard key={t.id} task={t} h={handlers} />
          ))}
        </div>
      )}

      <TaskEditor
        editingId={editorId ?? null}
        open={editorId !== undefined}
        onClose={() => setEditorId(undefined)}
        onSaved={load}
      />
      <RunDrawer task={runTarget} open={!!runTarget} onClose={() => setRunTarget(null)} />
      <RunRecordsDrawer task={recordsTarget} open={!!recordsTarget} onClose={() => setRecordsTarget(null)} />
      <GrantModal
        templateId={grantTarget?.id ?? null}
        templateName={grantTarget?.name}
        open={!!grantTarget}
        onClose={() => setGrantTarget(null)}
      />
    </Card>
  );
}
