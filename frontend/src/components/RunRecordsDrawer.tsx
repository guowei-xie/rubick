import { Drawer } from "antd";
import RunRecordsPanel from "./RunRecordsPanel";

/** 任务的运行记录,独立抽屉形态:卡片 ⋮ 菜单与通知深链(/tasks?records=<id>)的落点。
 *  表格与三个弹窗都在 RunRecordsPanel 里 —— 取数抽屉的折叠区块用的是同一个组件,
 *  只是 variant 不同,免得两处各维护一套列。 */
export default function RunRecordsDrawer({
  task,
  open,
  onClose,
}: {
  task: any;
  open: boolean;
  onClose: () => void;
}) {
  return (
    <Drawer title={task ? `运行记录:${task.name}` : ""} open={open} onClose={onClose} width={860}>
      <RunRecordsPanel key={task?.id} taskId={task?.id ?? null} variant="full" />
    </Drawer>
  );
}
