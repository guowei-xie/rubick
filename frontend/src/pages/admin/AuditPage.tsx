import { useEffect, useMemo, useState } from "react";
import { Button, Card, DatePicker, message, Select, Space, Table } from "antd";
import { DownloadOutlined, ReloadOutlined } from "@ant-design/icons";
import type { Dayjs } from "dayjs";
import {
  AuditLogRow,
  AuditMeta,
  errMsg,
  exportAuditLogs,
  getAuditMeta,
  listAuditLogs,
  listUsers,
} from "../../api";
import StatusTag, { TagMap } from "../../components/StatusTag";
import SqlModal from "../../components/SqlModal";
import { dash, fmtTime } from "../../format";

/** 分组 → Tag 颜色。纯展示留在前端;动作中文名由后端 /audit/meta 提供,前端不重列一份 */
const GROUP_COLOR: Record<string, string> = {
  auth: "default",
  query: "blue",
  task: "geekblue",
  permission: "purple",
  admin: "orange",
  audit: "gold",
};

/** 后端 created_at 是朴素本地时间,这里也按本地时间发,避免带时区导致比较错位 */
const FMT = "YYYY-MM-DD HH:mm:ss";

/** detail 可能是对象或 JSON 字符串,统一成对象 */
function asObj(d: any): any {
  if (!d) return null;
  if (typeof d === "string") {
    try {
      return JSON.parse(d);
    } catch {
      return { detail: d };
    }
  }
  return d;
}

export default function AuditPage() {
  const [rows, setRows] = useState<AuditLogRow[]>([]);
  const [total, setTotal] = useState(0);
  const [meta, setMeta] = useState<AuditMeta | null>(null);
  const [users, setUsers] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [viewRow, setViewRow] = useState<AuditLogRow | null>(null);

  const [action, setAction] = useState<string | undefined>();
  const [resourceType, setResourceType] = useState<string | undefined>();
  const [userId, setUserId] = useState<number | undefined>();
  const [range, setRange] = useState<[Dayjs, Dayjs] | null>(null);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  // 条件变了但页码没变时也要重查,靠这个计数器触发
  const [reload, setReload] = useState(0);

  const filters = () => ({
    action,
    resource_type: resourceType,
    user_id: userId,
    start: range?.[0] ? range[0].format(FMT) : undefined,
    end: range?.[1] ? range[1].format(FMT) : undefined,
  });

  useEffect(() => {
    getAuditMeta().then(setMeta).catch(() => void 0);
    // 操作人下拉用管理端用户列表(只含登录过的人,正好是可能成为操作人的集合)。
    // 不能用 lookupUsers:那是搜飞书通讯录的接口,与审计无关。
    listUsers().then(setUsers).catch(() => void 0);
  }, []);

  // 唯一的取数出口:首次加载 + 翻页/改页长 + 点查询/重置都走它。
  // effect 在 commit 之后跑,所以 filters() 一定读到最新的筛选值。
  useEffect(() => {
    setLoading(true);
    listAuditLogs({ ...filters(), offset: (page - 1) * pageSize, limit: pageSize })
      .then((r) => {
        setRows(r.items);
        setTotal(r.total);
      })
      .catch((e) => message.error(errMsg(e, "查询失败")))
      .finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page, pageSize, reload]);

  /** 应用当前筛选并回到第 1 页(两个 setState 会批成一次渲染,effect 只跑一次) */
  const refetch = () => {
    setPage(1);
    setReload((n) => n + 1);
  };

  const doReset = () => {
    setAction(undefined);
    setResourceType(undefined);
    setUserId(undefined);
    setRange(null);
    refetch();
  };

  const doExport = async () => {
    setExporting(true);
    try {
      const blob = await exportAuditLogs(filters());
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "audit_logs.csv";
      a.click();
      URL.revokeObjectURL(url);
    } catch (e: any) {
      message.error(errMsg(e, "导出失败"));
    } finally {
      setExporting(false);
    }
  };

  // 动作码 → {color,label},直接喂给 StatusTag(与其它枚举同一套渲染)
  const actionTags: TagMap = useMemo(
    () =>
      Object.fromEntries(
        (meta?.actions ?? []).map((a) => [
          a.code,
          { color: GROUP_COLOR[a.group] ?? "geekblue", label: a.label },
        ])
      ),
    [meta]
  );
  const resourceMap = useMemo(
    () => new Map((meta?.resource_types ?? []).map((r) => [r.code, r.label])),
    [meta]
  );
  // 动作下拉按分组归拢
  const actionOptions = useMemo(() => {
    const byGroup = new Map<string, { value: string; label: string }[]>();
    for (const a of meta?.actions ?? []) {
      const opts = byGroup.get(a.group) ?? [];
      opts.push({ value: a.code, label: `${a.label}(${a.code})` });
      byGroup.set(a.group, opts);
    }
    return [...byGroup].map(([group, options]) => ({ label: group, options }));
  }, [meta]);

  const detail = asObj(viewRow?.detail);
  // 按内容判断而非按动作码枚举:动作只会越来越多,枚举法必漏。
  // 这样 task_create / task_update 的 SQL 也自动走高亮视图。
  const sql = detail?.executed_sql ?? detail?.sql_text ?? null;

  const columns = useMemo(
    () => [
      { title: "时间", dataIndex: "created_at", width: 170, render: (t: string) => fmtTime(t) },
      { title: "操作人", dataIndex: "user_name", width: 120, ellipsis: true, render: dash },
      {
        title: "动作",
        dataIndex: "action",
        width: 170,
        render: (a: string) => <StatusTag map={actionTags} value={a} />,
      },
      {
        title: "资源",
        ellipsis: true,
        render: (_: any, r: AuditLogRow) => {
          if (!r.resource_type) return "-";
          const type = resourceMap.get(r.resource_type) ?? r.resource_type;
          // 优先展示名称快照:任务改名/数据源删除后仍然读得懂
          const label = r.resource_name || (r.resource_id ? `#${r.resource_id}` : "");
          const text = `${type} ${label}`.trim();
          return <span title={text}>{text}</span>;
        },
      },
      {
        title: "详情",
        width: 100,
        render: (_: any, r: AuditLogRow) => {
          const d = asObj(r.detail);
          if (!d || Object.keys(d).length === 0) return <span style={{ color: "#ccc" }}>—</span>;
          return (
            <Button type="link" size="small" onClick={() => setViewRow(r)}>
              点击查阅
            </Button>
          );
        },
      },
      { title: "IP", dataIndex: "ip", width: 130, render: dash },
    ],
    [actionTags, resourceMap]
  );

  return (
    <Card title="审计日志" style={{ borderRadius: 28, minHeight: "calc(100vh - 120px)" }}>
      <Space style={{ marginBottom: 12 }} wrap>
        <Select
          placeholder="动作"
          style={{ width: 240 }}
          allowClear
          showSearch
          optionFilterProp="label"
          value={action}
          onChange={setAction}
          options={actionOptions}
        />
        <Select
          placeholder="资源类型"
          style={{ width: 130 }}
          allowClear
          value={resourceType}
          onChange={setResourceType}
          options={(meta?.resource_types ?? []).map((r) => ({ value: r.code, label: r.label }))}
        />
        <Select
          placeholder="操作人"
          style={{ width: 160 }}
          allowClear
          showSearch
          optionFilterProp="label"
          value={userId}
          onChange={setUserId}
          options={users.map((u) => ({ value: u.id, label: u.name }))}
        />
        <DatePicker.RangePicker
          showTime
          style={{ width: 330 }}
          value={range as any}
          onChange={(v) => setRange(v as [Dayjs, Dayjs] | null)}
        />
        <Button type="primary" onClick={refetch}>
          查询
        </Button>
        <Button icon={<ReloadOutlined />} onClick={doReset}>
          重置
        </Button>
        <Button icon={<DownloadOutlined />} loading={exporting} onClick={doExport}>
          导出 CSV
        </Button>
      </Space>

      <Table
        rowKey="id"
        loading={loading}
        dataSource={rows}
        columns={columns}
        size="middle"
        pagination={{
          current: page,
          pageSize,
          total,
          showSizeChanger: true,
          pageSizeOptions: [20, 50, 100, 200],
          showTotal: (t) => `共 ${t} 条`,
          onChange: (p, ps) => {
            setPage(p);
            setPageSize(ps);
          },
        }}
      />

      <SqlModal
        title={sql ? "运行的 SQL" : "详情"}
        sql={viewRow ? sql || JSON.stringify(detail, null, 2) : null}
        onClose={() => setViewRow(null)}
      />
    </Card>
  );
}
