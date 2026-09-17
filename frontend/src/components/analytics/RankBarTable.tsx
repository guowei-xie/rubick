import { Table } from "antd";
import type { ColumnsType } from "antd/es/table";

/**
 * 排行表:AntD Table + 一列 CSS 行内条形。
 *
 * 排行刻意用**表格而不是条形图**:排行要同时回答「叫什么、属于谁、跑了多少次、成功率多少」,
 * 条形图只能表达一个维度,其余全得靠 tooltip —— 而 tooltip 里的信息等于不存在,
 * 没人会去逐个悬停。条形只作为一列背景,给「谁明显更高」一个一眼可见的形状。
 */
export default function RankBarTable<T extends object>({
  rows,
  columns,
  barKey,
  barLabel,
  rowKey,
  onRow,
  emptyText,
}: {
  rows: T[];
  columns: ColumnsType<T>;
  /** 画条形用哪个字段 */
  barKey: keyof T;
  barLabel: string;
  rowKey: (r: T) => React.Key;
  onRow?: (r: T) => { onClick?: () => void };
  emptyText?: string;
}) {
  const max = Math.max(1, ...rows.map((r) => Number(r[barKey]) || 0));
  const barColumn: ColumnsType<T>[number] = {
    title: barLabel,
    key: "__bar",
    width: 160,
    render: (_: unknown, r: T) => {
      const v = Number(r[barKey]) || 0;
      return (
        <div className="rk-rankbar">
          <span className="rk-rankbar-fill" style={{ width: `${(v / max) * 100}%` }} />
          <span className="rk-rankbar-num">{v.toLocaleString("zh-CN")}</span>
        </div>
      );
    },
  };

  return (
    <Table<T>
      size="small"
      pagination={false}
      rowKey={rowKey}
      dataSource={rows}
      columns={[...columns, barColumn]}
      onRow={onRow as any}
      scroll={{ x: "max-content" }}
      locale={{ emptyText: emptyText || "这个范围内还没有数据" }}
    />
  );
}
