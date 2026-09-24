import { Button, DatePicker, Segmented, Select, Space, Tag } from "antd";
import dayjs from "dayjs";
import { ScopeOption } from "../../api";
import { Preset } from "./useAnalyticsScope";

const { RangePicker } = DatePicker;

/** 页面内的板块锚点。不是 Tab —— 它们不卸载内容,只是滚动定位。 */
export const SECTIONS = [
  { id: "live", label: "实时负载" }, // 仅全平台视角渲染,锚点随之过滤
  { id: "adoption", label: "采纳与活跃" },
  { id: "health", label: "运行健康" },
  { id: "runs", label: "运行明细" },
  { id: "assets", label: "任务资产" },
  { id: "governance", label: "权限与配置" },
];

/**
 * 页头:口径行 + 范围/时间控件 + 板块锚点。
 *
 * **口径行是页面的一部分,不是提示条。** 刻意不用 Alert:它会被读成「临时通知」,
 * 带关闭按钮的话第一天就被关掉了,而这句话需要天天都在。
 *
 * 更要紧的是:**平台管理员看到的是同一条口径行**,内容写「全平台」。两种身份都有这一行,
 * 靠内容区分 —— 而不是靠「有没有提示」区分。后者会让人学会把「没有提示」读成「这是全平台」,
 * 那正是我们要防的误读。
 */
export default function AnalyticsHeader({
  scopeLabel,
  windowText,
  options,
  teamId,
  onTeam,
  preset,
  presets,
  onPreset,
  customRange,
  onRange,
  platform,
  isTeamView,
}: {
  scopeLabel: string;
  windowText: string;
  options: ScopeOption[];
  teamId: number | null;
  onTeam: (id: number | null) => void;
  presets: { key: Preset; label: string }[];
  preset: Preset;
  onPreset: (p: Preset) => void;
  customRange: [dayjs.Dayjs, dayjs.Dayjs] | null;
  onRange: (v: [dayjs.Dayjs, dayjs.Dayjs] | null) => void;
  platform: boolean;
  isTeamView: boolean;
}) {
  // 只有一个可选团队时不渲染下拉:一个只有一个选项的下拉是噪声,
  // 而且会让人以为点开能看到别的团队。直接显示成一枚不可交互的 Tag。
  const singleTeam = !platform && options.length <= 1;

  return (
    <div className="rk-ana-header">
      <div className="rk-ana-title">
        <h2>运营分析</h2>
        {isTeamView && <Tag color="purple">团队视角</Tag>}
      </div>

      {/* 口径行 —— 数据范围与时间范围各说一遍,两种身份都有 */}
      <div className="rk-ana-scope">
        <div className="rk-ana-scope-main">
          数据范围：<b>{scopeLabel}</b> · {windowText}
        </div>
        {isTeamView && (
          <div className="rk-ana-scope-note">
            你看到的是本团队任务的数据，不是全平台数据。全平台口径由平台管理员查看。
          </div>
        )}
      </div>

      <div className="rk-ana-controls">
        <Space wrap>
          {singleTeam ? (
            <Tag color="purple" style={{ marginInlineEnd: 0, padding: "4px 10px" }}>
              {options[0]?.name ?? scopeLabel}
            </Tag>
          ) : (
            <Select
              value={teamId}
              onChange={onTeam}
              showSearch
              optionFilterProp="label"
              style={{ minWidth: 180 }}
              options={options.map((o) => ({ value: o.team_id, label: o.name }))}
            />
          )}

          <Segmented
            value={preset}
            onChange={(v) => onPreset(v as Preset)}
            options={presets.map((p) => ({ value: p.key, label: p.label }))}
          />

          {preset === "custom" && (
            <RangePicker
              value={customRange}
              onChange={(v) => onRange(v as [dayjs.Dayjs, dayjs.Dayjs] | null)}
              allowClear
              // 不带 showTime:运营分析按天就够,带时分秒只会让人纠结边界
            />
          )}
        </Space>

        <Space size={4} wrap>
          {SECTIONS.filter((s) => s.id !== "live" || (platform && !isTeamView)).map((s) => (
            <Button
              key={s.id}
              type="text"
              size="small"
              onClick={() =>
                document.getElementById(s.id)?.scrollIntoView({ behavior: "smooth" })
              }
            >
              {s.label}
            </Button>
          ))}
        </Space>
      </div>
    </div>
  );
}
