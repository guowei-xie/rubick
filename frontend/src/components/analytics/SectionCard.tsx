import { Alert, Button, Card, Empty, Skeleton } from "antd";
import { ReloadOutlined } from "@ant-design/icons";

/**
 * 板块外壳:标题 + 范围后缀 + 三种非正常态(加载中 / 失败 / 空)。
 *
 * **标题后缀由这里统一渲染**,板块自己不拼字符串。页头的口径行会随着滚动离开视野,
 * 而每一块的标题不会 —— 所以「· 本团队 / · 全平台 / · 团队《X》」必须跟在每个板块标题上,
 * 否则滚到第三屏的人已经不记得自己在看谁的数据了。
 *
 * 失败**只降级这一块**:别的板块照常显示。一块的查询写崩了不该让整页打不开。
 */
export default function SectionCard({
  id,
  title,
  scopeLabel,
  extra,
  loading,
  error,
  onRetry,
  empty,
  emptyText,
  children,
  innerRef,
}: {
  id?: string;
  title: string;
  /** 「全平台」/ 团队名。由 AnalyticsPage 统一算一次往下传 —— 四个板块各自拼一遍的话,
   *  平台管理员下钻到某个队时,总有一块还写着「全平台」 */
  scopeLabel?: string;
  extra?: React.ReactNode;
  loading?: boolean;
  error?: string | null;
  onRetry?: () => void;
  empty?: boolean;
  emptyText?: string;
  children: React.ReactNode;
  innerRef?: React.Ref<HTMLDivElement>;
}) {
  return (
    <div id={id} ref={innerRef} style={{ scrollMarginTop: 96 }}>
      <Card
        className="rk-section"
        style={{ marginBottom: 16 }}
        title={
          <span className="rk-section-title">
            {title}
            {scopeLabel && <span className="rk-section-scope">· {scopeLabel}</span>}
          </span>
        }
        extra={extra}
      >
        {error ? (
          <Alert
            type="error"
            showIcon
            message="这一块没能加载出来"
            description={error}
            action={
              onRetry && (
                <Button size="small" icon={<ReloadOutlined />} onClick={onRetry}>
                  重试
                </Button>
              )
            }
          />
        ) : loading ? (
          <Skeleton active paragraph={{ rows: 4 }} />
        ) : empty ? (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={emptyText || "这个范围内还没有数据"}
          />
        ) : (
          children
        )}
      </Card>
    </div>
  );
}
