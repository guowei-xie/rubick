import { useCallback, useMemo } from "react";
import { Button, Dropdown, Table, TableColumnType } from "antd";
import { ClockCircleOutlined, MoreOutlined, WarningOutlined } from "@ant-design/icons";
import StatusTag, {
  CREDENTIAL_STATUS,
  TEMPLATE_STATUS,
  templateStatusRank,
} from "./StatusTag";
import { dash } from "../format";
import {
  AuthorizedAvatars,
  credentialWarnText,
  GrantButton,
  IDLE_TEXT,
  runHint,
  scheduleHint,
  scheduleLabel,
  showCredentialWarn,
  showIdle,
  stop,
  TaskHandlers,
  taskMenuItems,
  taskTimeCell,
  taskTimeMeta,
} from "./taskActions";

/** 列多,窄屏宁可横向滚动,也不要把列挤成两行(操作列另用 fixed 钉住,见下)。 */
const SCROLL = { x: "max-content" } as const;
const CURSOR_RUN = { cursor: "pointer" };
/** 点不动的行。**不叫 CURSOR_IDLE** —— 本文件里的 rk-row-idle 说的是「闲置」
 *  (长期没人运行),与「点不动」是两件事,同名两个 idle 迟早被读混。 */
const CURSOR_PLAIN = { cursor: "default" };

/** 排序器里现建 collator 会按次比较重建一份;整表排一次是上千次比较,建一次就够。 */
const ZH = new Intl.Collator("zh");

/** 记录整行替换才算变了(load() → setTasks 是唯一的写入路径,排序与筛选都不改记录本身),
 *  所以行内容只随记录身份失效。少了这一句,rc-table 会在每次父级渲染时重跑所有单元格的
 *  render —— 顶栏搜索是逐字符写 URL 的,几百行时每敲一个字就白跑几千次。 */
const shouldCellUpdate = (next: any, prev: any) => next !== prev;

/** 任务列表的「列表模式」:与卡片模式(TaskCard)消费同一份 filtered、同一套 handlers,
 *  差别只在密度 —— 卡片一屏十几个,表格一屏几十行。两种视图都要一致的动作与口径见 taskActions。
 *
 *  悬停提示一律用**原生 title** 而不是 Tooltip:行本身挂着「点击填参取数」的原生 title,
 *  子元素自带原生 title 才能盖住它;混用 Tooltip 会两个提示一起弹。
 *
 *  只排序、不分页:任务再多也是一页看完,顶部的状态计数与筛选就仍是「全部」的口径,
 *  不必再解释「这一页 20 条 / 总共 137 条」。 */
export default function TaskTable({ tasks, h }: { tasks: any[]; h: TaskHandlers }) {
  const columns: TableColumnType<any>[] = useMemo(() => {
    const cols: TableColumnType<any>[] = [
      {
        title: "任务名",
        dataIndex: "name",
        width: 260,
        ellipsis: true,
        sorter: (a: any, b: any) => ZH.compare(a.name || "", b.name || ""),
        render: (_: any, r: any) => (
          <span
            style={{ display: "inline-flex", alignItems: "center", gap: 6, maxWidth: "100%" }}
            // 说明是次级信息,不单独占一列(列表模式先要密度);悬停在任务名上看得到
            title={r.description ? `${r.name}\n\n${r.description}` : r.name}
          >
            <span style={{ fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis" }}>
              {r.name}
            </span>
            {showCredentialWarn(r) && (
              <WarningOutlined
                title={credentialWarnText(r)}
                aria-label="缺取数账号"
                style={{ color: CREDENTIAL_STATUS.unconfigured.dot, flexShrink: 0 }}
              />
            )}
          </span>
        ),
      },
      {
        title: "状态",
        dataIndex: "status",
        width: 96,
        // 表格里状态用文字标签而不是卡片上的色点:一列本就有位置写字,
        // 且与站内其它表格(用户/团队/审计)的读法一致
        render: (s: string) => <StatusTag map={TEMPLATE_STATUS} value={s} />,
        sorter: (a: any, b: any) => templateStatusRank(a.status) - templateStatusRank(b.status),
      },
      { title: "数据源", dataIndex: "datasource_name", width: 130, ellipsis: true, render: dash },
      { title: "团队", dataIndex: "team_name", width: 130, ellipsis: true, render: dash },
      {
        title: "计划",
        dataIndex: "schedule_desc",
        width: 150,
        ellipsis: true,
        render: (_: any, r: any) =>
          r.subscribe_enabled ? (
            <span
              title={scheduleHint(r)}
              // 已订阅换品牌色,一眼分清「任务可订」与「我订了」
              style={r.subscribed ? { color: "var(--brand)", fontWeight: 600 } : undefined}
            >
              <ClockCircleOutlined style={{ marginRight: 4 }} />
              {scheduleLabel(r)}
            </span>
          ) : (
            dash(null)
          ),
        sorter: (a: any, b: any) => (a.subscriber_count || 0) - (b.subscriber_count || 0),
      },
      { title: "作者", dataIndex: "author_name", width: 110, ellipsis: true, render: dash },
      {
        title: "被授权",
        width: 120,
        render: (_: any, r: any) => {
          const users: any[] = r.authorized_users || [];
          if (!users.length) return dash(null);
          return (
            <span onClick={stop}>
              <AuthorizedAvatars
                users={users}
                wrapperTitle={`被授权运行:${users.map((u) => u.name).join("、")}`}
              />
            </span>
          );
        },
      },
      {
        title: "时间",
        width: 150,
        // 这一格显示什么(时间 / 闲置几天)、悬停说什么,都由 taskTimeCell 定,与卡片共用
        // 一份 —— 否则一列裸时间没人知道量的是什么,两个视图还会各自漂。
        // 悬停必须是**原生 title**:行上挂着 runHint 的原生 title,只有子元素的原生 title
        // 盖得住它,换 Tooltip 会两个一起弹。
        render: (_: any, r: any) => {
          const c = taskTimeCell(r);
          return (
            <span title={c.hint} style={c.idle ? IDLE_TEXT : undefined}>
              {c.text}
            </span>
          );
        },
        // 后端给的是 ISO 字符串,字典序即时间序 —— 不必上 collator
        sorter: (a: any, b: any) => {
          const x = taskTimeMeta(a).value || "";
          const y = taskTimeMeta(b).value || "";
          return x < y ? -1 : x > y ? 1 : 0;
        },
        sortDirections: ["descend", "ascend"] as const,
      },
      {
        title: "操作",
        width: 88,
        // 窄屏横向滚动时把操作列钉在右边:⋮(运行记录/上下线)与授权 + 是这一行最常用的两个
        // 入口,不该是「先横滚到底才点得到」的那一列
        fixed: "right" as const,
        render: (_: any, r: any) => (
          <span onClick={stop} style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
            <Dropdown
              menu={{ items: taskMenuItems(r, h) }}
              trigger={["click"]}
              placement="bottomRight"
            >
              <Button type="text" size="small" icon={<MoreOutlined />} style={{ color: "#8a90a6" }} />
            </Dropdown>
            <GrantButton task={r} h={h} />
          </span>
        ),
      },
    ];
    return cols.map((c) => ({ ...c, shouldCellUpdate }));
  }, [h]);

  /** 闲置行整行退一档墨色(样式见 global.css 的 .rk-row-idle)。
   *  useCallback 只是跟着同文件 onRow 的写法,别指望它省下重渲 —— antd 每次渲染都会另建
   *  一个 internalRowClassName 包一层传给 rc-table,这里的引用稳不稳它都看不见。 */
  const rowClassName = useCallback((r: any) => (showIdle(r) ? "rk-row-idle" : ""), []);

  const onRow = useCallback(
    (r: any) => ({
      onClick: () => r.can_run && h.onRun(r),
      title: runHint(r),
      style: r.can_run ? CURSOR_RUN : CURSOR_PLAIN,
    }),
    [h]
  );

  return (
    <Table
      rowKey="id"
      size="middle"
      dataSource={tasks}
      columns={columns}
      pagination={false}
      scroll={SCROLL}
      onRow={onRow}
      rowClassName={rowClassName}
    />
  );
}
