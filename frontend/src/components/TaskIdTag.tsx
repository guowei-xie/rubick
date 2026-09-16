import { memo } from "react";
import { TableColumnType, message } from "antd";
import { CopyOutlined } from "@ant-design/icons";
import { copyText } from "../clipboard";

/**
 * 任务编号 `#128` + 一键复制。四处共用一份(卡片 / 列表 / 编辑器标题 / 团队页任务编辑权),
 * 理由同 taskActions:同一个东西写四遍,迟早写成四种样子(有的复制带 #、有的不带)。
 *
 * **显示带 #、复制纯数字**:光秃秃一个 128 读不出那是什么(与审计页 `任务 #12` 同一写法),
 * 但它要被粘进顶栏搜索框 / 工单 / 聊天框,带 # 每次都得手删一下。
 *
 * 冒泡拦在本组件内而不是四个调用点各写一遍 —— 漏一处就是「点编号却打开了取数抽屉」。
 *
 * 用原生 `<button>` 而不是 span+role+tabIndex:键盘可达与 Enter/Space 激活由浏览器给,
 * 不必自己维护一份(Space 该在 keyup 触发、要不要 preventDefault 掉滚动……)。
 * 样式全在 global.css 的 .rk-idtag 里(含 button 复位):内联 style 的优先级压得过类选择器,
 * 混写会让 :hover 的换色静默失效。
 *
 * 提示用**原生 title** 而不是 Tooltip:宿主(任务卡片、表格行)挂着 runHint 的原生 title,
 * 只有子元素的原生 title 盖得住它;混用会两个提示一起弹(TaskCard / TaskTable 的既有约定)。
 *
 * memo:卡片视图既不分页也没 memo(TasksPage 直接 map filtered),顶栏搜索又是逐字符写 URL,
 * 几百张卡时每敲一个字这一枚小标签会被重建几百次。props 全是原始值,浅比较 100% 有效。
 */
function TaskIdTag({ id }: { id: number }) {
  return (
    <button
      type="button"
      className="rk-idtag"
      title={`任务 ID ${id} —— 点击复制编号,粘进顶栏搜索框可直接定位这个任务`}
      onClick={async (e) => {
        e.stopPropagation();
        // 失败也要把数字念出来,否则用户没有第二条路(选中复制在卡片上会触发整卡的点击)
        (await copyText(String(id)))
          ? message.success(`已复制任务 ID ${id}`)
          : message.error(`复制失败,请手动记下任务 ID:${id}`);
      }}
    >
      #{id}
      <CopyOutlined />
    </button>
  );
}

export default memo(TaskIdTag);

/**
 * 任务表格里的「ID」列。任务列表与团队页「任务编辑权」共用同一份 ——
 * 宽度是从 TaskIdTag 的字号 + `#` + 复制图标反推来的,抄两份就会在改字号时漂移,
 * 而这次改动的全部卖点恰恰是「全站同一个编号」。
 *
 * ⚠️ render **只读 r**:TaskTable 给所有列统一挂了 `shouldCellUpdate`(只比较记录),
 * 搜索词变了不会重渲。所以「搜中了没有」绝不能读进这个闭包(会渲出陈旧的样子),
 * 必须走 rowClassName —— antd 每次渲染都重算它。
 *
 * 默认顺序本就是 id 倒序(新建在前),第一次点先给降序 = 把默认顺序显式化。
 */
export const TASK_ID_COLUMN: TableColumnType<any> = {
  title: "ID",
  dataIndex: "id",
  width: 92,
  render: (_: any, r: any) => <TaskIdTag id={r.id} />,
  sorter: (a: any, b: any) => a.id - b.id,
  sortDirections: ["descend", "ascend"],
};
