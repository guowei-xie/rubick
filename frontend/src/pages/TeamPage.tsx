import { useEffect, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import {
  Alert,
  Avatar,
  Button,
  Card,
  Popconfirm,
  Result,
  Space,
  Spin,
  Table,
  Tabs,
  Tag,
  message,
} from "antd";
import {
  TeamDetail,
  TeamMember,
  addTeamMember,
  errMsg,
  getTeamDetail,
  removeTeamMember,
} from "../api";
import { isPlatformAdmin, isTeamAdminOf, useAuth } from "../auth";
import StatusTag, { ROLE, TEAM_ROLE } from "../components/StatusTag";
import TeamCandidateSelect from "../components/TeamCandidateSelect";
import TeamCredentialsPanel from "../components/TeamCredentialsPanel";
import TeamTaskEditorsPanel from "../components/TeamTaskEditorsPanel";
import { dash, fmtTime } from "../format";

/**
 * 团队页 —— 团队管理员的主战场,普通成员只读。
 *
 * 三个 Tab:成员 / 团队取数账号 / 任务编辑权。
 * Tab 走 URL(?tab=)而不是内部 state:与本仓库「视图状态全在 URL」的既有约定一致,
 * 也让「团队账号未就绪」的通知深链 ?tab=credentials 能直接落到位。
 */
export default function TeamPage() {
  const { teamId } = useParams();
  const id = Number(teamId);
  const { user } = useAuth();
  const [sp, setSp] = useSearchParams();
  const tab = sp.get("tab") || "members";

  const [team, setTeam] = useState<TeamDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [denied, setDenied] = useState<string | null>(null);

  const canManage = isTeamAdminOf(user, id);

  const load = () => {
    setLoading(true);
    return getTeamDetail(id)
      .then((t) => {
        setTeam(t);
        setDenied(null);
      })
      .catch((e) => setDenied(errMsg(e, "无权查看该团队")))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    if (Number.isFinite(id)) load();
  }, [id]);

  if (loading) return <Spin style={{ margin: 80 }} />;
  if (denied)
    return (
      <Card style={{ borderRadius: 28 }}>
        <Result status="403" title="无权查看该团队" subTitle={denied} />
      </Card>
    );
  if (!team) return null;

  return (
    <Card
      title={
        <Space>
          <span>团队:{team.name}</span>
          {canManage && <Tag color={TEAM_ROLE.team_admin.color}>你是团队管理员</Tag>}
        </Space>
      }
      extra={
        <Space size={16} style={{ color: "#8c8c8c", fontSize: 12 }}>
          <span>{team.member_count} 名成员</span>
          <span>{team.template_count} 个任务</span>
        </Space>
      }
      style={{ borderRadius: 28, minHeight: "calc(100vh - 120px)" }}
    >
      {team.description && (
        <div style={{ color: "#8c8c8c", marginBottom: 16 }}>{team.description}</div>
      )}
      <Tabs
        activeKey={tab}
        onChange={(k) => {
          const next = new URLSearchParams(sp);
          next.set("tab", k);
          setSp(next, { replace: true });
        }}
        items={[
          {
            key: "members",
            label: "成员",
            children: <MembersTab team={team} canManage={canManage} onChanged={load} />,
          },
          {
            key: "credentials",
            label: "团队取数账号",
            children: (
              <TeamCredentialsPanel teamId={team.id} teamName={team.name} canEdit={canManage} />
            ),
          },
          {
            key: "editors",
            label: "任务编辑权",
            children: <TeamTaskEditorsPanel team={team} canManage={canManage} />,
          },
        ]}
      />
    </Card>
  );
}

function MembersTab({
  team,
  canManage,
  onChanged,
}: {
  team: TeamDetail;
  canManage: boolean;
  onChanged: () => void;
}) {
  const { user } = useAuth();
  const [picked, setPicked] = useState<number>();
  const [adding, setAdding] = useState(false);
  // 增删成员后 +1,让候选下拉重取(刚加进去的人要从候选里消失)
  const [candidateKey, setCandidateKey] = useState(0);

  const add = async () => {
    if (!picked) return message.warning("请选择要添加的开发者");
    setAdding(true);
    try {
      await addTeamMember(team.id, { user_id: picked });
      message.success("已加入团队");
      setPicked(undefined);
      onChanged();
      setCandidateKey((k) => k + 1);
    } catch (e: any) {
      message.error(errMsg(e, "添加失败"));
    } finally {
      setAdding(false);
    }
  };

  const remove = async (m: TeamMember) => {
    try {
      const out = await removeTeamMember(team.id, m.user_id);
      const n = (out?.revoked_edit_template_ids || []).length;
      message.success(n ? `已移出团队,并撤销了 ${n} 个任务的编辑权` : "已移出团队");
      onChanged();
      setCandidateKey((k) => k + 1);
    } catch (e: any) {
      message.error(errMsg(e, "移出失败"));
    }
  };

  const columns = [
    {
      title: "成员",
      render: (_: any, m: TeamMember) => (
        <Space>
          <Avatar size={22} src={m.avatar || undefined}>
            {(m.name || "?").slice(0, 1)}
          </Avatar>
          <span>{m.name}</span>
          {m.user_id === user?.id && <Tag>我</Tag>}
        </Space>
      ),
    },
    {
      // 同名同事只靠姓名区分不开,而「在这个团队」= 「能读这个团队的数据」
      title: "邮箱",
      dataIndex: "email",
      ellipsis: true,
      render: dash,
    },
    {
      title: "平台角色",
      width: 120,
      render: (_: any, m: TeamMember) => <StatusTag map={ROLE} value={m.role} />,
    },
    {
      title: "团队角色",
      width: 130,
      render: (_: any, m: TeamMember) => (
        <StatusTag map={TEAM_ROLE} value={m.is_team_admin ? "team_admin" : "member"} />
      ),
    },
    { title: "加入时间", width: 180, render: (_: any, m: TeamMember) => fmtTime(m.joined_at) },
    ...(canManage
      ? [
          {
            title: "操作",
            width: 100,
            render: (_: any, m: TeamMember) => (
              <Popconfirm
                title={`把 ${m.name} 移出团队?`}
                description="他将不再看得到本团队的任务;他在本团队任务上的编辑权也会一并撤销。任务本身留在团队里,继续可运行。"
                okText="移出"
                okButtonProps={{ danger: true }}
                cancelText="取消"
                onConfirm={() => remove(m)}
              >
                <Button type="link" size="small" danger>
                  移出
                </Button>
              </Popconfirm>
            ),
          },
        ]
      : []),
  ];

  return (
    <>
      {/* 这条警示必须常显:加成员不是「让他看看任务」,而是一次实打实的数据授权 */}
      <Alert
        type="warning"
        showIcon
        style={{ marginBottom: 16 }}
        message="把人加入本团队 = 授予他本团队取数账号的全部数据权限"
        description="团队账号是共享的：任何团队成员都能在任务编辑器里用它试跑任意 SQL。因此团队账号能读到的数据，全体成员都能读到。成员只能是「开发者」角色。"
      />
      {team.admins.length === 0 && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 16 }}
          message="本团队没有团队管理员"
          description="没有团队管理员就没人能配置团队取数账号或授予任务编辑权。请联系平台管理员指定一位。"
        />
      )}
      {canManage && (
        <Space style={{ marginBottom: 16 }} wrap>
          <TeamCandidateSelect
            teamId={team.id}
            value={picked}
            onChange={setPicked}
            reloadKey={candidateKey}
          />
          <Button type="primary" loading={adding} onClick={add}>
            添加成员
          </Button>
        </Space>
      )}
      <Table rowKey="user_id" dataSource={team.members} columns={columns} pagination={false} />
      {isPlatformAdmin(user) && (
        <div style={{ color: "#8c8c8c", fontSize: 12, marginTop: 12 }}>
          指定或取消「团队管理员」在「团队管理」页操作。
        </div>
      )}
    </>
  );
}
