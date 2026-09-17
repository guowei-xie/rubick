import { useState } from "react";
import { Alert, Modal, Table, Tag, Typography, message } from "antd";
import {
  AuthorTransferCandidate,
  batchTransferTaskAuthor,
  errMsg,
} from "../api";
import { useAuth } from "../auth";
import { byTaskName, personLabel } from "../format";
import StatusTag, { TEMPLATE_STATUS } from "./StatusTag";
import { TASK_ID_COLUMN } from "./TaskIdTag";
import TransferConsequence from "./transferConsequence";

/**
 * 批量转移作者的确认闸口 —— 勾完任务、选定接手人之后的最后一眼。
 *
 * 与单任务的 TransferAuthorModal 是同一件事的两种规模,三句后果的措辞刻意保持一致
 * (原作者仍可见可运行 / 接手人限本团队在职成员 / 双方都会收到通知),**只改人称与数量**。
 *
 * 人称是这里唯一真正的新问题:单任务版用「你 / 原作者」二选一(TransferAuthorModal),
 * 一批任务的原作者可能好几个,那套二元自适应必然说错话。所以这里一律**具名 + 数量**,
 * 通篇不出现「他/她」—— 接手人与原作者的性别都不是我们知道的事。
 */
function formerAuthorsLine(tasks: any[], meId?: number): string {
  const n = tasks.length;
  const ids = [...new Set(tasks.map((t) => t.author_id))];
  if (ids.length === 1) {
    return ids[0] === meId
      ? `转移后，你将不再能编辑这 ${n} 个任务`
      : `转移后，原作者 ${tasks[0].author_name ?? "原作者"} 将不再能编辑这 ${n} 个任务`;
  }
  const names = ids
    .map((id) => tasks.find((t) => t.author_id === id)?.author_name ?? `#${id}`)
    .join("、");
  const mine = tasks.filter((t) => t.author_id === meId).length;
  const tail = mine ? `，其中 ${mine} 个是你的任务` : "";
  return `转移后，这 ${n} 个任务的原作者（共 ${ids.length} 人：${names}）将不再能编辑它们${tail}`;
}

export default function BulkTransferAuthorModal({
  open,
  receiver,
  tasks,
  onClose,
  onDone,
}: {
  open: boolean;
  receiver?: AuthorTransferCandidate;
  /** 选中的完整记录 —— 含当前筛选看不见的那些,清单必须是全的 */
  tasks: any[];
  onClose: () => void;
  onDone: (count: number, receiverName: string) => void;
}) {
  const { user } = useAuth();
  const [busy, setBusy] = useState(false);
  // 整批拒绝时后端回的那段多行说明。留在弹窗里而不是 message.error:
  // antd 的 message 会把换行折掉,而这段话的价值正在于「哪几条、为什么」逐行列着
  const [failure, setFailure] = useState<string>();

  if (!open || !receiver) return null;

  const submit = async () => {
    setBusy(true);
    setFailure(undefined);
    try {
      const r = await batchTransferTaskAuthor(
        receiver.user_id,
        tasks.map((t) => t.id)
      );
      message.success(`已把 ${r.count} 个任务的作者转给 ${r.to_user_name}`);
      onDone(r.count, r.to_user_name);
    } catch (e) {
      // 全成功才生效:失败时一个任务都没被改动,所以不关弹窗、也不清空选中 ——
      // 让人看完原因直接改选再试,而不是回到表格里从头勾一遍
      setFailure(errMsg(e, "批量转移失败"));
    } finally {
      setBusy(false);
    }
  };

  const columns = [
    TASK_ID_COLUMN,
    { title: "任务名", dataIndex: "name", ellipsis: true, sorter: byTaskName },
    {
      title: "状态",
      width: 90,
      render: (_: any, t: any) => <StatusTag map={TEMPLATE_STATUS} value={t.status} />,
    },
    { title: "团队", dataIndex: "team_name", width: 130, ellipsis: true },
    // 原作者逐条列出:一批任务的作者可能各不相同,上面那句话只给得出汇总
    { title: "原作者", dataIndex: "author_name", width: 110, ellipsis: true },
  ];

  return (
    <Modal
      open
      width={720}
      title={`批量转移作者 → ${personLabel(receiver)}`}
      okText={`确认转移 ${tasks.length} 个`}
      okButtonProps={{ disabled: !tasks.length, loading: busy }}
      cancelButtonProps={{ disabled: busy }}
      onOk={submit}
      onCancel={onClose}
      maskClosable={!busy}
    >
      {failure && (
        <Alert
          type="error"
          showIcon
          style={{ marginBottom: 12 }}
          message="本次转移未生效，没有任何任务被改动"
          // pre-line:后端把每条拒绝写成了自成一句的整行,折成一行就读不出是哪几条
          description={<span style={{ whiteSpace: "pre-line" }}>{failure}</span>}
        />
      )}
      <Alert
        type="warning"
        showIcon
        style={{ marginBottom: 12 }}
        message={`${formerAuthorsLine(tasks, user?.id)}，作者将转给 ${receiver.name}`}
        description={
          <>
            <TransferConsequence subject="原作者" pron="本人" plural />
            <br />
            新旧作者各收到<b>一条汇总通知</b>（不是每个任务一条）。
            <br />
            <b>全部成功才生效</b>：只要有一个任务转不了，本次不会有任何任务被改动。
          </>
        }
      />
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        即将转移的任务 <Tag>{tasks.length}</Tag>
      </Typography.Text>
      <Table
        rowKey="id"
        size="small"
        style={{ marginTop: 8 }}
        dataSource={tasks}
        columns={columns}
        pagination={false}
        scroll={{ y: 240 }}
      />
    </Modal>
  );
}
