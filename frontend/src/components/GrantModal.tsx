import { useEffect, useState } from "react";
import { Button, Checkbox, Modal, Select, Space, Table, Tag, message } from "antd";
import {
  errMsg,
  grantPermission,
  listPermissions,
  revokePermission,
} from "../api";
import { useUserPicker } from "./useUserPicker";

const ALL_ACTIONS = ["view", "run", "download"];
// 动作码 → 中文标签(与后端 permissions 的 action 取值一一对应)
const ACTION_LABEL: Record<string, string> = {
  view: "查看",
  run: "运行",
  download: "下载",
};

/** 给某个任务授权:列出现有授权 + 添加(按用户)+ 撤销。
 *  作者只能开自己建的任务;管理员/开发者任意。 */
export default function GrantModal({
  templateId,
  templateName,
  open,
  onClose,
}: {
  templateId: number | null;
  templateName?: string;
  open: boolean;
  onClose: () => void;
}) {
  const [grants, setGrants] = useState<any[]>([]);
  const [subjectId, setSubjectId] = useState<string>();
  const [actions, setActions] = useState<string[]>(ALL_ACTIONS);
  const { options, onSearch, fetchNow, profileOf } = useUserPicker();

  const loadGrants = () => {
    if (templateId != null) listPermissions(String(templateId)).then(setGrants);
  };

  // 打开或切换模板时拉取现有授权
  useEffect(() => {
    if (open) loadGrants();
  }, [open, templateId]);

  // 打开时刷新候选列表。所选对象与勾选动作不必手动重置:调用方每次打开都挂一个新实例
  // (TasksPage 按任务 key 条件渲染),状态天然从初始值开始。
  useEffect(() => {
    if (open) fetchNow("");
  }, [open]);

  const add = async () => {
    if (!subjectId) return message.warning("请选择授权对象");
    const picked = profileOf(subjectId);
    try {
      await grantPermission({
        subject_type: "user",
        // 按 open_id 授权:服务端在此刻才把该用户落库(壳用户不再于搜索时生成)
        subject_open_id: subjectId,
        // 不回传邮箱:服务端会自己从飞书通讯录取(客户端可写 email 是一条提权路径)
        subject_name: picked?.name,
        subject_avatar: picked?.avatar,
        resource_type: "template",
        resource_id: String(templateId),
        actions,
      });
      message.success("已授权");
      loadGrants();
    } catch (e: any) {
      message.error(errMsg(e, "授权失败"));
    }
  };

  // 把扁平的授权记录按用户归并成名单:一人一行,展示其全部权限
  const groups = Object.values(
    grants.reduce((acc: any, p: any) => {
      const key = p.subject_id;
      acc[key] ||= {
        key,
        subject_id: p.subject_id,
        subject_name: p.subject_name,
        actions: [],
        ids: [],
      };
      acc[key].actions.push(p.action);
      acc[key].ids.push(p.id);
      return acc;
    }, {})
  );

  const revokeAll = async (ids: number[]) => {
    await Promise.all(ids.map((id) => revokePermission(id)));
    loadGrants();
  };

  const columns = [
    {
      title: "对象",
      render: (_: any, r: any) => (
        <Space>
          <Tag color="blue">用户</Tag>
          <span>{r.subject_name || `#${r.subject_id}`}</span>
        </Space>
      ),
    },
    {
      title: "拥有权限",
      render: (_: any, r: any) =>
        r.actions.map((a: string) => (
          <Tag key={a} color="green">{ACTION_LABEL[a] ?? a}</Tag>
        )),
    },
    {
      title: "操作",
      width: 90,
      render: (_: any, r: any) => (
        <Button type="link" danger size="small" onClick={() => revokeAll(r.ids)}>
          取消授权
        </Button>
      ),
    },
  ];

  return (
    <Modal
      title={`授权任务:${templateName || ""}`}
      open={open}
      onCancel={onClose}
      footer={<Button onClick={onClose}>关闭</Button>}
      width={620}
    >
      <Space direction="vertical" style={{ width: "100%" }} size="middle">
        <Space wrap>
          <Select
            showSearch
            filterOption={false}
            placeholder="输入姓名/邮箱搜全公司"
            style={{ width: 240 }}
            value={subjectId}
            onSearch={onSearch}
            onChange={setSubjectId}
            options={options}
            notFoundContent="没搜到?可能不在可见范围"
          />
          <Checkbox.Group
            options={ALL_ACTIONS.map((a) => ({ label: ACTION_LABEL[a] ?? a, value: a }))}
            value={actions}
            onChange={(v) => setActions(v as string[])}
          />
          <Button type="primary" onClick={add}>
            授权
          </Button>
        </Space>
        <Table rowKey="key" size="small" dataSource={groups} columns={columns} pagination={false}
          locale={{ emptyText: "尚未授权任何人" }} />
      </Space>
    </Modal>
  );
}
