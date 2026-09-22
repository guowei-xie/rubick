import { useCallback, useMemo } from "react";
import { useSearchParams } from "react-router-dom";
import dayjs, { Dayjs } from "dayjs";
import { AnalyticsMeta, AnalyticsQuery, ScopeOption } from "../../api";
import { User } from "../../api";
import { isPlatformAdmin } from "../../auth";

/** 时间范围预设。天数档由 /analytics/meta 下发(后端的 PRESET_DAYS),
 *  「本月」「自定义」是纯前端的概念,后端没有也不需要对应物。 */
export type Preset = string;

const DEFAULT_DAYS = 30;
const DEFAULT_PRESET: Preset = `${DEFAULT_DAYS}d`;

/**
 * 运营分析页的**唯一**参数源:时间范围 + 团队范围,全部同步到 URL。
 *
 * 「所有板块只能从这里拿参数、禁止自己拼 params」不是风格偏好 —— 漏传一次 team_id
 * 就是一次越权展示,而且页面上和正常情况长得一模一样。入口只有一个,才数得清。
 *
 * 状态放 URL 与仓库既有约定一致(TasksPage 的 ?status=/?idle=/?team=、TeamPage 的 ?tab=),
 * 顺带让「把当前视图发给同事」成立。
 */
export function useAnalyticsScope(user: User | null, meta: AnalyticsMeta | null) {
  const [sp, setSp] = useSearchParams();
  const platform = isPlatformAdmin(user);
  const options = useMemo(() => meta?.scope_options ?? [], [meta]);

  /** 可选的时间范围档。天数档**来自服务端**,前端不再另列一份 ——
   *  两份档位对不上时,界面上写着「近 7 天」而请求发的是 30 天,没有任何报错。 */
  const presets = useMemo<{ key: Preset; label: string }[]>(
    () => [
      ...(meta?.presets ?? [DEFAULT_DAYS]).map((d) => ({ key: `${d}d`, label: `近 ${d} 天` })),
      { key: "month", label: "本月" },
      { key: "custom", label: "自定义" },
    ],
    [meta]
  );

  const preset = sp.get("preset") || DEFAULT_PRESET;

  /**
   * 团队范围。**非平台管理员一律强制非空** —— URL 里手删 ?team= 或填一个他不管的队,
   * 都归一化回他自己的第一个团队,绝不发出一个不带 team_id 的请求。
   *
   * 后端会独立校验(传别队直接 403),前端这层只是不让界面进入「没有口径」的状态:
   * 一个连自己在看谁的数据都说不清的页面,比一个报错的页面糟得多。
   */
  const teamId = useMemo<number | null>(() => {
    const raw = sp.get("team");
    const asked = raw ? Number(raw) : null;
    if (platform) return Number.isFinite(asked) && asked ? asked : null;
    // 能选哪些队、默认哪一个,都**以 /analytics/meta 为准**:从 /auth/me 的团队顺序里
    // 自己挑一个,会与后端 resolve_scope 挑的那个不是同一个队 —— 界面显示 A 队、
    // 不带 team_id 的请求回的是 B 队,而两边都不会报错
    const allowed = options.map((o) => o.team_id).filter((id): id is number => id != null);
    if (asked && allowed.includes(asked)) return asked;
    return meta?.default_team_id ?? allowed[0] ?? null;
  }, [sp, platform, options, meta]);

  const range = useMemo<{ start?: string; end?: string; days?: number }>(() => {
    if (preset === "custom") {
      const s = sp.get("start");
      const e = sp.get("end");
      // 半个自定义区间(只填了一头)按默认区间处理,而不是发一个半截的请求
      if (s && e) return { start: dayjs(s).startOf("day").format("YYYY-MM-DDTHH:mm:ss"),
                           end: dayjs(e).endOf("day").format("YYYY-MM-DDTHH:mm:ss") };
      return { days: DEFAULT_DAYS };
    }
    if (preset === "month")
      return {
        start: dayjs().startOf("month").format("YYYY-MM-DDTHH:mm:ss"),
        end: dayjs().format("YYYY-MM-DDTHH:mm:ss"),
      };
    return { days: Number(preset.replace("d", "")) || DEFAULT_DAYS };
  }, [preset, sp]);

  /** 发给各板块的查询参数。板块拿到的就是这个,不再加工。 */
  const query = useMemo<AnalyticsQuery>(
    () => ({ team_id: teamId, ...range }),
    [teamId, range]
  );

  const patch = useCallback(
    (next: Record<string, string | null>) => {
      const sp2 = new URLSearchParams(sp);
      Object.entries(next).forEach(([k, v]) => (v === null ? sp2.delete(k) : sp2.set(k, v)));
      setSp(sp2, { replace: true });
    },
    [sp, setSp]
  );

  const setPreset = useCallback(
    (p: Preset) => patch(p === "custom" ? { preset: p } : { preset: p, start: null, end: null }),
    [patch]
  );

  const setRange = useCallback(
    (v: [Dayjs, Dayjs] | null) =>
      patch(
        v
          ? { preset: "custom", start: v[0].format("YYYY-MM-DD"), end: v[1].format("YYYY-MM-DD") }
          : { preset: DEFAULT_PRESET, start: null, end: null }
      ),
    [patch]
  );

  const setTeam = useCallback(
    (id: number | null) => patch({ team: id == null ? null : String(id) }),
    [patch]
  );

  const customRange = useMemo<[Dayjs, Dayjs] | null>(() => {
    const s = sp.get("start");
    const e = sp.get("end");
    return s && e ? [dayjs(s), dayjs(e)] : null;
  }, [sp]);

  /** 当前选中的范围选项(用来显示名字)。平台管理员且未下钻时是「全平台」。 */
  const currentOption = useMemo<ScopeOption | undefined>(
    () => options.find((o) => (o.team_id ?? null) === teamId),
    [options, teamId]
  );

  return {
    presets,
    preset, setPreset,
    customRange, setRange,
    teamId, setTeam,
    query,
    currentOption,
  };
}
