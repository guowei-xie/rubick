import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Button, Card, Modal, Space, Table, Tag, message } from "antd";
import { archiveTemplate, errMsg, listTasks, publishTemplate } from "../api";
import { useAuth } from "../auth";
import StatusTag, { TEMPLATE_STATUS } from "../components/StatusTag";
import TaskEditor from "../components/TaskEditor";
import RunDrawer from "../components/RunDrawer";
import RunRecordsDrawer from "../components/RunRecordsDrawer";
import GrantModal from "../components/GrantModal";

export default function TasksPage() {
  const { user } = useAuth();
  const canCreate = user?.role === "admin";

  const [tasks, setTasks] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [editorId, setEditorId] = useState<number | null | undefined>(undefined); // undefined=关闭
  const [runTarget, setRunTarget] = useState<any>(null);
  const [grantTarget, setGrantTarget] = useState<any>(null);
  const [recordsTarget, setRecordsTarget] = useState<any>(null);
  const [sp, setSp] = useSearchParams();

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

  const doPublish = (row: any) =>
    Modal.confirm({
      title: `发布任务「${row.name}」?`,
      content: "发布 = 验收通过并对被授权的业务用户可运行(发布最新版本)。",
      onOk: async () => {
        try {
          await publishTemplate(row.id, "任务列表发布");
          message.success("已发布");
          load();
        } catch (e: any) {
          message.error(errMsg(e, "发布失败"));
        }
      },
    });

  const doArchive = (row: any) =>
    Modal.confirm({
      title: `下线任务「${row.name}」?`,
      content: "下线后业务用户将不能再运行该任务。",
      onOk: () => archiveTemplate(row.id).then(load),
    });

  const columns = [
    {
      title: "项目名称",
      dataIndex: "name",
      width: 240,
      ellipsis: true,
      render: (n: string, r: any) => (
        <span>
          <strong>{n}</strong>
          {r.domain && <Tag style={{ marginLeft: 6 }}>{r.domain}</Tag>}
        </span>
      ),
    },
    { title: "创建人", dataIndex: "author_name", width: 90 },
    { title: "创建时间", dataIndex: "created_at", width: 160, render: (t: string) => (t ? t.replace("T", " ").slice(0, 19) : "-") },
    {
      title: "数据库",
      width: 190,
      render: (_: any, r: any) => (
        <span>
          <Tag color={r.engine === "hive" ? "orange" : "green"}>{r.engine}</Tag>
          {r.datasource_name}
        </span>
      ),
    },
    {
      title: "状态",
      dataIndex: "status",
      width: 90,
      render: (s: string) => <StatusTag map={TEMPLATE_STATUS} value={s} />,
    },
    {
      title: "操作",
      width: 100,
      render: (_: any, r: any) =>
        r.can_run ? (
          <Button type="link" onClick={() => setRunTarget(r)}>填参取数</Button>
        ) : (
          <span style={{ color: "#ccc" }}>—</span>
        ),
    },
    {
      title: "管理",
      width: 200,
      render: (_: any, r: any) =>
        r.can_manage ? (
          <Space size={0} wrap>
            <Button type="link" size="small" onClick={() => setEditorId(r.id)}>代码编辑</Button>
            <Button type="link" size="small" onClick={() => setGrantTarget(r)}>授权</Button>
            {r.status === "published" ? (
              <Button type="link" size="small" danger onClick={() => doArchive(r)}>下线</Button>
            ) : (
              <Button type="link" size="small" onClick={() => doPublish(r)}>发布</Button>
            )}
          </Space>
        ) : (
          <span style={{ color: "#ccc" }}>—</span>
        ),
    },
    {
      title: "运行记录",
      width: 90,
      render: (_: any, r: any) => (
        <Button type="link" size="small" onClick={() => setRecordsTarget(r)}>查看</Button>
      ),
    },
  ];

  return (
    <Card
      title="任务列表"
      extra={
        canCreate && (
          <Button type="primary" onClick={() => setEditorId(null)}>
            新建任务
          </Button>
        )
      }
    >
      <Table
        rowKey="id"
        loading={loading}
        dataSource={tasks}
        columns={columns}
        size="middle"
        scroll={{ x: 1100 }}
        pagination={{ pageSize: 15, showTotal: (t) => `共 ${t} 个任务` }}
      />

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
