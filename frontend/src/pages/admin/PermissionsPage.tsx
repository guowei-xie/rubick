import { useEffect, useState } from "react";
import { Button, Card, Table, Tag } from "antd";
import { listTemplates } from "../../api";
import GrantModal from "../../components/GrantModal";

export default function PermissionsPage() {
  const [templates, setTemplates] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [target, setTarget] = useState<any>(null);

  const load = () => {
    setLoading(true);
    // 管理员在此接口能看到全部已发布模板
    listTemplates(false)
      .then(setTemplates)
      .finally(() => setLoading(false));
  };
  useEffect(load, []);

  const columns = [
    { title: "ID", dataIndex: "id", width: 70 },
    { title: "模板", dataIndex: "name" },
    { title: "业务域", dataIndex: "domain", render: (d: string) => d && <Tag>{d}</Tag> },
    { title: "方言", dataIndex: "dialect", render: (d: string) => <Tag>{d}</Tag> },
    {
      title: "操作",
      render: (_: any, row: any) => (
        <Button type="link" onClick={() => setTarget(row)}>
          管理授权
        </Button>
      ),
    },
  ];

  return (
    <Card title="权限矩阵(按模板授权)" extra={<Button onClick={load}>刷新</Button>}>
      <Table rowKey="id" loading={loading} dataSource={templates} columns={columns} />
      <GrantModal
        templateId={target?.id ?? null}
        templateName={target?.name}
        open={!!target}
        onClose={() => setTarget(null)}
      />
    </Card>
  );
}
