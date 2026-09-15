import { useEffect, useState } from "react";
import { Button, message, Modal, Space, Table, TableColumnType, Tooltip } from "antd";
import { downloadJob, errMsg, previewJob, taskRunRecords, withBase } from "../api";
import StatusTag, { JOB_SOURCE, JOB_STATUS } from "./StatusTag";
import ResultPreviewTable from "./ResultPreviewTable";
import SqlModal from "./SqlModal";
import { fmtDayTime, fmtDuration, fmtTime } from "../format";
import { MODAL } from "../widths";

/** 表格里的空值占位,与「参数/执行SQL 没有内容」用同一个灰破折号,免得同一行里三种写法。 */
const EMPTY = <span style={{ color: "#ccc" }}>—</span>;

/** 两种形态的列宽预算列在一处 —— 要判断这几列塞不塞得进某个宽度,得把它们加起来看
 *  (compact 现为 510,full 约 830),散在各列的三元里加不动。 */
const WIDTHS = {
  full: { source: 70, time: 170, status: undefined, rows: 80, dur: 90, params: 90, result: 150 },
  compact: { source: 58, time: 92, status: 64, rows: 58, dur: 76, params: 62, result: 100 },
};

type RunRecordColumn = TableColumnType<any> & { hideInCompact?: boolean };

/** 任务的运行记录:谁、何时、参数、状态、结果预览/导出。
 *
 * 可见口径是**团队**不是角色:平台管理员与**该任务所属团队的成员**看这个任务下所有人的运行,
 * 其余(含别的团队的开发者)只看自己发起的 —— 见 permission_service.is_insider /
 * job_visibility_condition。别把它记成「开发者与作者看全部」:别团队的开发者一条都看不到,
 * 而被加进团队的普通用户看得到全部(团队成员身份不受 users.role 约束)。
 *
 * 两种呈现:full = ⋮ 菜单/通知深链打开的独立抽屉,列全;compact = 取数抽屉里的折叠区块,
 * 那儿窄得多,砍掉「运行人」「执行SQL」两列并压窄其余列。两者的抽屉宽度都跟着视口走
 * (见 widths.ts),所以**别再把某个像素值写进判断里**。
 * **砍列纯粹是宽度取舍,不是权限边界** —— 后端 GET /tasks/{id}/jobs 照样把 executed_sql
 * 返给业务用户,同一个人从卡片 ⋮ 进 full 形态就能看到这两列。要真不给看,得在
 * task_run_records 里按 is_insider 抹掉,不能靠前端少画一列。
 *
 * active 是「此刻真的看得见」——折叠着的区块不该白打一次接口,所以取数抽屉每开一次
 * 并不等于要拉一次记录;refreshKey 变化即重拉,供刚跑完一次的宿主把新记录顶上来。
 * 换任务由宿主传 key 触发重挂载(rows 与三个弹窗一起清干净),这里不自己清。 */
export default function RunRecordsPanel({
  taskId,
  active = true,
  variant = "full",
  refreshKey = 0,
  highlightJobId = null,
}: {
  taskId: number | null;
  active?: boolean;
  variant?: "full" | "compact";
  refreshKey?: number;
  /** 通知深链点名的那次运行:标出来,免得订阅者在几十期里找「本期」是哪条 */
  highlightJobId?: number | null;
}) {
  const [rows, setRows] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [previewData, setPreviewData] = useState<any>(null);
  const [sqlText, setSqlText] = useState<string | null>(null);
  const [paramsData, setParamsData] = useState<any>(null);
  const compact = variant === "compact";
  const w = WIDTHS[variant];

  useEffect(() => {
    if (!active || !taskId) return;
    setLoading(true);
    taskRunRecords(taskId)
      .then(setRows)
      .finally(() => setLoading(false));
  }, [taskId, active, refreshKey]);

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

  // 列只定义一份,窄抽屉里要藏的那两列自己带 hideInCompact —— 两套列各写一份迟早会漂移
  const columns: RunRecordColumn[] = [
    {
      title: "类型",
      dataIndex: "source",
      width: w.source,
      render: (s: string) => <StatusTag map={JOB_SOURCE} value={s || "run"} />,
    },
    { title: "运行人", dataIndex: "user_name", width: 100, hideInCompact: true },
    {
      title: "时间",
      dataIndex: "created_at",
      width: w.time,
      // 窄抽屉里省掉年份,完整时间挂悬停
      render: (t: string) =>
        compact ? <Tooltip title={fmtTime(t)}>{fmtDayTime(t)}</Tooltip> : fmtTime(t, false),
    },
    {
      title: "状态",
      dataIndex: "status",
      width: w.status,
      render: (s: string) => <StatusTag map={JOB_STATUS} value={s} />,
    },
    {
      title: "行数",
      dataIndex: "row_count",
      width: w.rows,
      render: (n: number | null) => n ?? EMPTY,
    },
    {
      // 只有跑成功的行才有耗时(失败/排队中恒为空)。标题挂说明:它量的是
      // 「查询执行到结果落盘」这一段,不含排队等待 —— 排了半小时队却看到「12 秒」
      // 的人,不解释一句一定会读成「系统在骗我」。
      title: <Tooltip title="查询执行到结果落盘的时长,不含在队列里排队等待的时间">耗时</Tooltip>,
      dataIndex: "duration_ms",
      width: w.dur,
      render: (ms: number | null) => (ms == null ? EMPTY : fmtDuration(ms)),
    },
    {
      title: "参数",
      width: w.params,
      render: (_: any, r: any) => {
        const n = Object.keys(r.params || {}).length;
        return n ? (
          <Button type="link" size="small" onClick={() => setParamsData(r.params)}>查看({n})</Button>
        ) : (
          EMPTY
        );
      },
    },
    {
      title: "执行SQL",
      width: 80,
      hideInCompact: true,
      render: (_: any, r: any) =>
        r.executed_sql ? (
          <Button type="link" size="small" onClick={() => setSqlText(r.executed_sql)}>查看</Button>
        ) : (
          EMPTY
        ),
    },
    {
      title: "结果",
      width: w.result,
      render: (_: any, r: any) => {
        if (r.status !== "success") return <span style={{ color: "#999" }}>-</span>;
        if (r.result_expired) return <span style={{ color: "#999" }}>已过期</span>;
        return (
          <Space size={compact ? 0 : 8}>
            <Button type="link" size="small" onClick={() => preview(r.id)}>预览</Button>
            <Button type="link" size="small" onClick={() => download(r.id)}>导出</Button>
          </Space>
        );
      },
    },
  ];

  return (
    <>
      <Table
        rowKey="id"
        size="small"
        loading={loading}
        dataSource={rows}
        columns={columns
          .filter((c) => !(compact && c.hideInCompact))
          .map(({ hideInCompact, ...c }) => c)}
        // 宁可横向滚动,也不要把「导出」挤成两行。两种形态都留着这个兜底:
        // 列宽预算是定数,而抽屉宽度现在跟着视口走(见 widths.ts),
        // 窄窗口下 full 形态同样可能排不下 —— 旧代码「full 本就排得下」的前提
        // 建立在那个已经不存在的固定 860 上。
        scroll={{ x: "max-content" }}
        rowClassName={(r: any) => (r.id === highlightJobId ? "rk-row-hit" : "")}
        locale={{ emptyText: "还没有运行记录" }}
      />

      <Modal
        title="结果预览(前 50 行)"
        open={!!previewData}
        onCancel={() => setPreviewData(null)}
        footer={null}
        width={MODAL.preview}
      >
        {previewData && (
          <>
            <div style={{ marginBottom: 8, color: "#888" }}>共 {previewData.row_count} 行</div>
            <ResultPreviewTable columns={previewData.columns} rows={previewData.rows} scrollY={400} />
          </>
        )}
      </Modal>

      <SqlModal sql={sqlText} onClose={() => setSqlText(null)} />

      <Modal
        title="本次运行参数"
        open={!!paramsData}
        onCancel={() => setParamsData(null)}
        footer={null}
        width={MODAL.params}
      >
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
    </>
  );
}
