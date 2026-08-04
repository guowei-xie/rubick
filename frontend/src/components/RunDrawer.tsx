import { useEffect, useState } from "react";
import { Button, Drawer, Form, message, Space, Typography } from "antd";
import { downloadJob, errMsg, getJob, getTemplate, ParamDef, previewJob, runQuery, withBase } from "../api";
import { ParamField } from "./ParamForm";
import ResultPreviewTable from "./ResultPreviewTable";
import SqlModal from "./SqlModal";

/** 业务用户填参取数:填表单 → 运行并预览(异步轮询)→ 确认后下载完整结果。 */
export default function RunDrawer({
  task,
  open,
  onClose,
}: {
  task: any;
  open: boolean;
  onClose: () => void;
}) {
  const [params, setParams] = useState<ParamDef[]>([]);
  const [desc, setDesc] = useState("");
  const [running, setRunning] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [job, setJob] = useState<any>(null); // 跑成功的任务(id / row_count / executed_sql)
  const [preview, setPreview] = useState<any>(null); // { columns, rows, row_count }(前 50 行)
  const [showSql, setShowSql] = useState(false);
  const [form] = Form.useForm();

  useEffect(() => {
    if (!open || !task) return;
    setJob(null);
    setPreview(null);
    getTemplate(task.id).then((d) => {
      const defs: ParamDef[] = d.published_version?.params || [];
      setParams(defs);
      setDesc(d.description || "");
      form.resetFields();
    });
  }, [open, task]);

  const close = () => {
    setJob(null);
    setPreview(null);
    onClose();
  };

  const rerun = () => {
    setJob(null);
    setPreview(null);
  };

  // 第一步:运行并取预览(不下载)
  const runAndPreview = async () => {
    const values = await form.validateFields().catch(() => null);
    if (!values) return;
    setRunning(true);
    setJob(null);
    setPreview(null);
    const hide = message.loading("已提交,执行中…(复杂查询可能要几分钟,请稍候)", 0);
    try {
      let j = await runQuery(task.id, values);
      // 有些 Hive 查询要跑十几分钟,轮询窗口放宽到 20 分钟;用退避间隔(1s→4s)减少请求
      const started = Date.now();
      const MAX_WAIT_MS = 20 * 60 * 1000;
      let delay = 1000;
      while ((j.status === "queued" || j.status === "running") && Date.now() - started < MAX_WAIT_MS) {
        await new Promise((r) => setTimeout(r, delay));
        delay = Math.min(delay + 500, 4000);
        j = await getJob(j.id);
      }
      hide();
      if (j.status === "success") {
        const pv = await previewJob(j.id);
        setJob(j);
        setPreview(pv);
        message.success(`取数完成,共 ${j.row_count} 行,请预览确认后下载`);
      } else if (j.status === "failed") {
        message.error(j.error || "取数失败");
      } else {
        message.warning("查询仍在执行,完成后会在「运行记录」和通知里,可稍后查看并下载");
      }
    } catch (e: any) {
      hide();
      message.error(errMsg(e, "取数失败"));
    } finally {
      setRunning(false);
    }
  };

  // 第二步:确认下载完整结果
  const confirmDownload = async () => {
    if (!job) return;
    setDownloading(true);
    try {
      const dl = await downloadJob(job.id);
      window.open(withBase(dl.url), "_blank");
      message.success("已开始下载");
      close();
    } catch (e: any) {
      message.error(errMsg(e, "下载失败"));
    } finally {
      setDownloading(false);
    }
  };

  return (
    <Drawer
      title={task ? `取数:${task.name}` : ""}
      open={open}
      onClose={close}
      width={preview ? 760 : 480}
      extra={
        preview ? (
          <Space>
            <Button onClick={rerun}>重新运行</Button>
            <Button type="primary" loading={downloading} onClick={confirmDownload}>
              确认下载
            </Button>
          </Space>
        ) : (
          <Button type="primary" loading={running} onClick={runAndPreview}>
            运行并预览
          </Button>
        )
      }
    >
      {desc && (
        <div
          style={{
            background: "var(--app-bg)",
            borderRadius: 14,
            padding: "12px 16px",
            marginBottom: 16,
            color: "#4a4a4a",
            lineHeight: "22px",
          }}
        >
          {desc}
        </div>
      )}
      <Form form={form} layout="vertical">
        {params.map((pd) => (
          <ParamField key={pd.name} pd={pd} templateId={task?.id} />
        ))}
        {params.length === 0 && <Typography.Text type="secondary">该任务无需参数</Typography.Text>}
      </Form>

      {preview && (
        <div
          style={{
            marginTop: 8,
            background: "#fafbff",
            border: "1px solid #eef0f7",
            borderRadius: 16,
            padding: 16,
          }}
        >
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              marginBottom: 10,
            }}
          >
            <span style={{ fontWeight: 600, color: "var(--ink)" }}>
              结果预览
              <span style={{ fontWeight: 400, color: "#888", marginLeft: 8, fontSize: 13 }}>
                前 {preview.rows.length} 行 / 共 {preview.row_count} 行,下载为完整结果
              </span>
            </span>
            {job?.executed_sql && (
              <Button type="link" size="small" onClick={() => setShowSql(true)}>
                查看执行SQL
              </Button>
            )}
          </div>
          <ResultPreviewTable columns={preview.columns} rows={preview.rows} scrollY={400} />
        </div>
      )}
      <SqlModal sql={showSql ? job?.executed_sql || "" : null} onClose={() => setShowSql(false)} />
    </Drawer>
  );
}
