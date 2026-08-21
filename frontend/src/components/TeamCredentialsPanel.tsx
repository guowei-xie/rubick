import { useEffect, useState } from "react";
import { Alert, Button, Form, Input, message, Modal, Popconfirm, Space, Table, Tag } from "antd";
import {
  TeamCredentialStatus,
  deleteTeamCredential,
  errMsg,
  listTeamCredentials,
  saveTeamCredential,
  connectOkMsg,
  testTeamCredential,
} from "../api";
import CredentialTag from "./CredentialTag";
import { dash, fmtTime } from "../format";

/**
 * 「团队取数账号」面板 —— 团队页的一个 Tab。
 *
 * 本团队的任务运行时用的就是这里登记的库账号,能取到什么由数据库决定。
 * 每个数据源一行(含尚未配置的),这样「还差哪个源没配」一眼可见。
 *
 * 「测试连接」是**自愿的自检**,不是配置流程里的必经一步:账号存下来就能上线、能取数。
 * 未测通只在状态列留一条弱提醒,不拦任何操作(见后端 services/credential_service)。
 *
 * canEdit=false(普通团队成员)时只读:后端连库用户名都不返回(半机密,见 api.ts),
 * 页面也不渲染任何写操作按钮。
 */
export default function TeamCredentialsPanel({
  teamId,
  teamName,
  canEdit,
}: {
  teamId: number;
  teamName: string;
  canEdit: boolean;
}) {
  const [rows, setRows] = useState<TeamCredentialStatus[]>([]);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState<TeamCredentialStatus | null>(null);
  const [saving, setSaving] = useState(false);
  const [testingId, setTestingId] = useState<number | null>(null);
  const [form] = Form.useForm();

  const load = () => {
    setLoading(true);
    return listTeamCredentials(teamId)
      .then(setRows)
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    load();
  }, [teamId]);

  const openEdit = (row: TeamCredentialStatus) => {
    form.resetFields();
    form.setFieldsValue({ username: row.username || "", password: "" });
    setEditing(row);
  };

  /** 接口回传的就是这一行的新状态,直接换掉它,不必重拉整表。 */
  const replaceRow = (updated: TeamCredentialStatus) =>
    setRows((rs) => rs.map((r) => (r.datasource_id === updated.datasource_id ? updated : r)));

  /** 返回是否保存成功 —— 「保存并测试」要据此决定还测不测。 */
  const doSave = async (v: { username: string; password?: string }): Promise<boolean> => {
    if (!editing) return false;
    setSaving(true);
    try {
      replaceRow(
        await saveTeamCredential(teamId, editing.datasource_id, {
          username: v.username,
          // 留空 = 保留原密码(与数据源编辑同一约定)
          password: v.password || undefined,
        })
      );
      message.success("已保存");
      setEditing(null);
      return true;
    } catch (e: any) {
      message.error(errMsg(e, "保存失败"));
      return false;
    } finally {
      setSaving(false);
    }
  };

  // 改动只会清空「已测通」这条自检痕迹,不影响任务能不能跑(测试连接非必选),
  // 故这里不再做二次确认 —— 从前那次确认是为了警告「全团队任务立刻停摆」,那个后果已经不存在了。
  const save = async () => {
    await doSave(await form.validateFields());
  };

  /** 「保存并测试连接」:测通不是上线的前置条件,但配的时候顺手验一下最省心,
   *  所以把它做成弹窗里的一个次要按钮 —— 想验的人一步到位,不想验的直接点「保存」。 */
  const saveAndTest = async () => {
    const row = editing;
    const v = await form.validateFields();
    if (row && (await doSave(v))) await test(row);
  };

  const test = async (row: TeamCredentialStatus) => {
    setTestingId(row.datasource_id);
    try {
      const r = await testTeamCredential(teamId, row.datasource_id);
      replaceRow(r);
      // 连通只是底线:把这个账号能访问的库一并说出来 —— 那才是配完账号最该确认的事
      message.success(connectOkMsg(r.databases), 8);
    } catch (e: any) {
      // 失败时后端也写了状态(清空测通 + 记原因),重拉把原因带出来
      message.error(errMsg(e, "连接失败"));
      load();
    } finally {
      setTestingId(null);
    }
  };

  const remove = async (row: TeamCredentialStatus) => {
    try {
      await deleteTeamCredential(teamId, row.datasource_id);
      message.success("已删除");
      load();
    } catch (e: any) {
      message.error(errMsg(e, "删除失败"));
    }
  };

  const columns = [
    { title: "数据源", dataIndex: "datasource_name" },
    { title: "引擎", dataIndex: "engine", render: (e: string) => <Tag>{e}</Tag> },
    {
      title: "地址",
      render: (_: any, r: TeamCredentialStatus) =>
        r.host ? `${r.host}:${r.port}/${r.database || ""}` : "—",
    },
    // 库用户名只有团队管理员/平台管理员拿得到(后端按 reveal_username 分级返回)
    { title: "团队账号", dataIndex: "username", render: dash },
    {
      title: "状态",
      width: 150,
      render: (_: any, r: TeamCredentialStatus) => <CredentialTag c={r} />,
    },
    {
      title: "最后更新",
      width: 190,
      render: (_: any, r: TeamCredentialStatus) =>
        r.configured ? (
          <span style={{ color: "#8c8c8c", fontSize: 12 }}>
            {r.updated_by_name ? `${r.updated_by_name} · ` : ""}
            {fmtTime(r.updated_at)}
          </span>
        ) : (
          "—"
        ),
    },
    ...(canEdit
      ? [
          {
            title: "操作",
            width: 230,
            render: (_: any, r: TeamCredentialStatus) => (
              <Space size={0}>
                <Button type="link" size="small" onClick={() => openEdit(r)}>
                  {r.configured ? "修改" : "配置"}
                </Button>
                <Button
                  type="link"
                  size="small"
                  disabled={!r.configured}
                  loading={testingId === r.datasource_id}
                  onClick={() => test(r)}
                >
                  测试连接
                </Button>
                {r.configured && (
                  <Popconfirm
                    title={`删除本团队在「${r.datasource_name}」上的取数账号?`}
                    description="删除后,本团队在该数据源上的任务将无法运行。"
                    okText="删除"
                    okButtonProps={{ danger: true }}
                    cancelText="取消"
                    onConfirm={() => remove(r)}
                  >
                    <Button type="link" size="small" danger>
                      删除
                    </Button>
                  </Popconfirm>
                )}
              </Space>
            ),
          },
        ]
      : []),
  ];

  const unverified = rows.filter((r) => r.configured && !r.verified).length;

  return (
    <>
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message={`本团队的任务，取数时用的是这里登记的数据库账号`}
        description={
          <>
            业务用户运行本团队的任务时，走的是团队这套账号 —— 能取到哪些数据由数据库的授权决定。
            <br />
            账号<b>存下来就生效</b>：本团队在该数据源上的任务即可上线、可运行。「测试连接」是
            <b>可选的自检</b>，用来当场确认账号填对了没；不点也不影响使用，只是账号真填错时
            要等到运行失败才知道。密码加密存储，<b>任何人（包括平台管理员）都无法查看</b>。
            {!canEdit && (
              <>
                <br />
                只有本团队的<b>团队管理员</b>可以配置账号；库账号名对普通成员不展示。
              </>
            )}
          </>
        }
      />
      {/* 弱提醒:未测通不拦任何事,只是「还没验过,建议顺手点一下」 */}
      {canEdit && unverified > 0 && (
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 16 }}
          message={`有 ${unverified} 个数据源的账号还没验过,建议点一次「测试连接」确认能连上（不影响任务运行）`}
        />
      )}
      <Table rowKey="datasource_id" loading={loading} dataSource={rows} columns={columns} />
      <Modal
        title={`配置团队「${teamName}」在「${editing?.datasource_name || ""}」上的取数账号`}
        open={!!editing}
        onCancel={() => setEditing(null)}
        footer={[
          <Button key="cancel" onClick={() => setEditing(null)}>
            取消
          </Button>,
          // 次要按钮而不是必经步骤:测试连接是可选的自检
          <Button
            key="save-test"
            onClick={saveAndTest}
            loading={saving || testingId === editing?.datasource_id}
          >
            保存并测试连接
          </Button>,
          <Button key="save" type="primary" onClick={save} loading={saving}>
            保存
          </Button>,
        ]}
      >
        <Form form={form} layout="vertical">
          <Form.Item
            name="username"
            label="数据库用户名"
            rules={[{ required: true, message: "请填写数据库用户名" }]}
          >
            <Input placeholder="本团队在该库上的账号" autoComplete="off" />
          </Form.Item>
          <Form.Item
            name="password"
            label="密码"
            extra={
              editing?.configured
                ? "留空则不修改现有密码;Hive 在 NONE 认证下可不填"
                : "Hive 在 NONE 认证方式下可不填"
            }
          >
            <Input.Password
              placeholder={editing?.configured ? "留空则不修改" : ""}
              autoComplete="new-password"
            />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}
