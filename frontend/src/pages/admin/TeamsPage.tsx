import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  Alert,
  Avatar,
  Button,
  Card,
  Drawer,
  Form,
  Input,
  Modal,
  Popconfirm,
  Space,
  Table,
  Tag,
  Tooltip,
  message,
} from "antd";
import {
  Team,
  TeamDetail,
  TeamMember,
  addTeamMember,
  createTeam,
  deleteTeam,
  errMsg,
  getTeamDetail,
  grantTeamAdmin,
  listTeams,
  revokeTeamAdmin,
  updateTeam,
} from "../../api";
import StatusTag, { ROLE, TEAM_ROLE } from "../../components/StatusTag";
import TeamCandidateSelect from "../../components/TeamCandidateSelect";
import { fmtTime } from "../../format";

/**
 * 「团队管理」(平台管理员)—— 建团队、指定团队管理员、删团队。
 *
 * 团队账号的配置、成员的日常增删由**团队管理员**在团队页做;这一页只承载平台级治理动作。
 */
export default function AdminTeamsPage() {
  const [rows, setRows] = useState<Team[]>([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<Team | null>(null);
  const [drawer, setDrawer] = useState<TeamDetail | null>(null);
  const [form] = Form.useForm();

  const load = () => {
    setLoading(true);
    return listTeams()
      .then(setRows)
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    load();
  }, []);

  const openCreate = () => {
    form.resetFields();
    setEditing(null);
    setCreating(true);
    // 首批团队管理员刻意不在建团队时选:候选接口是按团队查的(要排除已在团队的人),
    // 而团队此刻还没有 id。建完再到「成员」抽屉里指定,只多一步且语义更清楚。
  };

  const openEdit = (t: Team) => {
    form.resetFields();
    form.setFieldsValue({ name: t.name, description: t.description });
    setCreating(false);
    setEditing(t);
  };

  const submit = async () => {
    const v = await form.validateFields();
    try {
      if (editing) {
        await updateTeam(editing.id, { name: v.name, description: v.description });
        message.success("已保存");
      } else {
        await createTeam({ name: v.name, description: v.description });
        message.success("已创建团队,接下来请在「成员」里指定团队管理员");
      }
      setCreating(false);
      setEditing(null);
      load();
    } catch (e: any) {
      message.error(errMsg(e, "保存失败"));
    }
  };

  const remove = async (t: Team) => {
    try {
      await deleteTeam(t.id);
      message.success("已删除团队");
      load();
    } catch (e: any) {
      message.error(errMsg(e, "删除失败"));
    }
  };

  const openDrawer = (t: Team) => getTeamDetail(t.id).then(setDrawer);

  const columns = [
    {
      title: "团队",
      render: (_: any, t: Team) => (
        <Space direction="vertical" size={0}>
          <Link to={`/teams/${t.id}`}>{t.name}</Link>
          {t.description && (
            <span style={{ color: "#8c8c8c", fontSize: 12 }}>{t.description}</span>
          )}
        </Space>
      ),
    },
    {
      title: "团队管理员",
      render: (_: any, t: Team) =>
        t.admins.length ? (
          <Space size={4} wrap>
            {t.admins.map((a) => (
              <Tag key={a.user_id} color={TEAM_ROLE.team_admin.color}>
                {a.name}
              </Tag>
            ))}
          </Space>
        ) : (
          // 允许清零,但要显眼:没有团队管理员就没人能配团队账号、也没人能授编辑权
          <Tooltip title="没有团队管理员时,团队账号与任务编辑权都只能由平台管理员代为处理">
            <Tag color="orange">无团队管理员</Tag>
          </Tooltip>
        ),
    },
    { title: "成员数", width: 90, dataIndex: "member_count" },
    {
      title: "任务数",
      width: 90,
      render: (_: any, t: Team) => (
        <Tooltip title="含回收站里已下线的任务;非零时无法删除团队">
          <span>{t.template_count}</span>
        </Tooltip>
      ),
    },
    { title: "创建时间", width: 180, render: (_: any, t: Team) => fmtTime(t.created_at) },
    {
      title: "操作",
      width: 230,
      render: (_: any, t: Team) => (
        <Space size={0}>
          <Button type="link" size="small" onClick={() => openDrawer(t)}>
            成员
          </Button>
          <Button type="link" size="small" onClick={() => openEdit(t)}>
            编辑
          </Button>
          {t.template_count > 0 ? (
            <Tooltip title={`该团队下还有 ${t.template_count} 个任务(含回收站),请先转移后再删除`}>
              <Button type="link" size="small" danger disabled>
                删除
              </Button>
            </Tooltip>
          ) : (
            <Popconfirm
              title={`删除团队「${t.name}」?`}
              description="将同时清除其成员关系与团队取数账号。"
              okText="删除"
              okButtonProps={{ danger: true }}
              cancelText="取消"
              onConfirm={() => remove(t)}
            >
              <Button type="link" size="small" danger>
                删除
              </Button>
            </Popconfirm>
          )}
        </Space>
      ),
    },
  ];

  return (
    <Card
      title="团队管理"
      extra={
        <Button type="primary" onClick={openCreate}>
          新建团队
        </Button>
      }
      style={{ borderRadius: 28, minHeight: "calc(100vh - 120px)" }}
    >
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message="团队是任务的归属边界,也是取数身份的边界"
        description={
          <>
            开发者必须先属于某个团队才能新建任务；任务归属团队，
            <b>同团队成员互相可见</b>，但默认只能编辑自己建的（团队管理员可按任务授予编辑权）。
            取数时用的是<b>任务所属团队</b>的数据库账号，由团队管理员在团队页配置。
            <br />
            平台管理员不受团队约束：可见并可编辑全部团队的任务。团队成员只能是「开发者」角色。
          </>
        }
      />
      <Table rowKey="id" loading={loading} dataSource={rows} columns={columns} />

      <Modal
        title={editing ? `编辑团队:${editing.name}` : "新建团队"}
        open={creating || !!editing}
        onCancel={() => {
          setCreating(false);
          setEditing(null);
        }}
        onOk={submit}
        okText="保存"
      >
        <Form form={form} layout="vertical">
          <Form.Item
            name="name"
            label="团队名称"
            rules={[{ required: true, message: "请填写团队名称" }]}
          >
            <Input placeholder="如:数据平台组" />
          </Form.Item>
          <Form.Item name="description" label="说明">
            <Input.TextArea rows={2} placeholder="这个团队负责什么(可选)" />
          </Form.Item>
        </Form>
      </Modal>

      <MembersDrawer
        team={drawer}
        onClose={() => setDrawer(null)}
        onChanged={async () => {
          if (drawer) setDrawer(await getTeamDetail(drawer.id));
          load();
        }}
      />
    </Card>
  );
}

/** 成员抽屉:平台管理员在这里指定/取消团队管理员(需求 1),并可代为加人。 */
function MembersDrawer({
  team,
  onClose,
  onChanged,
}: {
  team: TeamDetail | null;
  onClose: () => void;
  onChanged: () => void;
}) {
  const [picked, setPicked] = useState<number>();
  const [busy, setBusy] = useState(false);
  // 增删成员/改团队管理员后 +1,让候选下拉重取
  const [candidateKey, setCandidateKey] = useState(0);

  useEffect(() => {
    setPicked(undefined);
  }, [team?.id]);

  if (!team) return null;

  const act = async (fn: () => Promise<any>, ok: string) => {
    setBusy(true);
    try {
      await fn();
      message.success(ok);
      onChanged();
      setCandidateKey((k) => k + 1);
    } catch (e: any) {
      message.error(errMsg(e, "操作失败"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Drawer title={`团队成员:${team.name}`} open onClose={onClose} width={620}>
      <Space direction="vertical" style={{ width: "100%" }} size="middle">
        <Alert
          type="warning"
          showIcon
          message="加入团队 = 授予该团队取数账号的全部数据权限"
          description="团队账号是共享的：成员能在任务编辑器里用它试跑任意 SQL。成员只能是「开发者」角色。"
        />
        <Space wrap>
          <TeamCandidateSelect
            teamId={team.id}
            value={picked}
            onChange={setPicked}
            reloadKey={candidateKey}
            placeholder="选择开发者加入"
          />
          <Button
            type="primary"
            loading={busy}
            onClick={() => {
              if (!picked) return message.warning("请选择开发者");
              act(() => addTeamMember(team.id, { user_id: picked }), "已加入团队");
              setPicked(undefined);
            }}
          >
            添加
          </Button>
        </Space>
        <Table
          rowKey="user_id"
          size="small"
          pagination={false}
          dataSource={team.members}
          locale={{ emptyText: "还没有成员" }}
          columns={[
            {
              title: "成员",
              render: (_: any, m: TeamMember) => (
                <Space>
                  <Avatar size={22} src={m.avatar || undefined}>
                    {(m.name || "?").slice(0, 1)}
                  </Avatar>
                  {m.name}
                </Space>
              ),
            },
            {
              title: "平台角色",
              width: 100,
              render: (_: any, m: TeamMember) => <StatusTag map={ROLE} value={m.role} />,
            },
            {
              title: "团队角色",
              width: 190,
              render: (_: any, m: TeamMember) =>
                m.is_team_admin ? (
                  <Space size={4}>
                    <Tag color={TEAM_ROLE.team_admin.color}>{TEAM_ROLE.team_admin.label}</Tag>
                    <Button
                      type="link"
                      size="small"
                      loading={busy}
                      onClick={() => act(() => revokeTeamAdmin(team.id, m.user_id), "已取消")}
                    >
                      取消
                    </Button>
                  </Space>
                ) : (
                  <Button
                    type="link"
                    size="small"
                    loading={busy}
                    onClick={() => act(() => grantTeamAdmin(team.id, m.user_id), "已设为团队管理员")}
                  >
                    设为团队管理员
                  </Button>
                ),
            },
          ]}
        />
      </Space>
    </Drawer>
  );
}
