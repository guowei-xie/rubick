import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Alert, Button, Card, message, Popconfirm, Space, Table, Tag, Tooltip } from "antd";
import {
  CredentialOverview,
  TeamCredentialStatus,
  credentialOverview,
  deleteTeamCredential,
  errMsg,
} from "../../api";
import CredentialTag from "../../components/CredentialTag";

/**
 * 平台管理员的「取数账号」总览 —— **常态健康看板**。
 *
 * 这不再是「切开关前的体检表」:强制使用团队账号已是唯一路径,过渡开关已删除。
 * 下方「此刻缺账号的已上线任务」只收**压根没有账号**的两种情形:① 任务没有所属团队;
 * ② 所属团队在该数据源上的账号被回收/从未登记。
 * **未测通不在此列** —— 测试连接是可选的自检,没点过的账号照样能跑(见后端 credential_service);
 * 覆盖矩阵里仍会用「已配置 · 未验过」把它标出来,供你催团队自查。
 *
 * 本页只看状态、可回收,拿不到密码(接口就不回传)。
 */
export default function AdminCredentialsPage() {
  const [data, setData] = useState<CredentialOverview | null>(null);
  const [loading, setLoading] = useState(true);

  const load = () =>
    credentialOverview()
      .then(setData)
      .finally(() => setLoading(false));

  useEffect(() => {
    load();
  }, []);

  const revoke = async (teamId: number, dsId: number) => {
    try {
      await deleteTeamCredential(teamId, dsId);
      message.success("已回收");
      load();
    } catch (e: any) {
      message.error(errMsg(e, "回收失败"));
    }
  };

  const cell = (teamId: number, c: TeamCredentialStatus) => {
    if (!c.configured) return <CredentialTag c={c} />;
    return (
      <Space size={0}>
        <CredentialTag c={c} withUsername />
        <Popconfirm
          title="回收该团队取数账号?"
          description="回收后,该团队在这个数据源上的任务将无法运行。"
          okText="回收"
          okButtonProps={{ danger: true }}
          cancelText="取消"
          onConfirm={() => revoke(teamId, c.datasource_id)}
        >
          <Button type="link" size="small" danger>
            回收
          </Button>
        </Popconfirm>
      </Space>
    );
  };

  // 一行一个团队、一列一个数据源:横向是「这个团队在各库上的身份」,纵向是「这个库上有哪些团队」
  const matrixColumns = [
    {
      title: "团队",
      fixed: "left" as const,
      width: 220,
      render: (_: any, t: any) => (
        <Space direction="vertical" size={0}>
          <Link to={`/teams/${t.team_id}?tab=credentials`}>{t.team_name}</Link>
          <Space size={4} wrap style={{ fontSize: 12 }}>
            {t.admins.length ? (
              t.admins.map((a: any) => (
                <Tag key={a.user_id} color="purple">
                  {a.name}
                </Tag>
              ))
            ) : (
              <Tooltip title="没有团队管理员就没人能配这个团队的账号,请先去「团队管理」指定一位">
                <Tag color="orange">无团队管理员</Tag>
              </Tooltip>
            )}
          </Space>
        </Space>
      ),
    },
    ...(data?.datasources || []).map((ds) => ({
      title: (
        <Space size={4}>
          {ds.name}
          <Tag>{ds.engine}</Tag>
        </Space>
      ),
      key: ds.id,
      render: (_: any, t: any) => {
        const c = t.credentials.find((x: TeamCredentialStatus) => x.datasource_id === ds.id);
        return c ? cell(t.team_id, c) : null;
      },
    })),
  ];

  const notReadyColumns = [
    { title: "任务", dataIndex: "template_name" },
    {
      title: "团队",
      render: (_: any, r: any) =>
        r.team_id ? (
          <Link to={`/teams/${r.team_id}?tab=credentials`}>{r.team_name}</Link>
        ) : (
          <Tag color="red">无所属团队</Tag>
        ),
    },
    { title: "作者", dataIndex: "author_name", width: 120 },
    { title: "数据源", dataIndex: "datasource_name", width: 160 },
    {
      title: "原因",
      dataIndex: "reason",
      width: 120,
      render: (r: string) => <Tag color="red">{r}</Tag>,
    },
  ];

  const notReady = data?.not_ready_templates || [];

  return (
    <Card title="团队取数账号" style={{ borderRadius: 28, minHeight: "calc(100vh - 120px)" }}>
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message="任务一律用「所属团队」的数据库账号取数"
        description={
          <>
            团队<b>没有登记账号</b>时，该团队在该数据源上的任务不允许上线、也无法运行
            （不会静默回退到数据源的公共账号）。「测试连接」是团队管理员的<b>可选自检</b>，
            没点过的账号一样能跑。账号由各团队的<b>团队管理员</b>在团队页配置，
            密码加密存储，<b>任何人（含平台管理员）都看不到</b> —— 你在这里只能看到状态与库用户名，
            以及在权限调整时回收账号。
          </>
        }
      />

      <div style={{ fontWeight: 600, margin: "8px 0 12px" }}>配置覆盖情况</div>
      <Table
        rowKey="team_id"
        loading={loading}
        dataSource={data?.teams || []}
        columns={matrixColumns}
        pagination={false}
        scroll={{ x: "max-content" }}
        size="small"
        locale={{ emptyText: "还没有团队 —— 去「团队管理」新建一个" }}
      />

      <div style={{ fontWeight: 600, margin: "28px 0 12px" }}>
        此刻缺账号的已上线任务
        {notReady.length > 0 ? (
          <Tag color="red" style={{ marginLeft: 8 }}>
            {notReady.length}
          </Tag>
        ) : (
          <Tag color="green" style={{ marginLeft: 8 }}>
            全部就绪
          </Tag>
        )}
      </div>
      {notReady.length > 0 && (
        <Alert
          type="error"
          showIcon
          style={{ marginBottom: 12 }}
          message="这些已上线任务现在就跑不动 —— 业务同学点运行会失败"
          description="原因只有一种:所属团队在该数据源上压根没有取数账号(被回收,或从未登记),或任务没有所属团队。请点团队名去催配。"
        />
      )}
      <Table
        rowKey="template_id"
        loading={loading}
        dataSource={notReady}
        columns={notReadyColumns}
        pagination={false}
        size="small"
        locale={{ emptyText: "全部已上线任务的所属团队都登记了取数账号" }}
      />
    </Card>
  );
}
