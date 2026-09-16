import { useEffect, useState } from "react";
import { Alert, Avatar, Button, Modal, Select, Space, Table, Tag, Tooltip, message } from "antd";
import {
  TaskEditor,
  TeamDetail,
  errMsg,
  grantTaskEditor,
  listTasks,
  revokeTaskEditor,
  teamTaskEditors,
} from "../api";
import StatusTag, { EDITOR_SOURCE, EDITOR_SOURCE_HINT, TEMPLATE_STATUS } from "./StatusTag";
import TransferAuthorModal from "./TransferAuthorModal";

/**
 * 「任务编辑权」面板 —— 团队管理员按任务把编辑权授予团队成员。
 *
 * 刻意**不复用 GrantModal**:那个面向业务使用者(view/run/download,候选人来自飞书通讯录),
 * 这个面向团队成员(edit,候选人只能是本团队成员)。受众与候选集都不同,合成一个会让两边都变复杂;
 * 后端也是两套入口(/permissions vs /tasks/{id}/editors),各有独立的守卫与审计动作码。
 */
export default function TeamTaskEditorsPanel({
  team,
  canManage,
}: {
  team: TeamDetail;
  canManage: boolean;
}) {
  const [rows, setRows] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [editorsOf, setEditorsOf] = useState<Record<number, TaskEditor[]>>({});
  const [open, setOpen] = useState<any | null>(null);
  const [transferTarget, setTransferTarget] = useState<any | null>(null);

  const load = () => {
    setLoading(true);
    // 两个请求,与任务数无关:任务列表已按团队收窄(服务端),编辑人一次批量取回。
    // 早先是逐任务调 /tasks/{id}/editors —— 50 个任务就是 51 个往返,且每次授予/撤销都重来。
    return Promise.all([listTasks(), teamTaskEditors(team.id)])
      .then(([all, editors]: [any[], Record<string, TaskEditor[]>]) => {
        setRows(all.filter((t) => t.team_id === team.id));
        setEditorsOf(
          Object.fromEntries(Object.entries(editors).map(([k, v]) => [Number(k), v]))
        );
      })
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    load();
  }, [team.id]);

  const columns = [
    { title: "任务", dataIndex: "name" },
    {
      title: "状态",
      width: 110,
      render: (_: any, t: any) => <StatusTag map={TEMPLATE_STATUS} value={t.status} />,
    },
    { title: "作者", width: 120, dataIndex: "author_name" },
    {
      title: "可编辑的人",
      render: (_: any, t: any) => {
        const list = editorsOf[t.id] || [];
        return (
          <Space size={4} wrap>
            {list.map((e) => (
              <Tooltip key={e.user_id} title={EDITOR_SOURCE_HINT[e.source]}>
                <Tag color={EDITOR_SOURCE[e.source]?.color}>
                  <Space size={4}>
                    <Avatar size={16} src={e.avatar || undefined}>
                      {(e.name || "?").slice(0, 1)}
                    </Avatar>
                    {e.name}
                  </Space>
                </Tag>
              </Tooltip>
            ))}
            {list.length === 0 && <span style={{ color: "#bfbfbf" }}>—</span>}
          </Space>
        );
      },
    },
    ...(canManage
      ? [
          {
            title: "操作",
            width: 190,
            // 「管理」而非「授予」:撤销也在同一个弹窗里,叫「授予编辑权」会让人以为撤不了
            render: (_: any, t: any) => (
              <Space size={0}>
                <Button type="link" size="small" onClick={() => setOpen(t)}>
                  管理编辑权
                </Button>
                {/* 团队管理员在这一页做离职交接最顺手:作者与可编辑的人就并排在左边两列。
                    门仍是服务端下发的 can_transfer_author,不是本面板的 canManage */}
                {t.can_transfer_author && (
                  <Button type="link" size="small" onClick={() => setTransferTarget(t)}>
                    转移作者
                  </Button>
                )}
              </Space>
            ),
          },
        ]
      : []),
  ];

  return (
    <>
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message="同团队默认「可见但不可编辑」他人的任务"
        description={
          <>
            团队成员互相看得到彼此的任务，但默认只能编辑自己建的。需要让某人改某个任务时，
            由<b>团队管理员</b>在这里按任务授予编辑权，<b>撤销也在同一处</b>。
            <br />
            团队管理员本人对本团队<b>全部</b>任务天然可编辑，无需授权，也无从撤销；成员被移出团队后，
            已授予的编辑权自动失效并清除。
          </>
        }
      />
      <Table rowKey="id" loading={loading} dataSource={rows} columns={columns} />
      <EditorsModal
        task={open}
        team={team}
        editors={(open && editorsOf[open.id]) || []}
        onClose={() => setOpen(null)}
        onChanged={load}
      />
      <TransferAuthorModal
        task={transferTarget}
        onClose={() => setTransferTarget(null)}
        onDone={load}
      />
    </>
  );
}

function EditorsModal({
  task,
  team,
  editors,
  onClose,
  onChanged,
}: {
  task: any | null;
  team: TeamDetail;
  editors: TaskEditor[];
  onClose: () => void;
  onChanged: () => void;
}) {
  const [picked, setPicked] = useState<number>();
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setPicked(undefined);
  }, [task?.id]);

  if (!task) return null;

  // 候选人 = 本团队成员,且还没有编辑权(隐式的作者/团队管理员也排除:给他们授权是空操作)
  const already = new Set(editors.map((e) => e.user_id));
  const options = team.members
    .filter((m) => !already.has(m.user_id))
    .map((m) => ({ value: m.user_id, label: m.name }));

  const grant = async () => {
    if (!picked) return message.warning("请选择团队成员");
    setBusy(true);
    try {
      await grantTaskEditor(task.id, picked);
      message.success("已授予编辑权");
      onChanged();
      setPicked(undefined);
    } catch (e: any) {
      message.error(errMsg(e, "授予失败"));
    } finally {
      setBusy(false);
    }
  };

  const revoke = async (userId: number) => {
    setBusy(true);
    try {
      await revokeTaskEditor(task.id, userId);
      message.success("已撤销");
      onChanged();
    } catch (e: any) {
      message.error(errMsg(e, "撤销失败"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      title={`任务编辑权:${task.name}`}
      open
      onCancel={onClose}
      footer={<Button onClick={onClose}>关闭</Button>}
      width={560}
    >
      <Space direction="vertical" style={{ width: "100%" }} size="middle">
        <Space wrap>
          <Select
            showSearch
            optionFilterProp="label"
            placeholder="从本团队成员中选择"
            style={{ width: 260 }}
            value={picked}
            onChange={setPicked}
            options={options}
            notFoundContent="本团队没有可再授予的成员"
          />
          <Button type="primary" loading={busy} onClick={grant}>
            授予编辑权
          </Button>
        </Space>
        <Table
          rowKey="user_id"
          size="small"
          pagination={false}
          dataSource={editors}
          locale={{ emptyText: "暂无可编辑的人" }}
          columns={[
            { title: "成员", dataIndex: "name" },
            {
              title: "来源",
              width: 140,
              render: (_: any, e: TaskEditor) => (
                <StatusTag map={EDITOR_SOURCE} value={e.source} />
              ),
            },
            {
              title: "操作",
              width: 100,
              render: (_: any, e: TaskEditor) =>
                e.source === "granted" ? (
                  <Button
                    type="link"
                    size="small"
                    danger
                    loading={busy}
                    onClick={() => revoke(e.user_id)}
                  >
                    撤销
                  </Button>
                ) : (
                  // 作者与团队管理员的编辑权是身份的推论,不是授权行 —— 撤不了
                  <Tooltip title="这是身份带来的权限,不可撤销">
                    <span style={{ color: "#bfbfbf" }}>—</span>
                  </Tooltip>
                ),
            },
          ]}
        />
      </Space>
    </Modal>
  );
}
