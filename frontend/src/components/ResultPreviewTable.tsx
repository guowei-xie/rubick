import { Table } from "antd";

/** 结果预览表格:把后端返回的 columns + rows(二维数组)渲染成表。
 *  试跑预览 / 取数预览 / 运行记录预览三处共用。 */
export default function ResultPreviewTable({
  columns,
  rows,
  pageSize = 10,
  scrollY,
}: {
  columns: string[];
  rows: any[][];
  pageSize?: number;
  scrollY?: number;
}) {
  return (
    <Table
      size="small"
      bordered
      scroll={{ x: "max-content", y: scrollY }}
      rowKey={(_, i) => String(i)}
      pagination={{ pageSize }}
      dataSource={rows.map((r, i) => {
        const o: any = { _i: i };
        columns.forEach((c, ci) => (o[c] = r[ci] == null ? "" : String(r[ci])));
        return o;
      })}
      columns={columns.map((c, ci) => ({ title: c, dataIndex: c, key: `${c}_${ci}`, ellipsis: true }))}
    />
  );
}
