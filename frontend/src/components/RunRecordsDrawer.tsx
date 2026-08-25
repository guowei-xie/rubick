import { useEffect, useState } from "react";
import { Button, Drawer, message, Modal, Space, Table } from "antd";
import { downloadJob, errMsg, previewJob, taskRunRecords, withBase } from "../api";
import StatusTag, { JOB_SOURCE, JOB_STATUS } from "./StatusTag";
import ResultPreviewTable from "./ResultPreviewTable";
import SqlModal from "./SqlModal";

/** 任务的运行记录:谁、何时、参数、状态、结果预览/导出。管理者(管理员/开发者)与作者看全部,其他人看自己。 */
export default function RunRecordsDrawer({
  task,
  open,
  onClose,
}: {
  task: any;
  open: boolean;
  onClose: () => void;
}) {
  const [rows, setRows] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [previewData, setPreviewData] = useState<any>(null);
  const [sqlText, setSqlText] = useState<string | null>(null);
  const [paramsData, setParamsData] = useState<any>(null);

  useEffect(() => {
    if (!open || !task) return;
    setLoading(true);
    taskRunRecords(task.id)
      .then(setRows)
      .finally(() => setLoading(false));
  }, [open, task]);

  const download = async (id: number) => {
    try {
      const dl = await downloadJob(id);
      window.open(withBase(dl.url), "_blank");
    } catch (e: any) {
      message.error(errMsg(e, "下载失败"));
    }
  };

  const preview = async (id: number) => {
    try {
      const pv = await previewJob(id);
      setPreviewData(pv);
    } catch (e: any) {
      message.error(errMsg(e, "预览失败"));
    }
  };

  const columns = [
    {
      title: "类型",
      dataIndex: "source",
      width: 70,
      render: (s: string) => <StatusTag map={JOB_SOURCE} value={s || "run"} />,
    },
    { title: "运行人", dataIndex: "user_name", width: 100 },
    { title: "时间", dataIndex: "created_at", width: 170 },
    { title: "状态", dataIndex: "status", render: (s: string) => <StatusTag map={JOB_STATUS} value={s} /> },
    { title: "行数", dataIndex: "row_count", width: 80 },
    {
      title: "参数",
      width: 90,
      render: (_: any, r: any) => {
        const n = Object.keys(r.params || {}).length;
        return n ? (
          <Button type="link" size="small" onClick={() => setParamsData(r.params)}>查看({n})</Button>
        ) : (
          <span style={{ color: "#ccc" }}>—</span>
        );
      },
    },
    {
      title: "执行SQL",
      width: 80,
      render: (_: any, r: any) =>
        r.executed_sql ? (
          <Button type="link" size="small" onClick={() => setSqlText(r.executed_sql)}>查看</Button>
        ) : (
          <span style={{ color: "#ccc" }}>—</span>
        ),
    },
    {
      title: "结果",
      width: 150,
      render: (_: any, r: any) => {
        if (r.status !== "success") return <span style={{ color: "#999" }}>-</span>;
        if (r.result_expired) return <span style={{ color: "#999" }}>已过期</span>;
        return (
          <Space>
            <Button type="link" size="small" onClick={() => preview(r.id)}>预览</Button>
            <Button type="link" size="small" onClick={() => download(r.id)}>导出</Button>
          </Space>
        );
      },
    },
  ];

  return (
    <Drawer title={task ? `运行记录:${task.name}` : ""} open={open} onClose={onClose} width={860}>
      <Table rowKey="id" size="small" loading={loading} dataSource={rows} columns={columns} />

      <Modal
        title="结果预览(前 50 行)"
        open={!!previewData}
        onCancel={() => setPreviewData(null)}
        footer={null}
        width={900}
      >
        {previewData && (
          <>
            <div style={{ marginBottom: 8, color: "#888" }}>共 {previewData.row_count} 行</div>
            <ResultPreviewTable columns={previewData.columns} rows={previewData.rows} scrollY={400} />
          </>
        )}
      </Modal>

      <SqlModal sql={sqlText} onClose={() => setSqlText(null)} />

      <Modal title="本次运行参数" open={!!paramsData} onCancel={() => setParamsData(null)} footer={null} width={560}>
        {paramsData && (
          <Table
            size="small"
            rowKey="k"
            pagination={false}
            scroll={{ y: 400 }}
            columns={[
              { title: "参数", dataIndex: "k", width: 180 },
              { title: "值", dataIndex: "v" },
            ]}
            dataSource={Object.entries(paramsData).map(([k, v]) => ({
              k,
              v: Array.isArray(v) ? v.join("、") : v == null ? "" : String(v),
            }))}
          />
        )}
      </Modal>
    </Drawer>
  );
}
