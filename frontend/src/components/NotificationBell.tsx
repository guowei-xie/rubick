import { useEffect, useState } from "react";
import { Badge, Button, Empty, List, Popover, Tag } from "antd";
import { BellOutlined } from "@ant-design/icons";
import { useNavigate } from "react-router-dom";
import {
  listNotifications,
  readAllNotifications,
  readNotification,
  unreadCount,
} from "../api";
import StatusTag, { NOTE_LEVEL } from "./StatusTag";

export default function NotificationBell() {
  const [count, setCount] = useState(0);
  const [items, setItems] = useState<any[]>([]);
  const [open, setOpen] = useState(false);
  const nav = useNavigate();

  const refreshCount = () => unreadCount().then(setCount).catch(() => {});
  const refreshList = () => listNotifications(false).then(setItems).catch(() => {});

  // 轮询未读数(演示用 8s;生产可换 SSE/WebSocket)
  useEffect(() => {
    refreshCount();
    const t = setInterval(refreshCount, 8000);
    return () => clearInterval(t);
  }, []);

  const onOpenChange = (v: boolean) => {
    setOpen(v);
    if (v) refreshList();
  };

  const clickItem = async (n: any) => {
    if (!n.is_read) {
      await readNotification(n.id);
      refreshCount();
      refreshList();
    }
    setOpen(false);
    // 深链到该任务的运行记录(template_id 由通知直接携带,无需再查 job)
    if (n.template_id) nav(`/tasks?records=${n.template_id}`);
    else if (n.job_id) nav("/tasks");
  };

  const markAll = async () => {
    await readAllNotifications();
    refreshCount();
    refreshList();
  };

  const content = (
    <div style={{ width: 340 }}>
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8 }}>
        <b>通知</b>
        <Button size="small" type="link" onClick={markAll}>
          全部已读
        </Button>
      </div>
      {items.length === 0 ? (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无通知" />
      ) : (
        <List
          size="small"
          dataSource={items}
          style={{ maxHeight: 360, overflow: "auto" }}
          renderItem={(n) => (
            <List.Item
              onClick={() => clickItem(n)}
              style={{ cursor: "pointer", background: n.is_read ? undefined : "#f0f7ff" }}
            >
              <List.Item.Meta
                title={
                  <span>
                    <StatusTag map={NOTE_LEVEL} value={n.level} />
                    {n.title}
                    {!n.feishu_sent && (
                      <Tag style={{ marginLeft: 6 }}>站内</Tag>
                    )}
                  </span>
                }
                description={<span style={{ fontSize: 12 }}>{n.body}</span>}
              />
            </List.Item>
          )}
        />
      )}
    </div>
  );

  return (
    <Popover content={content} trigger="click" open={open} onOpenChange={onOpenChange} placement="bottomRight">
      <Badge count={count} size="small">
        <BellOutlined style={{ color: "#fff", fontSize: 18, cursor: "pointer" }} />
      </Badge>
    </Popover>
  );
}
