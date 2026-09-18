import { useEffect, useState } from "react";
import {
  Alert,
  Avatar,
  Button,
  Modal,
  Popconfirm,
  Select,
  Space,
  Table,
  Tabs,
  Tag,
  Typography,
  message,
} from "antd";
import {
  errMsg,
  removeTaskSubscriber,
  SubscribeForSubject,
  SubscriberRow,
  subscribeTaskFor,
  SubscriptionEvent,
  taskSubscribers,
  taskSubscriptionEvents,
} from "../api";
import { fmtTime } from "../format";
import { useUserPicker } from "./useUserPicker";

/** 订阅事件的配色:订上=绿、退掉=灰,其余(自动清退/关闭订阅/离队/被移出)=橙。
 *  标签文案由后端按 SUB_EVENT_META 译好,这里只管颜色。 */
const EVENT_COLOR: Record<string, string> = {
  subscribe: "green",
  added_by_manager: "green",
  unsubscribe: "default",
  removed_by_manager: "default",
};

/** 某任务的订阅情况(开发者/团队管理员视角):
 *  「订阅者」= 在册名单 + 各自连续未消费期数(临近自动退订阈值标橙),可加人、可移除;
 *  「订阅记录」= append-only 留痕(谁在何时订阅/退订/被代订/被移出/被清退,可审查)。 */
export default function SubscribersModal({
  task,
  open,
  onClose,
  onDone,
}: {
  task: any;
  open: boolean;
  onClose: () => void;
  /** 加/移成功后调用:订阅人数印在卡片的计划标签上,不重拉列表会停在旧数字。 */
  onDone: () => void;
}) {
  const [threshold, setThreshold] = useState(10);
  const [subs, setSubs] = useState<SubscriberRow[]>([]);
  const [events, setEvents] = useState<SubscriptionEvent[]>([]);
  const [loading, setLoading] = useState(false);
  const [picked, setPicked] = useState<string[]>([]);
  const [adding, setAdding] = useState(false);
  // 整批被拒时后端回的是多行文案(全成功才生效),留在弹窗里显示,选中的人不清空 ——
  // 同 BulkTransferAuthorModal:让人改选了再试,而不是关掉重来
  const [failure, setFailure] = useState<string>();
  const { options, onSearch, fetchNow, profileOf } = useUserPicker();

  const load = () => {
    if (!task) return;
    setLoading(true);
    Promise.all([taskSubscribers(task.id), taskSubscriptionEvents(task.id)])
      .then(([s, ev]) => {
        setThreshold(s.threshold);
        setSubs(s.items);
        setEvents(ev);
      })
      .catch((e) => message.error(errMsg(e, "加载订阅信息失败")))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    if (!open || !task) return;
    setPicked([]);
    setFailure(undefined);
    load();
    fetchNow("");
  }, [open, task]);

  const add = async () => {
    if (!picked.length) return message.warning("请先选择要添加的人");
    const subjects: SubscribeForSubject[] = picked.map((openId) => {
      const r = profileOf(openId);
      // 已落库的人直接给 id,省掉服务端一次 open_id 解析(和可能的一次通讯录往返);
      // 通讯录里搜出来的新人才走 open_id,且**不回传邮箱** —— 客户端可写 email 是一条提权路径
      return r?.id
        ? { subject_id: String(r.id) }
        : { subject_open_id: openId, subject_name: r?.name, subject_avatar: r?.avatar };
    });
    setAdding(true);
    setFailure(undefined);
    try {
      const r = await subscribeTaskFor(task.id, subjects);
      const parts = [`已为 ${r.created.length} 人订阅`];
      if (r.granted_view.length) parts.push(`其中 ${r.granted_view.length} 人同时获得查看权限`);
      if (r.skipped.length) parts.push(`${r.skipped.length} 人已在名单中`);
      message.success(parts.join("，"));
      setPicked([]);
      load();
      onDone();
    } catch (e: any) {
      setFailure(errMsg(e, "添加订阅者失败"));
    } finally {
      setAdding(false);
    }
  };

  const remove = async (row: SubscriberRow) => {
    try {
      await removeTaskSubscriber(task.id, row.user_id);
      message.success("已移除");
      load();
      onDone();
    } catch (e: any) {
      message.error(errMsg(e, "移除失败"));
    }
  };

  const subColumns = [
    {
      title: "订阅者",
      render: (_: any, r: SubscriberRow) => (
        <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
          <Avatar size={24} src={r.avatar || undefined}>
            {(r.name || "?").slice(0, 1)}
          </Avatar>
          {r.name || `用户#${r.user_id}`}
        </span>
      ),
    },
    {
      title: "来源",
      width: 140,
      render: (_: any, r: SubscriberRow) =>
        r.added_by ? (
          <span style={{ color: "#888" }}>
            由 {r.added_by_name || `用户#${r.added_by}`} 代订
          </span>
        ) : (
          <span style={{ color: "#888" }}>自助订阅</span>
        ),
    },
    {
      title: "订阅时间",
      dataIndex: "created_at",
      width: 170,
      render: (t: string) => fmtTime(t, false),
    },
    {
      title: "连续未消费期数",
      dataIndex: "miss_streak",
      width: 150,
      render: (n: number) =>
        // 临近阈值标橙:再有一两期没人看就要被自动清退了
        n >= threshold - 1 && n > 0 ? (
          <Tag color="orange">{n} / {threshold}</Tag>
        ) : (
          <span>{n} / {threshold}</span>
        ),
    },
    {
      title: "",
      width: 70,
      render: (_: any, r: SubscriberRow) => (
        <Popconfirm
          title="移出订阅者名单"
          description="他不再收到该任务的推送，并会收到一条通知；查看权限不受影响。"
          okText="移除"
          cancelText="取消"
          onConfirm={() => remove(r)}
        >
          <Button type="link" size="small" danger>移除</Button>
        </Popconfirm>
      ),
    },
  ];

  const eventColumns = [
    {
      title: "时间",
      dataIndex: "created_at",
      width: 170,
      render: (t: string) => fmtTime(t, false),
    },
    { title: "当事人", dataIndex: "user_name", width: 120, render: (n: string, r: SubscriptionEvent) => n || `用户#${r.user_id}` },
    {
      title: "动作",
      dataIndex: "action_label",
      width: 170,
      render: (label: string, r: SubscriptionEvent) => (
        <Tag color={EVENT_COLOR[r.action] || "orange"}>{label}</Tag>
      ),
    },
    {
      title: "触发者",
      dataIndex: "operator_name",
      render: (n: string, r: SubscriptionEvent) =>
        // 无触发者 = 平台自动动作(连续未消费清退)
        n || (r.operator_id ? `用户#${r.operator_id}` : <span style={{ color: "#999" }}>平台自动</span>),
    },
  ];

  return (
    <Modal
      title={task ? `订阅情况:${task.name}` : ""}
      open={open}
      onCancel={onClose}
      footer={null}
      width={760}
    >
      <Tabs
        items={[
          {
            key: "subscribers",
            label: `订阅者(${subs.length})`,
            children: (
              <>
                <Space.Compact style={{ width: "100%", marginBottom: 8 }}>
                  <Select
                    mode="multiple"
                    showSearch
                    filterOption={false}
                    value={picked}
                    onChange={setPicked}
                    onSearch={onSearch}
                    options={options}
                    optionFilterProp="title"
                    placeholder="搜同事姓名，可一次选多人"
                    style={{ width: "100%" }}
                  />
                  <Button type="primary" loading={adding} onClick={add}>
                    添加
                  </Button>
                </Space.Compact>
                {failure && (
                  <Alert
                    type="error"
                    showIcon
                    style={{ marginBottom: 8 }}
                    message="本次添加未生效，没有人被订上"
                    // pre-line:后端把每条拒绝写成了自成一句的整行,折成一行就读不出是哪几条
                    description={<span style={{ whiteSpace: "pre-line" }}>{failure}</span>}
                  />
                )}
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  没有查看权限的人会在同一次操作里补上「查看」；有一个人不能订则整批不生效。
                  连续 {threshold} 个成功运行期未查看数据(下载或预览)的订阅者，平台会自动取消其订阅并通知本人。
                </Typography.Text>
                <Table
                  rowKey="user_id"
                  size="small"
                  loading={loading}
                  dataSource={subs}
                  columns={subColumns}
                  pagination={false}
                  style={{ marginTop: 8 }}
                  locale={{ emptyText: "还没有人订阅" }}
                />
              </>
            ),
          },
          {
            key: "events",
            label: "订阅记录",
            children: (
              <Table
                rowKey="id"
                size="small"
                loading={loading}
                dataSource={events}
                columns={eventColumns}
                pagination={{ pageSize: 10, hideOnSinglePage: true }}
                locale={{ emptyText: "暂无订阅/退订记录" }}
              />
            ),
          },
        ]}
      />
    </Modal>
  );
}
