import { useEffect, useState } from "react";
import { Button, Card, message, Table } from "antd";
import { downloadJob, errMsg, listJobs } from "../api";
import StatusTag, { JOB_STATUS } from "../components/StatusTag";

export default function JobsPage() {
  const [jobs, setJobs] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const hasPending = jobs.some((j) => j.status === "queued" || j.status === "running");

  const load = () => {
    setLoading(true);
    listJobs()
      .then(setJobs)
      .finally(() => setLoading(false));
  };
  useEffect(load, []);

  // 有未完成任务时自动轮询,直到全部终态(依赖派生布尔值,避免每次轮询重建定时器)
  useEffect(() => {
    if (!hasPending) return;
    const t = setInterval(() => listJobs().then(setJobs), 2000);
    return () => clearInterval(t);
  }, [hasPending]);

  const download = async (id: number) => {
    try {
      const dl = await downloadJob(id);
      window.open(dl.url, "_blank");
    } catch (e: any) {
      message.error(errMsg(e, "下载失败"));
    }
  };

  const columns = [
    { title: "任务号", dataIndex: "id", width: 90 },
    {
      title: "模板",
      dataIndex: "template_name",
      render: (name: string, row: any) => name || `#${row.template_id}`,
    },
    {
      title: "状态",
      dataIndex: "status",
      render: (s: string) => <StatusTag map={JOB_STATUS} value={s} />,
    },
    { title: "行数", dataIndex: "row_count" },
    { title: "耗时(ms)", dataIndex: "duration_ms" },
    {
      title: "操作",
      render: (_: any, row: any) => {
        if (row.status === "success" && row.result_expired)
          return <span style={{ color: "#999" }}>结果已过期,请重新运行</span>;
        if (row.status === "success")
          return (
            <Button type="link" onClick={() => download(row.id)}>
              下载
            </Button>
          );
        return <span style={{ color: "#999" }}>{row.error ? "见错误" : "-"}</span>;
      },
    },
  ];

  return (
    <Card title="我的取数任务" extra={<Button onClick={load}>刷新</Button>}>
      <Table rowKey="id" loading={loading} dataSource={jobs} columns={columns} />
    </Card>
  );
}
