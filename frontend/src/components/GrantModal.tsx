import { useEffect, useState } from "react";
import { Button, Checkbox, Modal, Radio, Select, Space, Table, Tag, message } from "antd";
import {
  errMsg,
  grantPermission,
  listPermissions,
  lookupDepartments,
  lookupUsers,
  revokePermission,
} from "../api";

const ALL_ACTIONS = ["view", "run", "download"];

/** 给某个模板授权:列出现有授权 + 添加(按用户/部门)+ 撤销。商分只能开自己的模板;管理员任意。 */
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
  const [subjectType, setSubjectType] = useState<"user" | "department">("user");
  const [subjectId, setSubjectId] = useState<string>();
  const [options, setOptions] = useState<any[]>([]);
  const [actions, setActions] = useState<string[]>(ALL_ACTIONS);

  const loadGrants = () => {
    if (templateId != null) listPermissions(String(templateId)).then(setGrants);
  };

  const search = (q: string) => {
    const fn = subjectType === "user" ? lookupUsers : lookupDepartments;
    fn(q).then((rows: any[]) =>
      setOptions(
        rows.map((r) => ({
          value: String(r.id),
          label: subjectType === "user" ? `${r.name}${r.email ? ` (${r.email})` : ""}` : r.name,
        }))
      )
    );
  };

  // 打开或切换模板时拉取现有授权
  useEffect(() => {
    if (open) loadGrants();
  }, [open, templateId]);

  // 打开或切换主体类型时,重置所选对象并刷新候选列表
  useEffect(() => {
    if (open) {
      setSubjectId(undefined);
      search("");
    }
  }, [open, subjectType]);

  const add = async () => {
    if (!subjectId) return message.warning("请选择授权对象");
    try {
      await grantPermission({
        subject_type: subjectType,
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

  const columns = [
    { title: "对象", dataIndex: "subject_type", render: (t: string, r: any) => (
      <span><Tag>{t === "user" ? "用户" : "部门"}</Tag>#{r.subject_id}</span>
    )},
    { title: "动作", dataIndex: "action", render: (a: string) => <Tag color="blue">{a}</Tag> },
    {
      title: "操作",
      render: (_: any, r: any) => (
        <Button type="link" danger size="small" onClick={() => revokePermission(r.id).then(loadGrants)}>
          撤销
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
          <Radio.Group value={subjectType} onChange={(e) => setSubjectType(e.target.value)}>
            <Radio.Button value="user">按用户</Radio.Button>
            <Radio.Button value="department">按部门</Radio.Button>
          </Radio.Group>
          <Select
            showSearch
            filterOption={false}
            placeholder={subjectType === "user" ? "搜索用户" : "搜索部门"}
            style={{ width: 240 }}
            value={subjectId}
            onSearch={search}
            onChange={setSubjectId}
            options={options}
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
        <Table rowKey="id" size="small" dataSource={grants} columns={columns} pagination={false} />
      </Space>
    </Modal>
  );
}
