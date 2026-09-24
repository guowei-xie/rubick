import { useEffect, useRef, useState } from "react";
import { Button, Collapse, Drawer, Form, message, Space, Typography } from "antd";
import { downloadJob, errMsg, getJob, getTemplate, Job, ParamDef, previewJob, runQuery, withBase } from "../api";
import { fmtTime } from "../format";
import { ParamField } from "./ParamForm";
import ResultPreviewTable from "./ResultPreviewTable";
import RunRecordsPanel from "./RunRecordsPanel";
import SqlModal from "./SqlModal";
import { DRAWER } from "../widths";

/** 「运行记录」折叠区块的开合记在本地:用户来这儿本就是为了少点几下,
 * 展开过一次的人不该每开一个任务都再点一次。跨任务生效,故不随抽屉重置。 */
const RECORDS_OPEN_KEY = "rubic_run_drawer_records_open";

// 抽屉里两处小节标题(结果预览 / 运行记录)共用的字重与副标样式,免得隔着 30 行各写一份漂移。
// #888 与 ParamForm 的 HINT 同源,比 --ink-secondary(#6b6880)浅一档,是既有观感,不动它。
const SECTION_TITLE: React.CSSProperties = { fontWeight: 600, color: "var(--ink)" };
const SECTION_HINT: React.CSSProperties = { fontWeight: 400, color: "#888", marginLeft: 8, fontSize: 13 };
// 抽屉里的提示条(等待实况 / 复用了现成结果)共用一套外观,两条说的是同一类「系统在替你做什么」
const NOTICE: React.CSSProperties = {
  background: "#f6f8ff",
  border: "1px solid #e3e8f7",
  borderRadius: 12,
  padding: "10px 14px",
  marginBottom: 14,
  color: "var(--ink-secondary)",
  fontSize: 13,
};

/** 等待期该显示哪句话。
 *
 * 「排在队列里」和「正在跑」对用户是两件事:前者的意思是「有人在你前面」,后者才是
 * 「系统在跑你的活」。原先两种状态共用一句「已提交,执行中…」,于是排队半小时读起来像卡死。
 * 一并给出已等时长 —— 没有它,用户无从判断该继续等还是去干别的。
 *
 * 两处措辞是刻意的:queue_ahead 数的是**还在排队**的那些(不是在跑的),所以说「在等」;
 * ahead=0 也不说「马上」—— 在跑的可能是一个上限 1 小时的 Hive 任务,「马上」会让人干等,
 * 而这一条提示的后半句本来就写着「关掉也不影响」。
 */
function describeWait(job: Job, startedAt: number): string {
  const mins = Math.floor((Date.now() - startedAt) / 60000);
  const elapsed = mins >= 1 ? `已等 ${mins} 分钟` : "已等不到 1 分钟";
  if (job.status === "queued") {
    const ahead = job.queue_ahead ?? 0;
    return ahead > 0
      ? `排队中:前面还有 ${ahead} 个取数在等,${elapsed}`
      : `排队中:你是下一个,等正在跑的取数腾出位子就开始,${elapsed}`;
  }
  if (job.status === "running") return `正在取数,${elapsed}`;
  return elapsed;
}

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
  const [job, setJob] = useState<Job | null>(null); // 跑成功的任务
  const [preview, setPreview] = useState<any>(null); // { columns, rows, row_count }(前 50 行)
  const [showSql, setShowSql] = useState(false);
  // 等待期的实况(见 describeWait);attached = 这次提交接上了自己一条同参、还没跑完的运行,
  // 面板上要说清并给「仍要重新运行」。两者同生同灭,放一个状态里,清一次就都清掉
  const [progress, setProgress] = useState<{ text: string; attached: boolean } | null>(null);
  // 每次提交递增;轮询循环发现自己不是最新那次就悄悄退出 —— 「仍要重新运行」会在
  // 上一轮还在等的时候发起新一轮,两个循环同时往同一个面板写字就乱了
  const runSeq = useRef(0);
  // 历史运行记录:折叠区块的开合(读本地偏好)与「重拉一次」的计数器
  const [recordsOpen, setRecordsOpen] = useState(
    () => localStorage.getItem(RECORDS_OPEN_KEY) === "1"
  );
  const [recordsKey, setRecordsKey] = useState(0);
  const [form] = Form.useForm();

  const toggleRecords = (next: boolean) => {
    setRecordsOpen(next);
    localStorage.setItem(RECORDS_OPEN_KEY, next ? "1" : "0");
  };

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
    runSeq.current += 1; // 关掉就不再更新面板(运行本身照常跑完)
    setJob(null);
    setPreview(null);
    setProgress(null);
    onClose();
  };

  const rerun = () => {
    setJob(null);
    setPreview(null);
  };

  // 第一步:运行并取预览(不下载)。
  // 平台**优先复用**:当天有人用同样参数跑成过,直接给那份结果;自己有一条同参的还没跑完,
  // 就接上它。fresh=true(「仍要重新运行」)一定新跑一次 —— 复用只是优先,不剥夺重跑。
  const runAndPreview = async (fresh = false) => {
    const values = await form.validateFields().catch(() => null);
    if (!values) return;
    const seq = ++runSeq.current;
    const stale = () => seq !== runSeq.current;
    setRunning(true);
    setJob(null);
    setPreview(null);
    // 等待期只留 progress 面板这一处:从前还并着一个 message.loading("已提交,执行中…"),
    // 说的正是这个面板要取代的那句笼统话 —— 两处同时在,面板说「排队中:前面还有 3 个」
    // 而浮层说「执行中」,读者信哪句?顺带也不用再在三个分支里记得 hide()。
    setProgress({ text: "已提交,正在安排…", attached: false });
    try {
      let j = await runQuery(task.id, values, fresh);
      if (stale()) return;
      // 「接上」只在提交的响应里有(轮询 /jobs/{id} 不带这个事实),先记下来
      const attached = j.reuse_kind === "inflight";
      // 有些 Hive 查询要跑十几分钟,轮询窗口放宽到 20 分钟;用退避间隔(1s→4s)减少请求
      const started = Date.now();
      const MAX_WAIT_MS = 20 * 60 * 1000;
      let delay = 1000;
      while ((j.status === "queued" || j.status === "running") && Date.now() - started < MAX_WAIT_MS) {
        // 「排队中」和「执行中」必须说成两句话:同一句话会让长等待读起来像卡死,
        // 而用户对「在等别人的活跑完」和「系统在跑我的活」的容忍度完全不同
        setProgress({ text: describeWait(j, started), attached });
        await new Promise((r) => setTimeout(r, delay));
        if (stale()) return;
        delay = Math.min(delay + 500, 4000);
        j = await getJob(j.id);
        if (stale()) return;
      }
      setProgress(null);
      if (j.status === "success") {
        const pv = await previewJob(j.id);
        if (stale()) return;
        setJob(j);
        setPreview(pv);
        // 刚跑完的这次要立刻出现在展开着的运行记录里,不劳用户手动刷新
        setRecordsKey((k) => k + 1);
        message.success(
          j.reuse_kind === "result"
            ? `已复用相同参数的现成结果,共 ${j.row_count} 行,请预览确认后下载`
            : `取数完成,共 ${j.row_count} 行,请预览确认后下载`
        );
      } else if (j.status === "failed") {
        message.error(j.error || "取数失败");
      } else if (j.status === "cancelled") {
        message.info("这次运行已被取消,可以重新运行");
      } else {
        message.warning("查询仍在执行,完成后会在「运行记录」和通知里,可稍后查看并下载");
      }
    } catch (e: any) {
      if (stale()) return;
      setProgress(null);
      message.error(errMsg(e, "取数失败"));
    } finally {
      if (!stale()) setRunning(false);
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
      width={preview ? DRAWER.runPreview : DRAWER.run}
      extra={
        preview ? (
          <Space>
            <Button onClick={rerun}>重新运行</Button>
            <Button type="primary" loading={downloading} onClick={confirmDownload}>
              确认下载
            </Button>
          </Space>
        ) : (
          <Button type="primary" loading={running} onClick={() => runAndPreview()}>
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
      {progress && (
        <div style={NOTICE}>
          {progress.attached && "相同参数的取数你刚才已经提交过、还没跑完,已为你接上那一次 · "}
          {progress.text} · 关掉这个抽屉也不影响它跑完,完成后会有通知,也可在「运行记录」里取
          {progress.attached && (
            <Button type="link" size="small" style={{ padding: "0 4px" }} onClick={() => runAndPreview(true)}>
              仍要重新运行
            </Button>
          )}
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
            <span style={SECTION_TITLE}>
              结果预览
              <span style={SECTION_HINT}>
                前 {preview.rows.length} 行 / 共 {preview.row_count} 行,下载为完整结果
              </span>
            </span>
            {job?.executed_sql && (
              <Button type="link" size="small" onClick={() => setShowSql(true)}>
                查看执行SQL
              </Button>
            )}
          </div>
          {job?.reuse_kind === "result" && (
            // 复用的数据不是此刻取的:把「是什么时候的数」摆在表格正上方,够不够新由用户判断
            <div style={{ ...NOTICE, marginBottom: 10 }}>
              复用了{job.reused_from_at ? ` ${fmtTime(job.reused_from_at, false)} ` : "今天"}
              以相同参数跑出的结果,没有重新执行。需要最新数据?
              <Button type="link" size="small" loading={running} onClick={() => runAndPreview(true)}>
                仍要重新运行
              </Button>
            </div>
          )}
          <ResultPreviewTable columns={preview.columns} rows={preview.rows} scrollY={400} />
        </div>
      )}
      {/* 历史运行记录:从前只能从卡片右上角 ⋮ 菜单进,想拿上次跑好的数据的人被迫绕一圈。
          默认收起 —— 收起时 RunRecordsPanel 不发请求,点开卡片的人不为一份可能不看的
          历史多打一次接口;折叠头上的「最后运行」读列表已有的 last_run_at,不额外取数。 */}
      <Collapse
        ghost
        size="small"
        style={{ marginTop: 12 }}
        activeKey={recordsOpen ? ["records"] : []}
        onChange={(k) => toggleRecords((k as string[]).length > 0)}
        items={[
          {
            key: "records",
            label: (
              <span style={SECTION_TITLE}>
                运行记录
                {task?.last_run_at && (
                  <span style={SECTION_HINT}>
                    最后运行 {fmtTime(task.last_run_at, false)} · 可直接预览/导出
                  </span>
                )}
              </span>
            ),
            children: (
              <RunRecordsPanel
                key={task?.id}
                taskId={task?.id ?? null}
                active={recordsOpen}
                variant="compact"
                refreshKey={recordsKey}
              />
            ),
          },
        ]}
      />

      <SqlModal sql={showSql ? job?.executed_sql || "" : null} onClose={() => setShowSql(false)} />
    </Drawer>
  );
}
