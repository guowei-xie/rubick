import { Avatar, Button, Tooltip } from "antd";
import { PlusOutlined } from "@ant-design/icons";

import { fmtTime } from "../format";
import { TASK_IDLE } from "./StatusTag";

/** 任务在两种视图(卡片 TaskCard / 列表 TaskTable)里必须一致的那一份:动作、口径、共用小部件。
 *
 * 判定都带 can_manage / can_subscribe / subscribe_enabled 的条件分支,两边各写一份迟早漂移
 * ——「卡片里能退订、列表里不能」这种。故凡是两种视图都要给出同一个结果的东西都放这里;
 * 只影响某一种视图的密度取舍(色点 vs 文字标签、说明单独占行 vs 挂悬停)留在各自文件里。
 *
 * 这里**只搬运服务端算好的布尔**,不自己推导权限规则 —— 与 TaskOut 的既有约定一致。
 */

export type TaskHandlers = {
  onRun: (r: any) => void;
  onEdit: (r: any) => void;
  onView: (r: any) => void; // 只读打开同一个编辑器(can_view_detail 但无 can_manage 的团队内部人)
  onGrant: (r: any) => void;
  onRecords: (r: any) => void;
  onPublish: (r: any) => void;
  onArchive: (r: any) => void; // 收进回收站(已上线叫「下线」、草稿叫「移入回收站」)
  onUnarchive: (r: any) => void; // 回收站 → 草稿(另一个出口是 onPublish 的「重新上线」)
  onSubscribeToggle: (r: any) => void; // 订阅/退订(按 r.subscribed 二态)
  onSubscribers: (r: any) => void; // 订阅者名单与订阅记录(can_manage)
  // 转移作者(离职交接)。门是 can_transfer_author 而**不是** can_manage:
  // 被授予该任务编辑权的人有 can_manage,却不该能处分归属
  onTransferAuthor: (r: any) => void;
};

/** 卡片/行内的点击不该冒泡成「取数」。菜单项与弹层虽在 DOM 上走 portal,
 *  但在 React 树里仍是本卡/本行的子节点,合成事件照样冒泡,所以要拦在宿主之前。 */
export const stop = (e: React.MouseEvent) => e.stopPropagation();

/** 生命周期动作(仅 can_manage)。**三个状态各自给全出口**,而不是「上线/下线」二选一:
 *
 *  - 已上线 → 下线
 *  - 草稿   → 上线 / 移入回收站
 *  - 回收站 → 恢复为草稿 / 重新上线
 *
 *  草稿也能进回收站:任务不可删,一个废弃的草稿否则只能永远留在列表里。而回收站里给两个
 *  出口是因为进去之后**无法区分**它原本是草稿还是已上线(status 只有一列,
 *  published_version_id 在下线时已清空),所以由操作者说了算,而不是替他猜 ——
 *  猜错的代价是把一个半成品直接放给业务用户。
 *
 *  进出的每一种都是独立的审计动作码(task_archive / task_restore / task_unarchive)。 */
function lifecycleItems(r: any, h: TaskHandlers): any[] {
  if (r.status === "published") {
    return [{ key: "archive", label: "下线", danger: true, onClick: () => h.onArchive(r) }];
  }
  if (r.status === "archived") {
    return [
      { key: "unarchive", label: "恢复为草稿", onClick: () => h.onUnarchive(r) },
      { key: "publish", label: "重新上线", onClick: () => h.onPublish(r) },
    ];
  }
  return [
    { key: "publish", label: "上线", onClick: () => h.onPublish(r) },
    // 草稿从未上线过,说「下线」是假话:按去处命名
    { key: "archive", label: "移入回收站", danger: true, onClick: () => h.onArchive(r) },
  ];
}

/** ⋮ 菜单项:运行记录(所有人)+ 订阅相关 + 管理项(仅 can_manage)/只读查看(仅 can_view_detail)。
 *  生命周期动作按状态整组给出,见 lifecycleItems。 */
export function taskMenuItems(r: any, h: TaskHandlers): any[] {
  const items: any[] = [{ key: "records", label: "运行记录", onClick: () => h.onRecords(r) }];
  // 已订阅的人永远能退订(哪怕任务已下线/权限被撤);未订阅的按服务端算好的 can_subscribe 显示
  if (r.subscribed) {
    items.push({ key: "unsubscribe", label: "退订", onClick: () => h.onSubscribeToggle(r) });
  } else if (r.can_subscribe) {
    items.push({ key: "subscribe", label: "订阅本任务", onClick: () => h.onSubscribeToggle(r) });
  }
  if (r.can_manage) {
    items.push(
      { type: "divider" },
      { key: "edit", label: "编辑", onClick: () => h.onEdit(r) },
      ...lifecycleItems(r, h)
    );
    if (r.subscribe_enabled) {
      items.push({ key: "subscribers", label: "订阅者名单", onClick: () => h.onSubscribers(r) });
    }
    // 归属变更,不是日常编辑动作:另起一组放最后,免得跟「编辑/上下线」混在一起误点。
    // 条件是服务端下发的独立布尔位 —— 用 can_manage 会把被授予编辑权的人也放进来
    if (r.can_transfer_author) {
      items.push(
        { type: "divider" },
        { key: "transfer-author", label: "转移作者", onClick: () => h.onTransferAuthor(r) }
      );
    }
  } else if (r.can_view_detail) {
    // 团队内部人但对这个任务没有编辑权:给一个只读入口。没有它,他在列表里看得见这个任务,
    // 却没有任何办法看到它到底怎么写的 —— 想参考同事的写法只能去问人。
    // else if 而非另起一个 if:有编辑权的人不该同时看到「编辑」与「查看」两个入口。
    items.push(
      { type: "divider" },
      { key: "view", label: "查看", onClick: () => h.onView(r) }
    );
  }
  return items;
}

/** 「点一下是不是就能取数」的悬停解释。点不动时必须说清是哪一种原因。 */
export function runHint(r: any): string {
  if (r.can_run) return "点击填参取数";
  return r.status !== "published" ? "任务未上线,暂不可取数" : "未授权,暂不可取数";
}

/** 列表里显示哪个时间,以及它是哪一种。 */
export function taskTimeMeta(r: any): { value: string; label: string } {
  if (r.last_run_at) return { value: r.last_run_at, label: "最后运行" };
  if (r.updated_at) return { value: r.updated_at, label: "最后编辑" };
  return { value: r.created_at, label: "创建于" };
}

/** 该不该挂「缺取数账号」告警。是一条策略(给谁看、哪种账号状态算跑不动),
 *  所以条件与文案一起放这里:业务用户看一堆自己修不了的红字只会造成困扰(他们点运行时会拿到
 *  指名团队的报错),而「已配置但没点过测试连接」的账号照样能跑,不该挂告警。 */
export function showCredentialWarn(r: any): boolean {
  return !!r.can_manage && r.credential_ready === false;
}

export function credentialWarnText(r: any): string {
  return `团队${r.team_name ? `《${r.team_name}》` : ""}尚未登记该数据源的取数账号 —— 该任务当前无法运行,请联系团队管理员`;
}

/** 该不该把这一行标成「闲置」。**判定在服务端(r.is_idle),这里只决定给谁看** ——
 *  与 showCredentialWarn 同一策略位:只给 can_manage 的人。三条理由:
 *  ① 闲置提示唯一的下一步是「下线」,而只有 can_manage 的人做得了那个动作;
 *  ② 对业务使用者它不只是无用还有害 —— 一个季度/年度才跑一次的任务被标成「闲置 300 天」、
 *     还被排到列表最后,读起来像「这份数据过时了」,而事实上他就是那个一年来跑一次的人;
 *  ③ can_manage 的覆盖面恰好对:管理员 / 团队管理员 / 作者本人 / 被授予编辑权的人,
 *     正是会做「清理没人用的任务」这件事的那批人。
 *  排序、卡片、列表、顶栏计数、筛选五处全读这一个函数 ——
 *  否则会出现「顶栏说 3 个闲置、列表里只找得到 1 个」。 */
export function showIdle(r: any): boolean {
  return !!r.can_manage && r.is_idle === true;
}

/** 闲置任务在卡头 / 时间列里显示的那行字。与 taskTimeMeta 的分工:那一句回答
 *  「最后一次是什么时候」(04-02),这一句回答「到今天有多久」(闲置 167 天)——
 *  同一个时间的两种读法,不是两个事实,所以**取代**日期而不是并排:并排既要占两段位置
 *  (280px 的卡头还要放状态点、⋮ 与可能出现的「缺取数账号」胶囊),又要读的人自己心算,
 *  而那个换算恰恰是这个提示的全部价值。日期退进悬停(idleHint)里。
 *  天数由服务端 idle_days 给,前端不许自己再算一遍。 */
export function idleLabel(r: any): string {
  return `闲置 ${r.idle_days} 天`;
}

/** 闲置的悬停解释。必须说清三件事:量的是什么、门槛多少、下一步做什么 ——
 *  否则「闲置 167 天」读起来像一句指责,而不是一条可以处理的线索。
 *  「从未运行」要单独说:对一个从来没跑过的任务,「最后一次运行在…」是假话,
 *  而且「上线后就没人跑过」与「跑过但没人跑了」的处理方式本来就不一样。 */
export function idleHint(r: any): string {
  // 从未运行时把 taskTimeMeta 那一档降级(最后编辑 / 创建于)也交代出来 ——
  // 否则一个「昨天刚编辑过、但一直没人跑」的任务,悬停里只剩「从未运行过」,
  // 而「还有人在维护」恰恰是决定要不要下线时最该看到的一条
  const { value, label } = taskTimeMeta(r);
  const head = r.last_run_at
    ? `最后一次运行在 ${fmtTime(r.last_run_at, false)}`
    : `上线至今从未运行过(天数从创建时间起算;${label} ${fmtTime(value, false)})`;
  return (
    `${head};已超过 ${r.idle_threshold_days} 天没有运行记录(含作者试跑与定时运行)。` +
    "确认没人再用的话,可以在 ⋮ 菜单里下线"
  );
}

/** 卡片卡头 / 列表时间列里那一格该显示什么:正常时是时间,闲置时换成「闲置 167 天」。
 *  **这个判断只写一次** —— 它正是本文件存在的理由(见文件头注释):两个视图各写一份
 *  if/else,迟早一边改了措辞另一边没改。历史上就已经漂过一次:同样是非闲置的悬停,
 *  卡片给的是不带秒的时间、列表给的是带秒的,谁也说不出为什么。现在统一成带秒(悬停本就
 *  该比正文多给一点信息,否则它和正文一字不差)。
 *  留给各视图自己决定的只有包法:卡片用 Tooltip,列表必须用原生 title(行上挂着 runHint
 *  的原生 title,只有子元素的原生 title 盖得住它)。 */
export function taskTimeCell(r: any): { text: string; hint: string; idle: boolean } {
  if (showIdle(r)) return { text: idleLabel(r), hint: idleHint(r), idle: true };
  const { value, label } = taskTimeMeta(r);
  return { text: fmtTime(value, false), hint: `${label} · ${fmtTime(value)}`, idle: false };
}

/** 闲置那一格的字怎么长。与 taskTimeCell 同住一处:文案统一了,样式再各写一份就白统一了。
 *  加粗的灰字而不是彩色胶囊 —— 卡头本就可能有一枚橙色「缺取数账号」,再来一枚彩的
 *  会变成两个抢眼的告警,而闲置不是故障。 */
export const IDLE_TEXT: React.CSSProperties = { color: TASK_IDLE.dot, fontWeight: 600 };

/** 定时运行标签的文字与悬停说明。计划描述由后端拼好(schedule_desc),前端不自己算频次语义;
 *  「已订阅」与计划本身二选一,订阅人数只在有人订时才缀上。 */
export function scheduleLabel(r: any): string {
  const head = r.subscribed ? "已订阅" : r.schedule_desc || "可订阅";
  return r.subscriber_count > 0 ? `${head} · ${r.subscriber_count}` : head;
}

export function scheduleHint(r: any): string {
  return (
    `定时运行:${r.schedule_desc || ""} · ${r.subscriber_count || 0} 人订阅` +
    (r.subscribed ? "(含你)" : "")
  );
}

/** 被授权运行的人的头像组。最多露 3 个,再多折成 +N。
 *  姓名怎么给分两种:卡片上逐个 Tooltip;列表里行本身挂着原生 title(点击取数的解释),
 *  逐个 Tooltip 会与它叠着弹,故整格用一句原生 title 一次给全(wrapperTitle)。 */
export function AuthorizedAvatars({ users, wrapperTitle }: { users: any[]; wrapperTitle?: string }) {
  const group = (
    <Avatar.Group max={{ count: 3, style: { background: "#8a90a6", fontSize: 12 } }} size={24}>
      {users.map((u) =>
        wrapperTitle ? (
          <Avatar key={u.id} size={24} src={u.avatar || undefined}>
            {(u.name || "?").slice(0, 1)}
          </Avatar>
        ) : (
          <Tooltip key={u.id} title={u.name}>
            <Avatar size={24} src={u.avatar || undefined}>{(u.name || "?").slice(0, 1)}</Avatar>
          </Tooltip>
        )
      )}
    </Avatar.Group>
  );
  return wrapperTitle ? <span title={wrapperTitle}>{group}</span> : group;
}

/** 「授权用户」按钮。can_manage 这道门与它的样子一起放这里 —— 两种视图各写一遍,
 *  改成「已下线的任务不给授权」时就会漏掉一处。 */
export function GrantButton({ task: r, h }: { task: any; h: TaskHandlers }) {
  if (!r.can_manage) return null;
  return (
    <Button
      shape="circle"
      size="small"
      icon={<PlusOutlined />}
      title="授权用户"
      aria-label="授权用户"
      onClick={() => h.onGrant(r)}
      style={{ borderStyle: "dashed", color: "#8a90a6" }}
    />
  );
}
