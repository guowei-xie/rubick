import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Card, Empty, Modal, Space } from "antd";
import { listTasks, publishTemplate } from "../api";
import TaskEditor from "../components/TaskEditor";
import RunRecordsDrawer from "../components/RunRecordsDrawer";
import GrantModal from "../components/GrantModal";
import TaskCard, { TaskCardHandlers } from "../components/TaskCard";

/** 回收站:统一收纳「已下线(archived)」任务,可查看运行记录并「重新上线」恢复。 */
export default function RecyclePage() {
  const [tasks, setTasks] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [editorId, setEditorId] = useState<number | null | undefined>(undefined); // undefined=关闭
  const [grantTarget, setGrantTarget] = useState<any>(null);
  const [recordsTarget, setRecordsTarget] = useState<any>(null);
  const [sp] = useSearchParams();

  const load = () => {
    setLoading(true);
    listTasks()
      .then(setTasks)
      .finally(() => setLoading(false));
  };
  useEffect(load, []);

  // 重新上线:复用现有上线接口,archived 任务的版本仍在,发布最新版本即恢复为已上线
  const doPublish = (row: any) =>
    Modal.confirm({
      title: `重新上线任务「${row.name}」?`,
      content: "上线后,被授权的业务用户即可运行该任务的最新版本。",
      okText: "上线",
      onOk: () => publishTemplate(row.id, "回收站重新上线").then(load),
    });

  // 顶栏搜索:按 任务名 / 作者 / 被授权人 客户端过滤(大小写不敏感);仅展示已下线任务
  const q = (sp.get("q") ?? "").trim().toLowerCase();
  const filtered = useMemo(() => {
    const matchQ = (t: any) => {
      if (!q) return true;
      if ((t.name || "").toLowerCase().includes(q)) return true;
      if ((t.author_name || "").toLowerCase().includes(q)) return true;
      return (t.authorized_users || []).some((u: any) => (u.name || "").toLowerCase().includes(q));
    };
    return tasks.filter((t) => t.status === "archived" && matchQ(t));
  }, [tasks, q]);

  const handlers: TaskCardHandlers = {
    onRun: () => {}, // 下线任务不可运行
    onEdit: (r) => setEditorId(r.id),
    onGrant: setGrantTarget,
    onRecords: setRecordsTarget,
    onPublish: doPublish,
    onArchive: () => {}, // 已在回收站,无「下线」动作
  };

  return (
    <Card
      styles={{ body: { paddingTop: 12 } }}
      style={{ borderRadius: 28, minHeight: "calc(100vh - 120px)" }}
      loading={loading && !tasks.length}
      title={
        <Space size={20} align="center">
          <span style={{ fontSize: 22, fontWeight: 700 }}>回收站</span>
          <span style={{ fontSize: 13, color: "var(--ink-secondary)" }}>
            <b style={{ color: "var(--ink)" }}>{filtered.length}</b> 个已下线任务
          </span>
        </Space>
      }
    >
      {filtered.length === 0 ? (
        <Empty
          style={{ padding: "48px 0" }}
          description={q ? `没有匹配「${sp.get("q")}」的下线任务` : "暂无下线任务"}
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
