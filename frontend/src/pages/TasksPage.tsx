import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Button, Card, Checkbox, Empty, Input, message, Modal, Segmented, Select, Space, Tooltip } from "antd";
import {
  AppstoreOutlined,
  BookOutlined,
  CopyOutlined,
  DeleteOutlined,
  PlusOutlined,
  RobotOutlined,
  SearchOutlined,
  UnorderedListOutlined,
  UserSwitchOutlined,
} from "@ant-design/icons";
import {
  archiveTemplate,
  AuthorTransferCandidate,
  authorTransferCandidates,
  BlockedGroup,
  errMsg,
  getAuthConfig,
  listTasks,
  publishTemplate,
  subscribeTask,
  Task,
  unarchiveTemplate,
  unsubscribeTask,
} from "../api";
import { hasTeam, isManager as isManagerRole, isPlatformAdmin, useAuth } from "../auth";
import { applyShareText } from "../applyLink";
import { copyText } from "../clipboard";
import AgentSkillModal from "../components/AgentSkillModal";
import TaskEditor from "../components/TaskEditor";
import RunDrawer from "../components/RunDrawer";
import RunRecordsDrawer from "../components/RunRecordsDrawer";
import GrantModal from "../components/GrantModal";
import SubscribersModal from "../components/SubscribersModal";
import TransferAuthorModal from "../components/TransferAuthorModal";
import BulkTransferBar from "../components/BulkTransferBar";
import BulkTransferAuthorModal from "../components/BulkTransferAuthorModal";
import TaskCard from "../components/TaskCard";
import TaskTable from "../components/TaskTable";
import { showIdle, TaskHandlers } from "../components/taskActions";
import { TASK_IDLE, TEMPLATE_STATUS } from "../components/StatusTag";
import { idHitFirst, parseTaskQuery, taskMatcher } from "../taskSearch";

/** 卡片 / 列表两种视图的选择:存本地,不进 URL。
 *  它是「这个人习惯怎么看」而不是「此刻在看哪一批」—— 筛选与搜索走 ?q= / ?status= 是为了刷新与深链
 *  可复现某一批任务,视图偏好换台机器本就该各自记,也不该被别人点开你的链接时改掉。
 *  键名沿用 rubic_ 前缀(与 rubic_token、取数抽屉的运行记录折叠状态同一套)。 */
const VIEW_KEY = "rubic_tasks_view";
type ViewMode = "card" | "list";

/** 使用文档(飞书版《用户手册》)。任务列表是所有人的落地页,手册入口就放在这里,
 *  仓库版在 docs/user-manual.md —— 两者内容同步,但飞书版才是给非开发者看的那份。 */
const MANUAL_URL = "https://wrpnn3mat2.feishu.cn/docx/ZlYBdS3fGoBosXx5btpcWs8yn8Y";

/** 筛选排里两个勾选框的字号/颜色 —— 与状态筛选片同一档,是次要信息 */
const FILTER_CHECK_STYLE = { fontSize: 13, color: "var(--ink-secondary)" };

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
  qid,
  noTeamYet,
  teamFilter,
  mineOnly,
  subOnly,
  idleOnly,
  showRecycle,
}: {
  rawQ: string;
  qid: number | null;
  noTeamYet: boolean;
  teamFilter: string | null;
  mineOnly: boolean;
  subOnly: boolean;
  idleOnly: boolean;
  showRecycle: boolean;
}): string {
  const where = showRecycle ? "回收站里" : "";
  // 搜的是个编号却没命中时,多给一句 —— 否则人会以为编号记错了,反复核对一个没错的数字。
  // 真正的原因通常是「这个任务没授权给我」(列表本就只有我看得见的那些)。
  if (rawQ.trim() && qid !== null)
    return `没有编号 ${qid} 的任务 —— 也可能是它没授权给你,列表里只有你看得见的任务`;
  if (rawQ.trim()) return `没有匹配「${rawQ}」的任务`;
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

  const [tasks, setTasks] = useState<Task[]>([]);
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
  const [transferTarget, setTransferTarget] = useState<any>(null);
  // 批量交接模式。**不进 URL**:它是一次未提交的操作过程,不是「此刻在看哪一批」——
  // 把半勾好的选中集做成可深链的状态,只会让别人点开你的链接时进入一个陌生的半成品
  const [bulk, setBulk] = useState(false);
  const [candidates, setCandidates] = useState<AuthorTransferCandidate[]>([]);
  // 与接手人无关的置灰理由(我没有处分权 / 任务无主),服务端只算一次
  const [baseBlocked, setBaseBlocked] = useState<BlockedGroup[]>([]);
  const [candLoading, setCandLoading] = useState(false);
  const [receiverId, setReceiverId] = useState<number>();
  const [selectedKeys, setSelectedKeys] = useState<number[]>([]);
  const [confirming, setConfirming] = useState(false);
  // 飞书应用申请链接(后端 /auth/config 下发,没配就是 null)。给「登不进来的同事」用,
  // 由已经在里面的人复制转发 —— 见下面 extra 里的「申请链接」按钮。
  const [applyUrl, setApplyUrl] = useState<string | null>(null);
  const [skillOpen, setSkillOpen] = useState(false);
  const [sp, setSp] = useSearchParams();
  // 默认卡片:只有明确选过列表才是列表(读不到/读到脏值都回落卡片)
  const [view, setView] = useState<ViewMode>(() =>
    localStorage.getItem(VIEW_KEY) === "list" ? "list" : "card"
  );
  const changeView = (v: ViewMode) => {
    setView(v);
    localStorage.setItem(VIEW_KEY, v);
  };
  // 批量交接期间强制列表(卡片没有勾选列),但**只改这次渲染读的值、不写偏好** ——
  // 退出模式自动回到他原本的视图,不需要任何「记住原视图再恢复」的簿记
  const effectiveView: ViewMode = bulk ? "list" : view;
  // 回收站视图:同页切换(?recycle=1),只看已下线任务;与标题栏那排筛选片互斥
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
  // 标题栏那一排片是**单选**:此刻在看哪一批,只有这一个答案。
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
  // 本页所有 URL 筛选的**唯一**写入口:给一张「键 → 新值」的表,null 即删键,一律 replace
  //(不给返回键留一串中间态)。签名与 analytics/useAnalyticsScope 的 patch 逐字同形 ——
  // 哪天要把这套写法提到共享模块,那是一次搬家,不是一次重新设计。
  //
  // **收多键而不是单键**:互斥筛选(选了状态就得清掉 idle、进回收站要清掉两者)本就是
  // 一次原子的多键写。做成单键的话这些地方只能自己手写一遍 sp.delete,于是「互斥关系」
  // 就散回到几行时序里;收成一张表之后它在一个表达式里读得出来。
  //
  // 复制 sp 而不是原地改:sp 是 useSearchParams 每次渲染给的对象,原地改它等于在渲染期
  // 改一个还要被读的值。useAnalyticsScope 一直是复制的,这里跟上。
  const patch = (next: Record<string, string | null>) => {
    const sp2 = new URLSearchParams(sp);
    for (const [k, v] of Object.entries(next)) v === null ? sp2.delete(k) : sp2.set(k, v);
    setSp(sp2, { replace: true });
  };
  // URL 上仍分两个键而不是把 idle 混进 ?status= —— status 是任务的状态字段,idle 是算出来的
  // 属性,混成一个枚举以后就会有人拿 ?status=idle 去后端查。于是选一枚片 = 一次两键写:
  // 选中的那个置位、另一个清空。互斥由这一个表达式保证,读取侧的 activeChip 再兜一道底。
  const selectChip = (key: string | null) =>
    patch({ status: key === "idle" ? null : key, idle: key === "idle" ? "1" : null });
  // 进回收站要连状态与闲置一起清:已下线的任务不参与闲置判定,
  // 带着一个必然为空的筛选进回收站是纯粹的困惑源
  const toggleRecycle = () =>
    patch(showRecycle ? { recycle: null } : { recycle: "1", status: null, idle: null });
  // 布尔型 URL 开关(?mine=1 / ?sub=1):勾上写 1,取消删键
  const toggleFlag = (key: string) => () =>
    patch({ [key]: sp.get(key) === "1" ? null : "1" });
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
  const setTeamFilter = (v: string | null) => patch({ team: v });
  // 搜索词(?q=)。逐字符写 URL 而不是等回车 —— 结果是即时的,中途停手也能把当前这一屏
  // 的链接发给同事。
  const setQ = (v: string) => patch({ q: v || null });

  const load = useCallback(() => {
    setLoading(true);
    listTasks()
      .then(setTasks)
      .finally(() => setLoading(false));
  }, []);
  useEffect(load, []);

  // 申请链接只有管理者用得上,所以也只替他们取一次;取不到就当没配置,**不弹错** ——
  // 这是个锦上添花的入口,不该为它在任务列表上糊一条红色提示。
  // (Agent Skill 弹窗要的 app_base_url 由它自己在打开时取,不占这条路径)
  useEffect(() => {
    if (!isManager) return;
    getAuthConfig()
      .then((c) => setApplyUrl(c.feishu_apply_url))
      .catch(() => {});
  }, [isManager]);

  // 从通知深链进来(/tasks?records=<taskId>&job=<jobId>):任务加载后打开对应运行记录抽屉,
  // 并清掉参数。job 可选,用来把通知说的那一次运行高亮出来(定时运行攒了几十期时,
  // 订阅者要的是「本期」那条,不是列表第一行)。
  useEffect(() => {
    const rid = sp.get("records");
    if (!rid || !tasks.length) return;
    const t = tasks.find((x) => String(x.id) === rid);
    if (t) setRecords({ task: t, jobId: Number(sp.get("job")) || null });
    patch({ records: null, job: null });
  }, [tasks]);

  // 收进回收站。草稿与已上线是**同一个接口、两套说法**:草稿从来就不可运行,
  // 跟它说「下线后业务用户不能再运行」是句假话,只会让人以为自己弄坏了什么。
  const doArchive = useCallback((row: Task) => {
    const draft = row.status !== "published";
    Modal.confirm({
      title: draft ? `把草稿「${row.name}」移入回收站?` : `下线任务「${row.name}」?`,
      content: draft
        ? "草稿本来就不可运行,移入回收站只是把它从任务列表收起来。之后可以在回收站里恢复为草稿。"
        : "下线后业务用户将不能再运行该任务。",
      okText: draft ? "移入回收站" : "下线",
      onOk: () => archiveTemplate(row.id).then(load),
    });
  }, [load]);

  // 回收站的另一个出口:退回草稿。成功后这一行会**从回收站视图里消失**(它不再是 archived),
  // 所以必须给一句话说明它去哪了 —— 否则看起来像「点了一下任务就没了」。
  const doUnarchive = useCallback((row: Task) =>
    Modal.confirm({
      title: `把「${row.name}」恢复为草稿?`,
      content: "恢复后它回到任务列表的草稿里,业务用户仍不可运行;要对业务开放请用「重新上线」。",
      okText: "恢复为草稿",
      onOk: async () => {
        try {
          await unarchiveTemplate(row.id);
          message.success(`「${row.name}」已恢复为草稿,可在任务列表里继续编辑`);
          load();
        } catch (e: any) {
          message.error(errMsg(e, "恢复为草稿失败"));
        }
      },
    }), [load]);

  const doPublish = useCallback((row: Task) => {
    const restore = row.status === "archived"; // 回收站里的任务:恢复=重新上线
    Modal.confirm({
      title: `${restore ? "重新上线" : "上线"}任务「${row.name}」?`,
      content: `${restore ? "重新上线" : "上线"}后,被授权的业务用户即可运行该任务的最新版本。`,
      okText: restore ? "重新上线" : "上线",
      onOk: () => publishTemplate(row.id, restore ? "回收站重新上线" : "任务列表上线").then(load),
    });
  }, [load]);

  // 列表搜索:按 任务编号(精确) / 任务名 / 作者 / 团队 / 被授权人 客户端过滤(大小写不敏感)。
  // 解析与组合律见 taskSearch,与团队页「任务编辑权」共用一份。
  // 默认视图排除下线(archived)任务;回收站视图则只看下线任务。
  // 不套 useMemo:下游依赖的全是 q / qid 两个**原始值**,没人消费这个对象的引用,
  // 而它本身只是一次 trim + toLowerCase + 一个六字符正则。
  // (反过来说也别把这个对象塞进 useMemo 的依赖数组 —— 它每次渲染都是新的,会让 memo 失效。)
  const rawQ = sp.get("q") ?? "";
  const { text: q, id: qid } = parseTaskQuery(rawQ);
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
    // 组合律(空串放行 / 编号精确 ∪ 文本包含)在 taskSearch 里,这里只说本页搜哪些字段。
    const match = taskMatcher({ text: q, id: qid });
    const matchQ = (t: any) =>
      match(t, [
        t.name,
        t.author_name,
        t.team_name,
        ...(t.authorized_users || []).map((u: any) => u.name),
      ]);
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
    //
    // 最前面还有一层:**按编号精确搜中的那一条置顶**,压过上面的「闲置沉底」。
    // 闲置沉底是我们**替**使用者做的默认降噪;而拿着编号来搜是他**点名**要这一条。
    // 被点名的任务恰好闲置时把它沉到几十条模糊命中之后,等于我们的降噪压过了他的指令 ——
    // 这与上一段「点了列头就该听使用者的」是同一条原则的另一次应用。
    // (它对默认顺序为什么是无害的,见 taskSearch.idHitFirst。)
    return rows.sort(
      (a, b) => idHitFirst(qid, a, b) || Number(showIdle(a)) - Number(showIdle(b)) || b.id - a.id
    );
  }, [scoped, q, qid, activeChip, idleOnly, showRecycle]);

  // ---------------------------------------------------------------- 批量交接作者
  // 能勾什么、为什么勾不了,**全部来自服务端算好的候选集**(authorTransferCandidates):
  // 同团队 ∧ 在职 ∧ 不是当前作者 这三条规则在前端复述一遍就是第二份会漂移的规则,
  // 而漂移的表现是「勾得上、点了报错」。这里只做查表与计数。
  const receiver = useMemo(
    () => candidates.find((c) => c.user_id === receiverId),
    [candidates, receiverId]
  );
  const eligibleIds = useMemo(
    () => new Set(receiver?.eligible_template_ids ?? []),
    [receiver]
  );
  /** 任务 id → 为什么勾不了。顶层那截与接手人无关(我没处分权 / 无主任务),
   *  候选人那截是「他接不了」。两截都由服务端给话,前端只合表、不造句。 */
  const blockedReason = useMemo(() => {
    const m = new Map<number, string>();
    for (const g of [...baseBlocked, ...(receiver?.blocked ?? [])])
      for (const id of g.template_ids) m.set(id, g.message);
    return m;
  }, [baseBlocked, receiver]);

  const loadCandidates = useCallback(() => {
    setCandLoading(true);
    authorTransferCandidates()
      .then((d) => {
        setCandidates(d.candidates);
        setBaseBlocked(d.blocked);
      })
      .catch((e) => message.error(errMsg(e, "取接手人名单失败")))
      .finally(() => setCandLoading(false));
  }, []);

  const exitBulk = useCallback(() => {
    setBulk(false);
    setReceiverId(undefined);
    setSelectedKeys([]);
    setConfirming(false);
    // 候选集一并清掉:下次进来无条件重拉,留着只是白占内存,还会先闪一帧旧名单
    setCandidates([]);
    setBaseBlocked([]);
  }, []);

  const enterBulk = () => {
    setBulk(true);
    setSelectedKeys([]);
    setReceiverId(undefined);
    loadCandidates();
    // 卡片没有勾选列,批量只在列表视图里做。**不写 localStorage** —— 视图偏好是
    // 「这个人习惯怎么看」,不该被一次临时操作改掉;退出后自动回到他原本的卡片视图
    if (view === "card") message.info("批量交接需要逐行勾选，已临时切到列表视图");
  };

  // 换接手人:保留交集,而不是清空。离职交接常要在两个接手人之间比一比,
  // 全清等于让人把十几行重勾一遍;被剔掉的那些如实说一句,不静默丢弃
  const pickReceiver = (id?: number) => {
    setReceiverId(id);
    if (id === undefined) return;
    const next = new Set(candidates.find((c) => c.user_id === id)?.eligible_template_ids ?? []);
    setSelectedKeys((prev) => {
      const kept = prev.filter((k) => next.has(k));
      const dropped = prev.length - kept.length;
      const who = candidates.find((c) => c.user_id === id)?.name ?? "他";
      if (dropped)
        message.warning(
          kept.length
            ? `已选中的任务里有 ${dropped} 个不能转给 ${who}，已自动取消勾选`
            : `已选中的任务都不能转给 ${who}，选中已清空`
        );
      return kept;
    });
  };

  // 勾选保留跨筛选:搜「张三」勾一批、再搜「李四」接着勾,正是交接的用法。
  // 代价是会出现「选中的行现在看不见」,故计数里把它如实说出来(见 BulkTransferBar)
  const selectedSet = useMemo(() => new Set(selectedKeys), [selectedKeys]);
  const filteredIds = useMemo(() => new Set(filtered.map((t) => t.id)), [filtered]);
  const selectedTasks = useMemo(
    () => tasks.filter((t) => selectedSet.has(t.id)),
    [tasks, selectedSet]
  );
  // receiver 为空时 eligibleIds 本就是空集,filter 自然得空数组,不必再加一层守卫
  const visibleEligible = useMemo(
    () => filtered.filter((t) => eligibleIds.has(t.id)),
    [filtered, eligibleIds]
  );
  const hiddenSelectedCount = useMemo(
    () => selectedKeys.reduce((n, k) => (filteredIds.has(k) ? n : n + 1), 0),
    [selectedKeys, filteredIds]
  );

  /** 这一行现在能不能勾,以及不能勾时那句话。**每一句都来自服务端** ——
   *  候选接口返回的三截(顶层 blocked / 他的 eligible / 他的 blocked)恰好覆盖我看得见的
   *  全部任务,所以这里只是查表。落到最后那条兜底,说明服务端的划分与列表不同步了
   *  (有人刚改了团队或成员),如实让他刷新,而不是替服务端猜一个理由。 */
  const decisionOf = useCallback(
    (r: Task) => {
      if (!receiver) return { ok: false, reason: "请先在上方选择接手人" };
      if (eligibleIds.has(r.id)) return { ok: true, reason: `可转给 ${receiver.name}` };
      const why = blockedReason.get(r.id);
      return why
        ? { ok: false, reason: why }
        : { ok: false, reason: "这个任务的归属刚刚变过，请刷新页面后重试" };
    },
    [receiver, eligibleIds, blockedReason]
  );

  // 不套 useMemo:selectedKeys 在依赖里,每勾一行都会换新身份 —— 那个 memo 只会让人
  // 误以为它是稳定的,而 TaskTable 侧本来就不需要它稳定(见那边的说明)
  const bulkProps = bulk
    ? { selectedKeys, onSelectedChange: setSelectedKeys, decisionOf }
    : undefined;

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
        // 与卡片读同一个 showIdle:标题栏说「3 个闲置」,列表里就必须正好找得到那 3 个。
        // 它含 can_manage 那道门,所以没有编辑权的人既看不到标记、也不会被算进这个数
        if (showIdle(t)) s.idle++;
      } else if (t.status === "draft") s.draft++;
    }
    return s;
  }, [scoped]);

  // 标题栏筛选片:已上线/草稿读共享状态色(tint),「总数」清除筛选。
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
    rawQ,
    qid,
    noTeamYet,
    teamFilter,
    mineOnly,
    subOnly,
    idleOnly,
    showRecycle,
  });

  // 订阅/退订:成功后重拉列表(subscribed / subscriber_count 都由服务端算,不本地改)
  const doSubscribeToggle = useCallback(async (row: Task) => {
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
  // 几百行的单元格会跟着白跑一遍(搜索框是逐字符触发渲染的)
  const handlers: TaskHandlers = useMemo(
    () => ({
      onRun: setRunTarget,
      onEdit: (r) => setEditor({ id: r.id }),
      onView: (r) => setEditor({ id: r.id, readOnly: true }),
      onGrant: setGrantTarget,
      onRecords: (r) => setRecords({ task: r, jobId: null }),
      onPublish: doPublish,
      onArchive: doArchive,
      onUnarchive: doUnarchive,
      onSubscribeToggle: doSubscribeToggle,
      onSubscribers: setSubscribersTarget,
      onTransferAuthor: setTransferTarget,
    }),
    [doPublish, doArchive, doUnarchive, doSubscribeToggle]
  );

  // 视图切换对所有人可见(普通用户任务少也照样有人偏好列表);回收站与新建仍限管理者。
  // 图标不能是唯一的信息载体:原生 title 给鼠标、aria-label 给读屏。
  const extra = (
    <Space size={8}>
      <Tooltip title="使用文档(飞书文档,新标签页打开)">
        <Button
          type="text"
          icon={<BookOutlined />}
          href={MANUAL_URL}
          target="_blank"
          rel="noopener noreferrer"
          style={{ color: "var(--ink-secondary)" }}
        >
          使用文档
        </Button>
      </Tooltip>
      {/* Agent Skill:弹出安装指令面板,可顺手签一枚 API Token 附进去。
          设计理由见 AgentSkillModal 的 docstring。 */}
      <Tooltip title="获取一句安装指令,粘贴给你的 AI Agent 即可自动安装(rubick-skill);可一并附上你的 API Token,Agent 装完就能取数">
        <Button
          type="text"
          icon={<RobotOutlined />}
          onClick={() => setSkillOpen(true)}
          style={{ color: "var(--ink-secondary)" }}
        >
          Agent Skill
        </Button>
      </Tooltip>
      {/* 申请链接:发给还没有飞书应用权限、因而卡在登录页外面的同事 —— 他连登录页的按钮都
          点不通,平台这边看到的只是「他没登录」,唯一能帮上忙的就是把这条链接发过去。
          门控 isManager:「把人拉进来」与新建 / 授权是同一类职能。
          没配 applyUrl(或还没取回来)就整个不渲染,而不是留一个点了没反应的按钮。
          文案只写「申请链接」不写「复制」:与旁边「使用文档」齐平,动作由图标与 Tooltip 说。 */}
      {isManager && applyUrl && (
        <Tooltip title="复制飞书应用的申请链接,发给还没有权限、登不进来的同事(复制的是一整句,粘进聊天框即可)">
          <Button
            type="text"
            icon={<CopyOutlined />}
            style={{ color: "var(--ink-secondary)" }}
            onClick={async () => {
              // 成功与否由 copyText 返回(它为什么必须返回布尔,见 clipboard.ts):
              // **不许无条件报成功**;失败也要把链接念出来,否则用户没有第二条路。
              (await copyText(applyShareText(applyUrl)))
                ? message.success("已复制申请链接,粘给需要权限的同事即可")
                : message.error(`复制失败,请手动复制:${applyUrl}`);
            }}
          >
            申请链接
          </Button>
        </Tooltip>
      )}
      {/* 批量交接:门不是光 isManager,还要**手上确实有能处分的任务** —— 一个没有任何
          可转移任务的开发者不该看到一个点进去必然空手而归的入口。判据用服务端下发的
          can_transfer_author(与 ⋮ 菜单那一项同一个布尔),用 tasks 而不是 filtered,
          否则按钮会随搜索逐字符闪现 */}
      {isManager && !bulk && tasks.some((t) => t.can_transfer_author) && (
        <Tooltip title="多选任务，一次把作者转给同一个接手人（离职交接）">
          <Button type="text" icon={<UserSwitchOutlined />} onClick={enterBulk}>
            批量交接
          </Button>
        </Tooltip>
      )}
      {/* 批量模式下置灰而不是藏起来:静默切走视图会让人以为自己的偏好被改了。
          偏好其实没动,退出后自动回到原视图(见 effectiveView) */}
      <Tooltip title={bulk ? "批量交接需要逐行勾选，已临时切到列表视图" : undefined}>
        <Segmented<ViewMode>
          size="small"
          value={effectiveView}
          disabled={bulk}
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
      </Tooltip>
      {isManager && (
        <>
          <Tooltip title={showRecycle ? "返回任务列表" : "回收站(已下线的任务与收起来的草稿)"}>
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
        <Space size={16} align="center">
          <span style={{ fontSize: 22, fontWeight: 700 }}>{showRecycle ? "回收站" : "任务列表"}</span>
          {showRecycle ? (
            <span style={{ fontSize: 13, color: "var(--ink-secondary)" }}>
              {/* 不说「已下线」:回收站里也有从未上线过、被作者收起来的草稿 */}
              <b style={{ color: "var(--ink)" }}>{summary.archived}</b> 个任务在回收站
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
        </Space>
      }
      extra={extra}
    >
      {/* 筛选排。搜索框排头,后面跟着团队 / 我开发的 / 我订阅的 —— 它们是同一类东西
          (都只过滤这一页、都写进 URL),放一起才不必解释「这个框管的是哪张列表」。
          此前搜索框在全局顶栏里:它只服务这一页,却在其余每个页面上照样渲染,
          在那些页面里敲第一个字符就会把人弹到 /tasks(而且框里显示的永远是空的)。
          放在 BulkTransferBar **之前**:交接横幅里那句「用上方搜索框搜离职同事的姓名」
          说的就是它。 */}
      <Space size={16} align="center" wrap style={{ marginBottom: 16 }}>
        {/* placeholder 要盖住 matchQ 的**全部**覆盖面(编号 / 任务名 / 作者 / 被授权人 / 团队名)——
            搜得到却没人知道能这么搜,等于没做。
            「编号」摆在最前:它是唯一能精确定位到一条的搜法,也是卡片上那个一键复制的去处
            (复制给的是纯数字,粘进来直接就能搜;写 #128 也认)。 */}
        <Input
          allowClear
          value={rawQ}
          onChange={(e) => setQ(e.target.value)}
          prefix={<SearchOutlined style={{ color: "var(--icon-muted)" }} />}
          placeholder="搜索编号 / 任务 / 人 / 团队"
          style={{ width: 320 }}
        />
        {/* 团队下拉只在看得到的任务跨越多个团队时出现:单团队开发者不需要它 */}
        {isManager && teamChoices.length > 1 && (
          <Select
            allowClear
            placeholder="全部团队"
            style={{ minWidth: 150 }}
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
      {bulk && (
        <BulkTransferBar
          candidates={candidates}
          loading={candLoading}
          picked={receiverId}
          onPick={pickReceiver}
          selectedCount={selectedKeys.length}
          hiddenSelectedCount={hiddenSelectedCount}
          eligibleVisibleCount={visibleEligible.length}
          blockedGroups={receiver?.blocked ?? []}
          onSelectAll={() =>
            setSelectedKeys((prev) => [
              ...new Set([...prev, ...visibleEligible.map((t) => t.id)]),
            ])
          }
          onSubmit={() => setConfirming(true)}
          onExit={exitBulk}
        />
      )}
      {/* 两种视图消费同一个 filtered:顶部计数、筛选、搜索、空态都只有一份,
          切视图不会让「数字与内容对不上」。空态两种视图共用,不必各画一遍。 */}
      {filtered.length === 0 ? (
        <Empty style={{ padding: "48px 0" }} description={emptyText} />
      ) : effectiveView === "list" ? (
        <TaskTable tasks={filtered} h={handlers} hitId={qid} bulk={bulkProps} />
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
        // 代订阅/移除会改变订阅人数,而它印在卡片与表格的计划标签上(taskActions.scheduleLabel);
        // 不重拉列表就会停在旧数字。同 TransferAuthorModal onDone 的理由。
        onDone={load}
      />
      <TransferAuthorModal
        task={transferTarget}
        onClose={() => setTransferTarget(null)}
        // 必须重拉列表:作者本人转出后 can_manage / can_transfer_author / developed_by_me
        // 全都翻转,不重拉他还看得到「编辑」入口,点进去才 403
        onDone={load}
      />
      <BulkTransferAuthorModal
        open={confirming}
        receiver={receiver}
        tasks={selectedTasks}
        onClose={() => setConfirming(false)}
        // 成功后必须退出模式:一次转走 N 个任务,这 N 行的 can_manage /
        // can_transfer_author / developed_by_me 全部翻转,候选集也整体作废,
        // 留在模式里只会给出一张已经说谎的表(同上面那条注释,批量下更强)
        onDone={() => {
          exitBulk();
          load();
        }}
      />
      <AgentSkillModal open={skillOpen} onClose={() => setSkillOpen(false)} />
    </Card>
  );
}
