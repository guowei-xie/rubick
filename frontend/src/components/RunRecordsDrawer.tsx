import { Drawer } from "antd";
import RunRecordsPanel from "./RunRecordsPanel";
import { DRAWER } from "../widths";

/** 任务的运行记录,独立抽屉形态:卡片 ⋮ 菜单与通知深链(/tasks?records=<id>&job=<jid>)的落点。
 *  表格与三个弹窗都在 RunRecordsPanel 里 —— 取数抽屉的折叠区块用的是同一个组件,
 *  只是 variant 不同,免得两处各维护一套列。
 *  highlightJobId 由深链带来(见 TasksPage),用来把通知说的那次运行标出来。 */
export default function RunRecordsDrawer({
  task,
  open,
  onClose,
  highlightJobId,
}: {
  task: any;
  open: boolean;
  onClose: () => void;
  highlightJobId?: number | null;
}) {
  return (
    <Drawer
      title={task ? `运行记录:${task.name}` : ""}
      open={open}
      onClose={onClose}
      width={DRAWER.records}
    >
      <RunRecordsPanel
        key={task?.id}
        taskId={task?.id ?? null}
        variant="full"
        highlightJobId={highlightJobId}
      />
    </Drawer>
  );
}
