import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Button, Card, Checkbox, Empty, message, Modal, Segmented, Select, Space, Tooltip } from "antd";
import {
  AppstoreOutlined,
  DeleteOutlined,
  PlusOutlined,
  UnorderedListOutlined,
} from "@ant-design/icons";
import {
  archiveTemplate,
  errMsg,
  listTasks,
  publishTemplate,
  subscribeTask,
  unsubscribeTask,
} from "../api";
import { hasTeam, isManager as isManagerRole, isPlatformAdmin, useAuth } from "../auth";
import TaskEditor from "../components/TaskEditor";
import RunDrawer from "../components/RunDrawer";
import RunRecordsDrawer from "../components/RunRecordsDrawer";
import GrantModal from "../components/GrantModal";
import SubscribersModal from "../components/SubscribersModal";
import TaskCard from "../components/TaskCard";
import TaskTable from "../components/TaskTable";
import { showIdle, TaskHandlers } from "../components/taskActions";
import { TASK_IDLE, TEMPLATE_STATUS } from "../components/StatusTag";

/** 卡片 / 列表两种视图的选择:存本地,不进 URL。
 *  它是「这个人习惯怎么看」而不是「此刻在看哪一批」—— 筛选与搜索走 ?q= / ?status= 是为了刷新与深链
 *  可复现某一批任务,视图偏好换台机器本就该各自记,也不该被别人点开你的链接时改掉。
 *  键名沿用 rubic_ 前缀(与 rubic_token、取数抽屉的运行记录折叠状态同一套)。 */
const VIEW_KEY = "rubic_tasks_view";
type ViewMode = "card" | "list";

/** 标题栏里两个筛选勾选框的字重/字号 —— 与状态筛选片一样,是次要信息不抢标题 */
const FILTER_CHECK_STYLE = { fontSize: 13, fontWeight: 400, color: "var(--ink-secondary)" };

/** 顶部可点击的状态筛选小片:点击切换只看该状态,再点或点「总数」清除。 */
function StatChip({
  n,
  label,
  active,
  activeBg,
  hint,
  onClick,
}: {
  n: number;
  label: string;
  active: boolean;
  activeBg: string;
  hint?: string;
  onClick: () => void;
}) {
  return (
    <span
      onClick={onClick}
      title={hint}
      style={{
        cursor: "pointer",
        padding: "3px 12px",
        borderRadius: 12,
        background: active ? activeBg : "transparent",
        color: active ? "var(--ink)" : "var(--ink-secondary)",
        fontWeight: active ? 600 : 400,
        transition: ".15s",
        userSelect: "none",
      }}
    >
      <b style={{ color: "var(--ink)" }}>{n}</b> {label}
    </span>
  );
}

/** 空态文案。早返回而不是层层三元:每加一个筛选项就多一层缩进,读的人得数括号。 */
function emptyTextFor({
  rawQ,
  noTeamYet,
  teamFilter,
  mineOnly,
  subOnly,
  idleOnly,
  showRecycle,
}: {
  rawQ: string | null;
  noTeamYet: boolean;
  teamFilter: string | null;
  mineOnly: boolean;
  subOnly: boolean;
  idleOnly: boolean;
  showRecycle: boolean;
}): string {
  const where = showRecycle ? "回收站里" : "";
  if (rawQ?.trim()) return `没有匹配「${rawQ}」的任务`;
  if (noTeamYet) return "你还不属于任何团队 —— 请联系平台管理员把你加入团队后才能新建任务";
  if (teamFilter) return "该团队下暂无任务";
  if (idleOnly) return "没有闲置的任务 —— 已上线的任务最近都有人在跑";
  if (mineOnly && subOnly) return `${where}没有既是你开发、又被你订阅的任务`;
  if (mineOnly) return `${where}没有你开发的任务`;
  if (subOnly) return `${where}没有你订阅的任务`;
  return showRecycle ? "回收站为空" : "暂无任务";
}

export default function TasksPage() {
  const { user } = useAuth();
  // 管理者 = 管理员 / 开发者(与后端 permission_service.can_author 同一口径):
  // 能进编辑器、看回收站、用「我开发的」。注意它**不**回答「能不能动某个任务」——
  // 那一律读服务端算好的 task.can_manage,前端不自己算团队规则。
  const isManager = isManagerRole(user);

  const [tasks, setTasks] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  // 编辑器目标:null=关闭;{id:null}=新建;{id:n}=编辑;带 readOnly=只读查看
  const [editor, setEditor] = useState<{ id: number | null; readOnly?: boolean } | null>(null);
  const [runTarget, setRunTarget] = useState<any>(null);
  const [grantTarget, setGrantTarget] = useState<any>(null);
  // 运行记录抽屉的目标:任务 + 可选的「点名那次运行」(通知深链的 ?job=,用于高亮)。
  // 两者是同一件事的两半,合成一个 state —— 拆成两个就得在每个入口手工配对,
  // 漏配一次就是一个不报错的陈旧高亮
  const [records, setRecords] = useState<{ task: any; jobId: number | null } | null>(null);
  const [subscribersTarget, setSubscribersTarget] = useState<any>(null);
  const [sp, setSp] = useSearchParams();
  // 默认卡片:只有明确选过列表才是列表(读不到/读到脏值都回落卡片)
  const [view, setView] = useState<ViewMode>(() =>
    localStorage.getItem(VIEW_KEY) === "list" ? "list" : "card"
  );
  const changeView = (v: ViewMode) => {
    setView(v);
    localStorage.setItem(VIEW_KEY, v);
  };
  // 回收站视图:同页切换(?recycle=1),只看已下线任务;与顶栏那排筛选片互斥
  const showRecycle = sp.get("recycle") === "1";
  // 闲置阈值(天):服务端下发在每一行上,全局同一个值,取任一行即可;0 = 这项提示关着。
  // 只用来拼一句悬停解释 —— 判定本身在服务端(is_idle),前端不拿它去算。
  // **不给默认值**:写 `?? 90` 就是前端自己推导规则,一个配了 30 天的部署会被那句话骗
  //(而且它恰好在「列表还没加载完 + 深链 ?idle=1」时才露出来,最不容易被发现)
  const idleThreshold: number = tasks[0]?.idle_threshold_days ?? 0;
  // 提示关着时 ?idle=1 一律当没写过:否则深链会筛出一张空列表、空态还说「最近都有人在跑」,
  // 而真相是功能关着;此时筛选片也不渲染,人连退出这个筛选的入口都没有。
  // 列表还没加载完(tasks 为空、阈值无从得知)时先按「开着」算,免得全量先闪一下再收窄。
  const idleUsable = !tasks.length || idleThreshold > 0;
  // 顶栏那一排片是**单选**:此刻在看哪一批,只有这一个答案。
  // 互斥由**读取侧**保证,而不是只靠 selectChip 写的时候删干净 —— 否则一条手敲的
  // ?status=draft&idle=1(或收藏夹里的旧链接)会同时套两个谓词:列表恒空、高亮显示「闲置」、
  // 空态却说「已上线的任务最近都有人在跑」,三者互相矛盾且没人解释得清。
  // 在这里收口,坏 URL 就只是被归一化成一个合法选中态,下游(filtered / 计数 / 空态)
  // 都只看 activeChip 一个值。
  // 闲置 ⊂ 已上线,所以「已上线 + 闲置」同时选本就多余、「草稿 + 闲置」必然是空集,
  // 单选不损失任何能力,却省掉一整套「这两个能不能同时勾」的解释。
  // isManager 兜底同 mineOnly:闲置标记本就只给 can_manage 的人(taskActions.showIdle)。
  const activeChip =
    !showRecycle && isManager && idleUsable && sp.get("idle") === "1"
      ? "idle"
      : sp.get("status"); // null=全部
  const idleOnly = activeChip === "idle";
  // URL 上仍分两个键而不是把 idle 混进 ?status= —— status 是任务的状态字段,
  // idle 是算出来的属性,混成一个枚举以后就会有人拿 ?status=idle 去后端查。
  const selectChip = (key: string | null) => {
    sp.delete("status");
    sp.delete("idle");
    if (key === "idle") sp.set("idle", "1");
    else if (key) sp.set("status", key);
    setSp(sp, { replace: true });
  };
  const toggleRecycle = () => {
    if (showRecycle) sp.delete("recycle");
    else {
      sp.set("recycle", "1");
      sp.delete("status");
      // 已下线的任务不参与闲置判定,带着一个必然为空的筛选进回收站是纯粹的困惑源
      sp.delete("idle");
    }
    setSp(sp, { replace: true });
  };
  // 布尔型 URL 开关(?mine=1 / ?sub=1):勾上写 1,取消删键。与状态/团队筛选同一套约定
  const toggleFlag = (key: string) => () => {
    if (sp.get(key) === "1") sp.delete(key);
    else sp.set(key, "1");
    setSp(sp, { replace: true });
  };
  // 我开发的(?mine=1):口径 = 我建的 + 被授予编辑权的,由服务端逐行算好
  // (task.developed_by_me),前端不自己算。isManager 兜底 —— 普通用户看到的本来就只是
  // 授权给自己的任务,手改 URL 也不生效
  const mineOnly = isManager && sp.get("mine") === "1";
  // 我订阅的(?sub=1):与「我开发的」相互独立,同时勾选即取交集。
  // **不加 isManager 门槛** —— 普通用户也订阅任务,这个筛选对他们同样有用
  const subOnly = sp.get("sub") === "1";
  // 团队筛选(?team=<id>):与 ?q= / ?status= / ?recycle= / ?mine= / ?sub= 同一套约定,纯客户端过滤。
  // 服务端已按团队收窄过一遍,这里只是在「我看得到的那些」里再挑一个团队看。
  const teamFilter = sp.get("team");
  const setTeamFilter = (v: string | null) => {
    if (v) sp.set("team", v);
    else sp.delete("team");
    setSp(sp, { replace: true });
  };

  const load = useCallback(() => {
    setLoading(true);
    listTasks()
      .then(setTasks)
      .finally(() => setLoading(false));
  }, []);
  useEffect(load, []);

  // 从通知深链进来(/tasks?records=<taskId>&job=<jobId>):任务加载后打开对应运行记录抽屉,
  // 并清掉参数。job 可选,用来把通知说的那一次运行高亮出来(定时运行攒了几十期时,
  // 订阅者要的是「本期」那条,不是列表第一行)。
  useEffect(() => {
    const rid = sp.get("records");
    if (!rid || !tasks.length) return;
    const t = tasks.find((x) => String(x.id) === rid);
    if (t) setRecords({ task: t, jobId: Number(sp.get("job")) || null });
    sp.delete("records");
    sp.delete("job");
    setSp(sp, { replace: true });
  }, [tasks]);

  const doArchive = useCallback((row: any) =>
    Modal.confirm({
      title: `下线任务「${row.name}」?`,
      content: "下线后业务用户将不能再运行该任务。",
      onOk: () => archiveTemplate(row.id).then(load),
    }), [load]);

  const doPublish = useCallback((row: any) => {
    const restore = row.status === "archived"; // 回收站里的下线任务:恢复=重新上线
    Modal.confirm({
      title: `${restore ? "重新上线" : "上线"}任务「${row.name}」?`,
      content: `${restore ? "重新上线" : "上线"}后,被授权的业务用户即可运行该任务的最新版本。`,
      okText: restore ? "重新上线" : "上线",
      onOk: () => publishTemplate(row.id, restore ? "回收站重新上线" : "任务列表上线").then(load),
    });
  }, [load]);

  // 顶栏搜索:按 任务名 / 作者 / 被授权人 客户端过滤(大小写不敏感)。
  // 默认视图排除下线(archived)任务;回收站视图则只看下线任务。
  const q = (sp.get("q") ?? "").trim().toLowerCase();
  // 「团队筛选」「我开发的」「我订阅的」都先于状态筛选与统计生效:顶部计数与卡片同源,
  // 筛选后数字不会自相矛盾(这是既有约定,以后新增的筛选也必须并进同一层)
  const scoped = useMemo(() => {
    let rows = tasks;
    if (teamFilter) rows = rows.filter((t) => String(t.team_id) === teamFilter);
    if (mineOnly) rows = rows.filter((t) => t.developed_by_me);
    if (subOnly) rows = rows.filter((t) => t.subscribed);
    return rows;
  }, [tasks, teamFilter, mineOnly, subOnly]);

  // 团队下拉只在「看得到的任务跨越多个团队」时才出现 —— 单团队开发者不该被无意义的下拉打扰
  const teamChoices = useMemo(() => {
    const m = new Map<number, string>();
    for (const t of tasks) if (t.team_id) m.set(t.team_id, t.team_name || `团队#${t.team_id}`);
    return [...m].map(([value, label]) => ({ value: String(value), label }));
  }, [tasks]);
  const filtered = useMemo(() => {
    const matchQ = (t: any) => {
      if (!q) return true;
      if ((t.name || "").toLowerCase().includes(q)) return true;
      if ((t.author_name || "").toLowerCase().includes(q)) return true;
      if ((t.team_name || "").toLowerCase().includes(q)) return true;
      return (t.authorized_users || []).some((u: any) => (u.name || "").toLowerCase().includes(q));
    };
    const rows = scoped.filter((t) =>
      showRecycle
        ? t.status === "archived" && matchQ(t)
        : t.status !== "archived" &&
          matchQ(t) &&
          (idleOnly ? showIdle(t) : !activeChip || t.status === activeChip)
    );
    // 闲置的沉到最后。视觉降噪只是让它不抢眼,沉底才真正把一屏的位置还给还在用的任务 ——
    // 「三年前建的僵尸任务」与「上周建的活跃任务」谁在前面,本来完全取决于建得早晚
    // (后端按 id DESC 给)。sort 是稳定的,所以两组内部仍保持后端给的顺序,
    // 不会顺手打乱既有的「新建在前」;组内也不按闲置程度再排 —— 一个默认顺序不该同时
    // 回答两个问题,要按「闲置最久」看就用闲置筛选片 + 列表视图的时间列升序。
    // 就地 sort 安全:filter() 返回的已经是新数组,改不到 tasks / scoped。
    // 判定走 showIdle 而不是 t.is_idle —— 看不到闲置标记的人,也不该被悄悄换掉顺序。
    // 列表视图里这只是「默认顺序」:点了列头排序后由 AntD 的 sorter 接管,那是使用者的
    // 明确指令,该听他的。
    // 次序补全到「总」:同组内按 id 倒序(= 新建在前)。不写这一条的话,组内顺序
    // 完全靠「后端给的是 id DESC」+「sort 稳定」两个隐含前提撑着 —— 哪天后端把
    // ORDER BY 改成 updated_at,卡片视图的默认顺序会跟着变,而前端一行 diff 都没有。
    return rows.sort((a, b) => Number(showIdle(a)) - Number(showIdle(b)) || b.id - a.id);
  }, [scoped, q, activeChip, idleOnly, showRecycle]);

  const summary = useMemo(() => {
    const s = { published: 0, draft: 0, archived: 0, idle: 0, total: 0 };
    for (const t of scoped) {
      if (t.status === "archived") {
        s.archived++; // 下线任务只进回收站,不计入本页统计
        continue;
      }
      s.total++;
      if (t.status === "published") {
        s.published++;
        // 与卡片读同一个 showIdle:顶栏说「3 个闲置」,列表里就必须正好找得到那 3 个。
        // 它含 can_manage 那道门,所以没有编辑权的人既看不到标记、也不会被算进这个数
        if (showIdle(t)) s.idle++;
      } else if (t.status === "draft") s.draft++;
    }
    return s;
  }, [scoped]);

  // 顶部筛选片:已上线/草稿读共享状态色(tint),「总数」清除筛选。
  // 「闲置」只在真有闲置任务时才插进来 —— 一个恒为 0 的筛选片是噪声,而它本身就是来降噪的;
  // 但 idleOnly 时无论如何都要渲染,否则「筛完只剩 0 个」会把这枚片连同关掉它的唯一入口
  // 一起抹掉,人就困在空列表里了。
  // 没有闲置任务时不占位(一个恒为 0 的片是噪声,而它本身就是来降噪的);但 idleOnly 时
  // 无论如何都要渲染,否则「筛完只剩 0 个」会把这枚片连同关掉它的唯一入口一起抹掉。
  // 提示关着时 idleOnly 已经恒为 false(见 idleUsable),这里不必再判一次阈值。
  // 不叠 isManager:summary.idle 数的是 showIdle(含 can_manage),idleOnly 自己也带了
  // 那道门,再加一层只会让这个闸看起来是按角色开的,而它其实是按权限开的。
  const showIdleChip = summary.idle > 0 || idleOnly;
  const statChips: { key: string | null; label: string; n: number; tint: string; hint?: string }[] = [
    { key: "published", label: "已上线", n: summary.published, tint: TEMPLATE_STATUS.published.tint! },
    { key: "draft", label: "草稿", n: summary.draft, tint: TEMPLATE_STATUS.draft.tint! },
    ...(showIdleChip
      ? [
          {
            key: "idle",
            label: "闲置",
            n: summary.idle,
            tint: TASK_IDLE.tint,
            hint: `已上线但超过 ${idleThreshold} 天没有运行记录的任务(含作者试跑与定时运行)—— 可以考虑下线`,
          },
        ]
      : []),
    { key: null, label: "总数", n: summary.total, tint: "#eef0f7" },
  ];

  // 「开发者但没有团队」= 建不了任务。平台管理员不受团队约束(后端
  // require_can_create_in_team 对他放行、编辑器也会列出全部团队),故不算在内 ——
  // 否则一个不入队的管理员会看到灰按钮和一句不适用的提示。
  const noTeamYet = isManager && !isPlatformAdmin(user) && !hasTeam(user);
  const emptyText = emptyTextFor({
    rawQ: sp.get("q"),
    noTeamYet,
    teamFilter,
    mineOnly,
    subOnly,
    idleOnly,
    showRecycle,
  });

  // 订阅/退订:成功后重拉列表(subscribed / subscriber_count 都由服务端算,不本地改)
  const doSubscribeToggle = useCallback(async (row: any) => {
    try {
      if (row.subscribed) {
        await unsubscribeTask(row.id);
        message.success(`已退订「${row.name}」`);
      } else {
        await subscribeTask(row.id);
        message.success(`已订阅「${row.name}」,${row.schedule_desc || "按计划"}自动运行后会通知你`);
      }
      load();
    } catch (e: any) {
      message.error(errMsg(e, row.subscribed ? "退订失败" : "订阅失败"));
    }
  }, [load]);

  // 身份稳定:TaskTable 的列定义与 onRow 都按 h 记忆,h 每次渲染换新的话
  // 几百行的单元格会跟着白跑一遍(顶栏搜索是逐字符触发渲染的)
  const handlers: TaskHandlers = useMemo(
    () => ({
      onRun: setRunTarget,
      onEdit: (r) => setEditor({ id: r.id }),
      onView: (r) => setEditor({ id: r.id, readOnly: true }),
      onGrant: setGrantTarget,
      onRecords: (r) => setRecords({ task: r, jobId: null }),
      onPublish: doPublish,
      onArchive: doArchive,
      onSubscribeToggle: doSubscribeToggle,
      onSubscribers: setSubscribersTarget,
    }),
    [doPublish, doArchive, doSubscribeToggle]
  );

  // 视图切换对所有人可见(普通用户任务少也照样有人偏好列表);回收站与新建仍限管理者。
  // 图标不能是唯一的信息载体:原生 title 给鼠标、aria-label 给读屏。
  const extra = (
    <Space size={8}>
      <Segmented<ViewMode>
        size="small"
        value={view}
        onChange={changeView}
        options={[
          { value: "card", title: "卡片视图", icon: <AppstoreOutlined aria-label="卡片视图" /> },
          {
            value: "list",
            title: "列表视图(信息更密,可点列头排序)",
            icon: <UnorderedListOutlined aria-label="列表视图" />,
          },
        ]}
      />
      {isManager && (
        <>
          <Tooltip title={showRecycle ? "返回任务列表" : "回收站(已下线任务)"}>
            <Button
              shape="circle"
              icon={<DeleteOutlined />}
              type={showRecycle ? "primary" : "default"}
              onClick={toggleRecycle}
              aria-label="回收站"
            />
          </Tooltip>
          {!showRecycle && (
            // 需求 4 的 UI 兑现:没有团队就建不了任务(后端 require_can_create_in_team 也会拦)
            <Tooltip
              title={
                noTeamYet ? "你还不属于任何团队,请联系平台管理员把你加入团队后再建任务" : undefined
              }
            >
              <Button
                type="primary"
                icon={<PlusOutlined />}
                disabled={noTeamYet}
                onClick={() => setEditor({ id: null })}
              >
                新建任务
              </Button>
            </Tooltip>
          )}
        </>
      )}
    </Space>
  );

  return (
    <Card
      styles={{ body: { paddingTop: 12 } }}
      style={{ borderRadius: 28, minHeight: "calc(100vh - 120px)" }}
      loading={loading && !tasks.length}
      title={
        <Space size={20} align="center">
          <span style={{ fontSize: 22, fontWeight: 700 }}>{showRecycle ? "回收站" : "任务列表"}</span>
          {showRecycle ? (
            <span style={{ fontSize: 13, color: "var(--ink-secondary)" }}>
              <b style={{ color: "var(--ink)" }}>{summary.archived}</b> 个已下线任务
            </span>
          ) : (
            <Space size={8} style={{ fontSize: 13, fontWeight: 400 }}>
              {statChips.map((c) => (
                <StatChip
                  key={c.label}
                  n={c.n}
                  label={c.label}
                  active={activeChip === c.key}
                  activeBg={c.tint}
                  hint={c.hint}
                  onClick={() => selectChip(activeChip === c.key ? null : c.key)}
                />
              ))}
            </Space>
          )}
          {/* 团队下拉只在看得到的任务跨越多个团队时出现:单团队开发者不需要它 */}
          {isManager && teamChoices.length > 1 && (
            <Select
              variant="borderless"
              allowClear
              size="small"
              placeholder="全部团队"
              style={{ minWidth: 130, fontWeight: 400 }}
              value={teamFilter ?? undefined}
              onChange={(v) => setTeamFilter(v ?? null)}
              options={teamChoices}
            />
          )}
          {isManager && (
            <Checkbox checked={mineOnly} onChange={toggleFlag("mine")} style={FILTER_CHECK_STYLE}>
              我开发的
            </Checkbox>
          )}
          <Checkbox checked={subOnly} onChange={toggleFlag("sub")} style={FILTER_CHECK_STYLE}>
            我订阅的
          </Checkbox>
        </Space>
      }
      extra={extra}
    >
      {/* 两种视图消费同一个 filtered:顶部计数、筛选、搜索、空态都只有一份,
          切视图不会让「数字与内容对不上」。空态两种视图共用,不必各画一遍。 */}
      {filtered.length === 0 ? (
        <Empty style={{ padding: "48px 0" }} description={emptyText} />
      ) : view === "list" ? (
        <TaskTable tasks={filtered} h={handlers} />
      ) : (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))",
            gap: 20,
          }}
        >
          {filtered.map((t) => (
            <TaskCard key={t.id} task={t} h={handlers} />
          ))}
        </div>
      )}

      <TaskEditor
        editingId={editor?.id ?? null}
        readOnly={editor?.readOnly}
        open={!!editor}
        onClose={() => setEditor(null)}
        onSaved={load}
      />
      <RunDrawer task={runTarget} open={!!runTarget} onClose={() => setRunTarget(null)} />
      <RunRecordsDrawer
        task={records?.task ?? null}
        highlightJobId={records?.jobId ?? null}
        open={!!records}
        onClose={() => setRecords(null)}
      />
      <GrantModal
        templateId={grantTarget?.id ?? null}
        templateName={grantTarget?.name}
        open={!!grantTarget}
        onClose={() => setGrantTarget(null)}
      />
      <SubscribersModal
        task={subscribersTarget}
        open={!!subscribersTarget}
        onClose={() => setSubscribersTarget(null)}
      />
    </Card>
  );
}
