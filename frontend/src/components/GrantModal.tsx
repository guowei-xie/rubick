import { useEffect, useRef, useState } from "react";
import { Avatar, Button, Checkbox, Modal, Select, Space, Table, Tag, message } from "antd";
import {
  errMsg,
  grantPermission,
  listPermissions,
  lookupUsers,
  revokePermission,
} from "../api";

const ALL_ACTIONS = ["view", "run", "download"];

/** 给某个模板授权:列出现有授权 + 添加(按用户)+ 撤销。商分只能开自己的模板;管理员任意。 */
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
  const [options, setOptions] = useState<any[]>([]);
  const [actions, setActions] = useState<string[]>(ALL_ACTIONS);

  const loadGrants = () => {
    if (templateId != null) listPermissions(String(templateId)).then(setGrants);
  };

  const runFetch = (q: string) => {
    lookupUsers(q).then((rows: any[]) =>
      setOptions(
        rows.map((r) => ({
          value: String(r.id),
          // 供 showSearch 兜底过滤 & 选中后回填文本用
          title: [r.name, r.email].filter(Boolean).join(" "),
          label: (
            <Space size={6}>
              <Avatar size={20} src={r.avatar}>{(r.name || "?").slice(0, 1)}</Avatar>
              <span>{r.name}</span>
              {r.email && <span style={{ color: "#999" }}>{r.email}</span>}
            </Space>
          ),
        }))
      )
    );
  };
  // 输入框搜索防抖:避免每敲一个字就打一次飞书/DB(live 搜索尤其贵)
  const searchTimer = useRef<any>(null);
  const debouncedSearch = (q: string) => {
    if (searchTimer.current) clearTimeout(searchTimer.current);
    searchTimer.current = setTimeout(() => runFetch(q), 350);
  };

  // 打开或切换模板时拉取现有授权
  useEffect(() => {
    if (open) loadGrants();
  }, [open, templateId]);

  // 打开时重置所选对象并刷新候选列表
  useEffect(() => {
    if (open) {
      setSubjectId(undefined);
      runFetch("");
    }
  }, [open]);

  const add = async () => {
    if (!subjectId) return message.warning("请选择授权对象");
    try {
      await grantPermission({
        subject_type: "user",
        subject_id: subjectId,
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
      render: (_: any, r: any) => r.actions.map((a: string) => <Tag key={a} color="green">{a}</Tag>),
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
      title={`授权模板:${templateName || ""}`}
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
            onSearch={debouncedSearch}
            onChange={setSubjectId}
            options={options}
            notFoundContent="没搜到?可能不在可见范围"
          />
          <Checkbox.Group
            options={ALL_ACTIONS.map((a) => ({ label: a, value: a }))}
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
