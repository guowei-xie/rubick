import { useCallback, useMemo } from "react";
import { Button, Dropdown, Table, TableColumnType, Tag } from "antd";
import { ClockCircleOutlined, MoreOutlined, WarningOutlined } from "@ant-design/icons";
import StatusTag, {
  CREDENTIAL_STATUS,
  TEMPLATE_STATUS,
  templateStatusRank,
} from "./StatusTag";
import { byTaskName, dash } from "../format";
import { TASK_ID_COLUMN } from "./TaskIdTag";
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

/** 行样式类名的取值。见下方 rowClassName。 */
const ROW_IDLE = "rk-row-idle";
const ROW_HIT = "rk-row-hit";
const ROW_IDLE_HIT = "rk-row-idle rk-row-hit";
/** 批量交接模式下「这次转不了」的行。与 rk-row-idle 同色不加深:闲置说「没人用」,
 *  blocked 说「这次转不了」,两者可能同时成立,两级告警会把它们读成一件事。 */
const ROW_BLOCKED = "rk-row-blocked";
const CURSOR_NO = { cursor: "not-allowed" };

/** 记录整行替换才算变了(load() → setTasks 是唯一的写入路径,排序与筛选都不改记录本身),
 *  所以行内容只随记录身份失效。少了这一句,rc-table 会在每次父级渲染时重跑所有单元格的
 *  render —— 任务列表的搜索框是逐字符写 URL 的,几百行时每敲一个字就白跑几千次。 */
const shouldCellUpdate = (next: any, prev: any) => next !== prev;

/** 任务列表的「列表模式」:与卡片模式(TaskCard)消费同一份 filtered、同一套 handlers,
 *  差别只在密度 —— 卡片一屏十几个,表格一屏几十行。两种视图都要一致的动作与口径见 taskActions。
 *
 *  悬停提示一律用**原生 title** 而不是 Tooltip:行本身挂着「点击填参取数」的原生 title,
 *  子元素自带原生 title 才能盖住它;混用 Tooltip 会两个提示一起弹。
 *
 *  只排序、不分页:任务再多也是一页看完,顶部的状态计数与筛选就仍是「全部」的口径,
 *  不必再解释「这一页 20 条 / 总共 137 条」。 */
/** 批量交接模式下表格额外需要的东西。**独立于 h 传入,不进 TaskHandlers** ——
 *  h 被 columns 的 useMemo 依赖着,把随勾选变化的东西塞进去,每勾一行都会让整表列定义
 *  重建(TasksPage 里那条 useMemo 的注释说的就是这件事)。
 *  本对象每次勾选都会换新身份,这没关系:它只影响行级 props(onRow / rowClassName /
 *  rowSelection),单元格仍被 shouldCellUpdate 挡着。 */
export interface TaskTableBulk {
  selectedKeys: number[];
  onSelectedChange: (keys: number[]) => void;
  /** 这一行现在能不能勾,以及不能勾时那句话。**理由来自服务端**,前端不复述规则 */
  decisionOf: (r: any) => { ok: boolean; reason: string };
}

export default function TaskTable({
  tasks,
  h,
  hitId,
  bulk,
}: {
  tasks: any[];
  h: TaskHandlers;
  /** 按 ID 精确搜时命中的那一条,给它整行高亮。见下方 rowClassName 的理由。 */
  hitId?: number | null;
  /** 给了它就进入批量交接模式:多一列勾选,整行点击从「取数」改为「勾选」。 */
  bulk?: TaskTableBulk;
}) {
  const columns: TableColumnType<any>[] = useMemo(() => {
    const cols: TableColumnType<any>[] = [
      TASK_ID_COLUMN,
      {
        title: "任务名",
        dataIndex: "name",
        width: 260,
        ellipsis: true,
        sorter: byTaskName,
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
          r.subscribe_enabled || r.allow_api ? (
            <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
              {r.subscribe_enabled && (
                <span
                  title={scheduleHint(r)}
                  // 已订阅换品牌色,一眼分清「任务可订」与「我订了」
                  style={r.subscribed ? { color: "var(--brand)", fontWeight: 600 } : undefined}
                >
                  <ClockCircleOutlined style={{ marginRight: 4 }} />
                  {scheduleLabel(r)}
                </span>
              )}
              {/* 「允许 API」小标签:与卡片视图的 chip 同一语义,颜色与运行记录的「API」来源标签一致 */}
              {r.allow_api && (
                <Tag
                  color="cyan"
                  style={{ marginRight: 0 }}
                  title="允许 API 调用:持有 API Token 的用户可通过开放 API 触发本任务运行"
                >
                  API
                </Tag>
              )}
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

  /** 闲置行整行退一档墨色(样式见 global.css 的 .rk-row-idle);按 ID 搜中的那一行复用
   *  深链高亮 .rk-row-hit(它的语义本就是「被点名的那一行」)。
   *
   *  **为什么高亮要存在**:默认顺序下 ID 命中那条会被置顶(见 TasksPage 的 sort),但一点
   *  列头排序就由 AntD 的 sorter 接管、置顶失效 —— 那是使用者的明确指令,不该去对抗它。
   *  顺序回答「先看哪条」,颜色回答「就是这条」,后者不受排序影响,正好补上那个洞。
   *  卡片视图**不加**这个高亮,不是漏做:卡片没有 sorter,置顶从未失效过。
   *
   *  useCallback 只是跟着同文件 onRow 的写法,别指望它省下重渲 —— antd 每次渲染都会另建
   *  一个 internalRowClassName 包一层传给 rc-table,这里的引用稳不稳它都看不见。 */
  const rowClassName = useCallback(
    // 四个常量而不是每行拼模板串:结果只有这四种,而本函数每次渲染都会对每一行重跑一遍。
    // hitId 至多命中一行,先判它还能替绝大多数行省掉一次 showIdle 调用。
    (r: any) => {
      // 批量模式下「转不了」压过其它档:此刻人在找「哪些能勾」,闲置与深链高亮都让位
      if (bulk && !bulk.decisionOf(r).ok) return ROW_BLOCKED;
      const idle = showIdle(r);
      if (r.id === hitId) return idle ? ROW_IDLE_HIT : ROW_HIT;
      return idle ? ROW_IDLE : "";
    },
    [hitId, bulk]
  );

  /** 批量模式下整行点击改为「勾选」:一行几十像素宽,只点 16px 的方框在几十行上很难受。
   *  悬停提示也跟着换成「为什么勾不了」—— 此刻点击不再取数,继续挂「点击填参取数」就是假话。
   *  依赖里带上 bulk(它随勾选换新)只影响行级 props;单元格仍被 shouldCellUpdate 挡着,
   *  不会跟着重跑 render。 */
  const onRow = useCallback(
    (r: any) => {
      if (bulk) {
        const d = bulk.decisionOf(r);
        return {
          onClick: () => {
            if (!d.ok) return;
            const keys = bulk.selectedKeys;
            bulk.onSelectedChange(
              keys.includes(r.id) ? keys.filter((k) => k !== r.id) : [...keys, r.id]
            );
          },
          title: d.reason,
          style: d.ok ? CURSOR_RUN : CURSOR_NO,
        };
      }
      return {
        onClick: () => r.can_run && h.onRun(r),
        title: runHint(r),
        style: r.can_run ? CURSOR_RUN : CURSOR_PLAIN,
      };
    },
    [h, bulk]
  );

  /** 勾选列必须交给 AntD 注入,**不能自己加一列**:本文件给每一列都挂了
   *  shouldCellUpdate(记录没换就不重渲),自建的勾选列会被它冻住 —— 选中变了而记录对象
   *  没变,表现是「勾不出勾」。AntD 的选择列走 rc-table 内部路径,不经过我们那份 columns。
   *  fixed:左钉,理由同操作列的右钉:scroll x 下横滚后勾不到是致命的。 */
  const rowSelection = bulk && {
    fixed: true as const,
    columnWidth: 44,
    selectedRowKeys: bulk.selectedKeys,
    onChange: (keys: React.Key[]) => bulk.onSelectedChange(keys.map(Number)),
    getCheckboxProps: (r: any) => ({ disabled: !bulk.decisionOf(r).ok }),
  };

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
      rowSelection={rowSelection || undefined}
    />
  );
}
