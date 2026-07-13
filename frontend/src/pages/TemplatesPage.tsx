import { useEffect, useState } from "react";
import {
  Button,
  Card,
  Drawer,
  Empty,
  Form,
  message,
  Space,
  Table,
  Tag,
  Typography,
} from "antd";
import {
  downloadJob,
  errMsg,
  getJob,
  getTemplate,
  listTemplates,
  ParamDef,
  runQuery,
} from "../api";
import { initialValues, ParamField, serializeValues } from "../components/ParamForm";

export default function TemplatesPage() {
  const [templates, setTemplates] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(false);
  const [current, setCurrent] = useState<any>(null);
  const [params, setParams] = useState<ParamDef[]>([]);
  const [running, setRunning] = useState(false);
  const [form] = Form.useForm();

  const load = () => {
    setLoading(true);
    listTemplates(false)
      .then(setTemplates)
      .finally(() => setLoading(false));
  };
  useEffect(load, []);

  const openRun = async (row: any) => {
    const detail = await getTemplate(row.id);
    const defs: ParamDef[] = detail.published_version?.params || [];
    setCurrent(detail);
    setParams(defs);
    setOpen(true);
    form.resetFields();
    form.setFieldsValue(initialValues(defs));
  };

  const submit = async () => {
    const values = await form.validateFields().catch(() => null);
    if (!values) return; // 表单校验错误
    setRunning(true);
    const hide = message.loading("已提交,排队执行中…", 0);
    try {
      let job = await runQuery(current.id, serializeValues(params, values));
      // 异步:轮询任务状态直到终态(worker 在后台执行)
      for (let i = 0; i < 120 && (job.status === "queued" || job.status === "running"); i++) {
        await new Promise((r) => setTimeout(r, 800));
        job = await getJob(job.id);
      }
      hide();
      if (job.status === "success") {
        message.success(`取数完成,共 ${job.row_count} 行,开始下载`);
        const dl = await downloadJob(job.id);
        window.open(dl.url, "_blank");
        setOpen(false);
      } else if (job.status === "failed") {
        message.error(job.error || "取数失败");
      } else {
        message.warning("任务仍在执行,可稍后到「我的任务」查看");
      }
    } catch (e: any) {
      hide();
      message.error(errMsg(e, "取数失败"));
    } finally {
      setRunning(false);
    }
  };

  const columns = [
    { title: "名称", dataIndex: "name" },
    { title: "业务域", dataIndex: "domain", render: (d: string) => d && <Tag>{d}</Tag> },
    { title: "说明", dataIndex: "description", ellipsis: true },
    {
      title: "数据源方言",
      dataIndex: "dialect",
      render: (d: string) => <Tag color={d === "hive" ? "orange" : "green"}>{d}</Tag>,
    },
    {
      title: "操作",
      render: (_: any, row: any) => (
        <Button type="link" onClick={() => openRun(row)}>
          填参取数
        </Button>
      ),
    },
  ];

  return (
    <Card title="取数模板" extra={<Button onClick={load}>刷新</Button>}>
      {templates.length === 0 && !loading ? (
        <Empty description="暂无有权限的模板,请联系管理员授权" />
      ) : (
        <Table rowKey="id" loading={loading} dataSource={templates} columns={columns} />
      )}

      <Drawer
        title={current ? `取数:${current.name}` : ""}
        open={open}
        onClose={() => setOpen(false)}
        width={480}
        extra={
          <Button type="primary" loading={running} onClick={submit}>
            运行并下载
          </Button>
        }
      >
        <Typography.Paragraph type="secondary">{current?.description}</Typography.Paragraph>
        <Form form={form} layout="vertical">
          {params.map((pd) => (
            <ParamField key={pd.name} pd={pd} />
          ))}
          {params.length === 0 && <Typography.Text type="secondary">该模板无需参数</Typography.Text>}
        </Form>
      </Drawer>
    </Card>
  );
}
