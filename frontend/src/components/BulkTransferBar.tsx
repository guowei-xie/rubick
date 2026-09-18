import { Alert, Avatar, Button, Select, Space, Tag, Tooltip, Typography } from "antd";
import { AuthorTransferCandidate, BlockedGroup } from "../api";
import { personLabel } from "../format";

/**
 * 批量交接的工具条 —— 表格上方那一条,只在批量模式下存在。
 *
 * **先选接手人,再按他过滤可勾选的任务**:没选人之前整张表都勾不动。顺序反过来也做得出来,
 * 但那样人会先勾满一屏、再被告知其中一半给不了这个人 —— 而「能不能给他」本来就是
 * 服务端一次就算好了的事(候选接口的 eligible / blocked),没有理由让人先做无用功。
 *
 * 放在 Card body 里而不是 extra:extra 已经四件东西,再塞一个 280px 的选人框会在窄屏
 * 把标题栏挤崩;而工具条只在模式里出现,不占常态版面。
 */
export default function BulkTransferBar({
  candidates,
  loading,
  picked,
  onPick,
  selectedCount,
  hiddenSelectedCount,
  eligibleVisibleCount,
  blockedGroups,
  onSelectAll,
  onSubmit,
  onExit,
}: {
  candidates: AuthorTransferCandidate[];
  loading: boolean;
  picked?: number;
  onPick: (id?: number) => void;
  selectedCount: number;
  /** 选中了、但不在当前筛选结果里的条数。如实说出来,否则计数与眼前的表格对不上 */
  hiddenSelectedCount: number;
  eligibleVisibleCount: number;
  /** 当前接手人接不了的那些,按理由分组(文案由服务端出,这里只做计数) */
  blockedGroups: BlockedGroup[];
  onSelectAll: () => void;
  onSubmit: () => void;
  onExit: () => void;
}) {
  const blockedCount = blockedGroups.reduce((n, g) => n + g.template_ids.length, 0);

  return (
    <Alert
      type="info"
      style={{ marginBottom: 12 }}
      message={
        <Space size={12} wrap align="center">
          <b>批量交接作者</b>
          <Select
            showSearch
            allowClear
            optionFilterProp="label"
            loading={loading}
            value={picked}
            onChange={(v) => onPick(v ?? undefined)}
            style={{ minWidth: 280 }}
            placeholder="选择接手人"
            notFoundContent={loading ? "加载中…" : "没有可接手你任务的人"}
            options={candidates.map((c) => ({
              value: c.user_id,
              // label 单独给字符串:下面的 option 是富渲染,而搜索匹配的是 label
              label: personLabel(c),
              option: c,
            }))}
            optionRender={(o) => {
              const c = (o.data as any).option as AuthorTransferCandidate;
              return (
                <Space size={6}>
                  <Avatar size={20} src={c.avatar || undefined}>
                    {(c.name || "?").slice(0, 1)}
                  </Avatar>
                  <span>{c.name}</span>
                  <span style={{ color: "#999" }}>{c.email}</span>
                  <Tag color="blue">可接手 {c.eligible_template_ids.length} 个</Tag>
                </Space>
              );
            }}
          />
          {!picked ? (
            <Typography.Text type="secondary">
              先选接手人，再按他能不能接手来勾任务；也可以在上方搜索框输入离职同事的姓名，只看他的任务。
            </Typography.Text>
          ) : (
            <Space size={8} wrap align="center">
              <span>
                已选 <b>{selectedCount}</b>
                {hiddenSelectedCount > 0 && (
                  <Typography.Text type="secondary">
                    {`（其中 ${hiddenSelectedCount} 个不在当前筛选结果中）`}
                  </Typography.Text>
                )}
              </span>
              <Button size="small" onClick={onSelectAll} disabled={!eligibleVisibleCount}>
                选中全部可转移的（{eligibleVisibleCount}）
              </Button>
              {blockedCount > 0 && (
                // 逐行悬停能看到每条的理由,这里给的是「一共多少、分几类」的鸟瞰:
                // 25 个置灰时没人愿意一行行悬停过去
                <Tooltip
                  title={
                    <ul style={{ margin: 0, paddingLeft: 18 }}>
                      {blockedGroups.map((g) => (
                        <li key={g.code + g.message}>
                          {g.message}（{g.template_ids.length} 个）
                        </li>
                      ))}
                    </ul>
                  }
                >
                  <Tag color="default">不可转移 {blockedCount}</Tag>
                </Tooltip>
              )}
            </Space>
          )}
          <Button type="primary" disabled={!picked || !selectedCount} onClick={onSubmit}>
            转移 {selectedCount} 个任务
          </Button>
          <Button onClick={onExit}>退出</Button>
        </Space>
      }
    />
  );
}
