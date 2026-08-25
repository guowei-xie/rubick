import { useEffect, useState } from "react";
import { Avatar, Modal, Table, Tabs, Tag, Typography, message } from "antd";
import {
  errMsg,
  SubscriberRow,
  SubscriptionEvent,
  taskSubscribers,
  taskSubscriptionEvents,
} from "../api";
import { fmtTime } from "../format";

/** 某任务的订阅情况(开发者/团队管理员视角):
 *  「订阅者」= 在册名单 + 各自连续未消费期数(临近自动退订阈值标橙);
 *  「订阅记录」= append-only 留痕(谁在何时订阅/退订/被清退,可审查)。 */
export default function SubscribersModal({
  task,
  open,
  onClose,
}: {
  task: any;
  open: boolean;
  onClose: () => void;
}) {
  const [threshold, setThreshold] = useState(10);
  const [subs, setSubs] = useState<SubscriberRow[]>([]);
  const [events, setEvents] = useState<SubscriptionEvent[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!open || !task) return;
    setLoading(true);
    Promise.all([taskSubscribers(task.id), taskSubscriptionEvents(task.id)])
      .then(([s, ev]) => {
        setThreshold(s.threshold);
        setSubs(s.items);
        setEvents(ev);
      })
      .catch((e) => message.error(errMsg(e, "加载订阅信息失败")))
      .finally(() => setLoading(false));
  }, [open, task]);

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
        <Tag color={r.action === "subscribe" ? "green" : r.action === "unsubscribe" ? "default" : "orange"}>
          {label}
        </Tag>
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
      width={640}
    >
      <Tabs
        items={[
          {
            key: "subscribers",
            label: `订阅者(${subs.length})`,
            children: (
              <>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  连续 {threshold} 个成功运行期未查看数据(下载或预览)的订阅者,平台会自动取消其订阅并通知本人。
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
