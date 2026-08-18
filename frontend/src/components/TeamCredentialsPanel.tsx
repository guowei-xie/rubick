import { useEffect, useState } from "react";
import { Alert, Button, Form, Input, message, Modal, Popconfirm, Space, Table, Tag } from "antd";
import {
  TeamCredentialStatus,
  deleteTeamCredential,
  errMsg,
  listTeamCredentials,
  saveTeamCredential,
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

  const doSave = async (v: { username: string; password?: string }) => {
    if (!editing) return;
    setSaving(true);
    try {
      replaceRow(
        await saveTeamCredential(teamId, editing.datasource_id, {
          username: v.username,
          // 留空 = 保留原密码(与数据源编辑同一约定)
          password: v.password || undefined,
        })
      );
      message.success("已保存,请点「测试连接」验证后本团队的任务才能上线");
      setEditing(null);
    } catch (e: any) {
      message.error(errMsg(e, "保存失败"));
    } finally {
      setSaving(false);
    }
  };

  const save = async () => {
    const v = await form.validateFields();
    // 改动会清空测通状态 —— 团队账号是共享的,一次误改会让该数据源上**本团队的全部任务**
    // 立刻停摆。个人账号时代没有这个风险,所以这里要专门确认一次。
    const willBreak = editing?.verified && (v.password || v.username !== editing.username);
    if (willBreak) {
      Modal.confirm({
        title: "修改后需要重新测通",
        content: (
          <>
            保存后,该数据源上<b>本团队的全部任务</b>会立即变为「未就绪」、无法运行,
            直到你重新点「测试连接」并通过。确认修改?
          </>
        ),
        okText: "确认修改",
        cancelText: "取消",
        onOk: () => doSave(v),
      });
      return;
    }
    await doSave(v);
  };

  const test = async (row: TeamCredentialStatus) => {
    setTestingId(row.datasource_id);
    try {
      replaceRow(await testTeamCredential(teamId, row.datasource_id));
      message.success("连接成功,该数据源上的任务可以上线了");
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
            账号需<b>测试连接通过</b>才算就绪：未就绪的数据源上，本团队的任务无法上线、也无法运行。
            <b>改过用户名或密码后测通状态会被清空</b>，该数据源上本团队的全部任务会一起停摆，
            直到重新测通。密码加密存储，<b>任何人（包括平台管理员）都无法查看</b>。
            {!canEdit && (
              <>
                <br />
                只有本团队的<b>团队管理员</b>可以配置账号；库账号名对普通成员不展示。
              </>
            )}
          </>
        }
      />
      {canEdit && unverified > 0 && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 16 }}
          message={`有 ${unverified} 个数据源的账号尚未测通,该数据源上本团队的任务现在跑不动`}
        />
      )}
      <Table rowKey="datasource_id" loading={loading} dataSource={rows} columns={columns} />
      <Modal
        title={`配置团队「${teamName}」在「${editing?.datasource_name || ""}」上的取数账号`}
        open={!!editing}
        onCancel={() => setEditing(null)}
        onOk={save}
        confirmLoading={saving}
        okText="保存"
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
