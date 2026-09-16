import { useEffect, useState } from "react";
import { Alert, Modal, Select, Space, Typography, message } from "antd";
import { TeamMember, errMsg, taskAuthorCandidates, transferTaskAuthor } from "../api";
import { useAuth } from "../auth";
import { personLabel } from "../format";

/**
 * 转移任务作者 —— 离职交接。
 *
 * 候选人**不在前端筛**:「同团队 ∧ 在职 ∧ 不是当前作者」这三条由服务端的
 * /tasks/{id}/author-candidates 算好。在这里复述一遍就是两份会漂移的规则,
 * 而漂移的表现是「下拉里选得到、点了报错」。
 *
 * 入口条件是 task.can_transfer_author,**不是** can_manage —— 被授予该任务编辑权的人
 * 有 can_manage 却不该能处分归属(见后端 permission_service.can_transfer_author)。
 */
export default function TransferAuthorModal({
  task,
  onClose,
  onDone,
}: {
  task: any | null;
  onClose: () => void;
  onDone: () => void;
}) {
  const { user } = useAuth();
  const [candidates, setCandidates] = useState<TeamMember[]>([]);
  const [loading, setLoading] = useState(false);
  const [picked, setPicked] = useState<number>();
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setPicked(undefined);
    if (!task) return;
    setLoading(true);
    taskAuthorCandidates(task.id)
      .then(setCandidates)
      .catch((e) => message.error(errMsg(e, "取候选人失败")))
      .finally(() => setLoading(false));
  }, [task?.id]);

  if (!task) return null;

  // 失去编辑权的是原作者。发起人正好是作者本人时说「你」,管理员代办时说「原作者」——
  // 对着代办的管理员说「你将不再能编辑」是错的
  const byAuthorSelf = task.author_id === user?.id;
  const who = byAuthorSelf ? "你" : "原作者";
  const pron = byAuthorSelf ? "你" : "他";

  const submit = async () => {
    if (!picked) return message.warning("请选择接手人");
    setBusy(true);
    try {
      const r = await transferTaskAuthor(task.id, picked);
      message.success(`已把《${task.name}》的作者转给 ${r.author_name}`);
      // 关闭在这里做,调用点只管刷新 —— 否则每个新调用点都要记得自己补 setTarget(null)
      onClose();
      onDone();
    } catch (e: any) {
      message.error(errMsg(e, "转移失败"));
    } finally {
      setBusy(false);
    }
  };

  const options = candidates.map((m) => ({ value: m.user_id, label: personLabel(m) }));

  return (
    <Modal
      title={`转移作者:${task.name}`}
      open
      onCancel={onClose}
      onOk={submit}
      okText="确认转移"
      okButtonProps={{ disabled: !picked, loading: busy }}
      width={560}
    >
      <Space direction="vertical" style={{ width: "100%" }} size="middle">
        <Alert
          type="warning"
          showIcon
          message={`转移后${who}将不再能编辑此任务`}
          description={
            <>
              {who}仍是团队成员，<b>仍可见、可运行</b>这个任务，但不再有编辑权
              （除非{pron}是本团队的团队管理员）。确有需要时，由团队管理员在
              「任务编辑权」里单独授予一条。
              <br />
              接手人只能是<b>本任务所属团队</b>的在职成员；转移后新旧双方都会收到飞书通知。
            </>
          }
        />
        <div>
          <Typography.Text type="secondary">当前作者：</Typography.Text>
          <Typography.Text strong>{task.author_name || "—"}</Typography.Text>
        </div>
        <Select
          showSearch
          optionFilterProp="label"
          loading={loading}
          placeholder="选择接手人（本团队在职成员）"
          style={{ width: "100%" }}
          value={picked}
          onChange={setPicked}
          options={options}
          notFoundContent={loading ? "加载中…" : "本团队暂无其他可接手的成员"}
        />
      </Space>
    </Modal>
  );
}
