import { Avatar, Button, Tooltip } from "antd";
import { PlusOutlined } from "@ant-design/icons";

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
  onArchive: (r: any) => void;
  onSubscribeToggle: (r: any) => void; // 订阅/退订(按 r.subscribed 二态)
  onSubscribers: (r: any) => void; // 订阅者名单与订阅记录(can_manage)
};

/** 卡片/行内的点击不该冒泡成「取数」。菜单项与弹层虽在 DOM 上走 portal,
 *  但在 React 树里仍是本卡/本行的子节点,合成事件照样冒泡,所以要拦在宿主之前。 */
export const stop = (e: React.MouseEvent) => e.stopPropagation();

/** ⋮ 菜单项:运行记录(所有人)+ 订阅相关 + 管理项(仅 can_manage)/只读查看(仅 can_view_detail)。
 *  动作动词三分:已上线→下线、已下线→重新上线、草稿→上线。 */
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
      r.status === "published"
        ? { key: "archive", label: "下线", danger: true, onClick: () => h.onArchive(r) }
        : {
            key: "publish",
            label: r.status === "archived" ? "重新上线" : "上线",
            onClick: () => h.onPublish(r),
          }
    );
    if (r.subscribe_enabled) {
      items.push({ key: "subscribers", label: "订阅者名单", onClick: () => h.onSubscribers(r) });
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
