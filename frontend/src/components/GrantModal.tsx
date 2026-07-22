import { useEffect, useRef, useState } from "react";
import { Button, Checkbox, Modal, Radio, Select, Space, Table, Tag, Tooltip, message } from "antd";
import { SyncOutlined } from "@ant-design/icons";
import {
  errMsg,
  grantPermission,
  listPermissions,
  lookupDepartments,
  lookupUsers,
  revokePermission,
  syncContacts,
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
  const [query, setQuery] = useState("");
  const [syncing, setSyncing] = useState(false);

  const loadGrants = () => {
    if (templateId != null) listPermissions(String(templateId)).then(setGrants);
  };

  const runFetch = (q: string) => {
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
  const search = (q: string) => {
    setQuery(q);
    runFetch(q);
  };
  // 输入框搜索防抖:避免每敲一个字就打一次飞书/DB(live 搜索尤其贵)
  const searchTimer = useRef<any>(null);
  const debouncedSearch = (q: string) => {
    setQuery(q);
    if (searchTimer.current) clearTimeout(searchTimer.current);
    searchTimer.current = setTimeout(() => runFetch(q), 350);
  };

  // 同步飞书通讯录目录(供授权搜索用;不影响「用户管理」——那边只显示登录过的)
  const doSync = async () => {
    setSyncing(true);
    const hide = message.loading("正在同步飞书通讯录…", 0);
    try {
      const r = await syncContacts();
      hide();
      message.success(`同步完成:部门 ${r.departments} 个,用户 ${r.users} 人`);
      search(query); // 用当前关键词重搜
    } catch (e: any) {
      hide();
      message.error(errMsg(e, "同步失败(检查飞书应用是否已开通通讯录读取、可见范围是否为全员)"));
    } finally {
      setSyncing(false);
    }
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

  // 把扁平的授权记录按"主体"归并成名单:一人/一部门一行,展示其全部权限
  const groups = Object.values(
    grants.reduce((acc: any, p: any) => {
      const key = `${p.subject_type}:${p.subject_id}`;
      acc[key] ||= {
        key,
        subject_type: p.subject_type,
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
          <Tag color={r.subject_type === "user" ? "blue" : "purple"}>
            {r.subject_type === "user" ? "用户" : "部门"}
          </Tag>
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
          <Radio.Group value={subjectType} onChange={(e) => setSubjectType(e.target.value)}>
            <Radio.Button value="user">按用户</Radio.Button>
            <Radio.Button value="department">按部门</Radio.Button>
          </Radio.Group>
          <Select
            showSearch
            filterOption={false}
            placeholder={subjectType === "user" ? "输入姓名/邮箱搜全公司" : "搜索部门"}
            style={{ width: 240 }}
            value={subjectId}
            onSearch={debouncedSearch}
            onChange={setSubjectId}
            options={options}
            notFoundContent={
              subjectType === "user" ? "没搜到?可能通讯录未同步或不在可见范围" : "无匹配部门"
            }
          />
          {subjectType === "user" && (
            <Tooltip title="从飞书同步通讯录目录(需应用已开通通讯录读取、可见范围设为全员)。同步的人不会进「用户管理」,只用于这里搜索。">
              <Button icon={<SyncOutlined />} loading={syncing} onClick={doSync}>
                同步通讯录
              </Button>
            </Tooltip>
          )}
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
