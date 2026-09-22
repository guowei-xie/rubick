import { useCallback, useEffect, useRef, useState } from "react";
import { errMsg } from "../../api";

/**
 * 板块级取数。**每块各自 loading、各自失败降级** —— 各块串成一个请求的话,
 * 最快的那块会被最慢的拖住,整页白几秒;而一块的 SQL 写崩了不该让整页打不开。
 *
 * 顺带收口一个必踩的竞态:快速连点「近7天 / 近30天 / 近90天」会并发出三个请求,
 * 先发的后到就会把新结果盖掉。这里用 AbortController 取消上一次未完成的请求。
 */
export function useAnalyticsQuery<T>(
  fetcher: (signal: AbortSignal) => Promise<T>,
  deps: unknown[],
  { enabled = true }: { enabled?: boolean } = {}
) {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    if (!enabled) return;
    abortRef.current?.abort();
    const ac = new AbortController();
    abortRef.current = ac;
    setLoading(true);
    setError(null);
    fetcher(ac.signal)
      .then((d) => {
        if (!ac.signal.aborted) setData(d);
      })
      .catch((e) => {
        // 被自己取消的请求不是错误,别把它显示成一块红字
        if (ac.signal.aborted || e?.code === "ERR_CANCELED") return;
        setError(errMsg(e, "这一块加载失败"));
      })
      .finally(() => {
        if (!ac.signal.aborted) setLoading(false);
      });
    return () => ac.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, enabled, tick]);

  const reload = useCallback(() => setTick((n) => n + 1), []);
  return { data, loading, error, reload };
}

/**
 * 滚到附近才取数。每块各发一个请求,一起并发在本地 SQLite 上会串行排队,而用户实际只先看
 * 第一块 —— 首屏只打「meta + 板块①」两个请求,其余各块滚到跟前再说。
 *
 * 一旦进入过视口就永久 enabled:再滚出去不该把已经拿到的数据丢掉重取。
 */
export function useInViewOnce<E extends HTMLElement>() {
  const ref = useRef<E | null>(null);
  const [seen, setSeen] = useState(false);

  useEffect(() => {
    if (seen || !ref.current) return;
    // 没有 IntersectionObserver 的环境(老浏览器、某些测试环境)直接放行,
    // 宁可多取一次数,也不要整块永远不加载
    if (typeof IntersectionObserver === "undefined") {
      setSeen(true);
      return;
    }
    const io = new IntersectionObserver(
      (entries) => entries.some((e) => e.isIntersecting) && setSeen(true),
      { rootMargin: "240px" } // 提前一屏开始取,滚到时通常已经画好了
    );
    io.observe(ref.current);
    return () => io.disconnect();
  }, [seen]);

  return { ref, seen };
}

/**
 * 一个板块要的全套:滚到跟前才取数 + 取消上一次未完成的请求 + 三种渲染状态。
 *
 * 各块把 `useInViewOnce` / `[JSON.stringify(query)]` / `{ enabled: seen }` /
 * `loading={(loading && !data) || !seen}` 各抄一遍时,那四行必须**字字相同**才行:
 * 漏掉 `|| !seen` 的那一块会在滚到之前先闪一屏空态,看着像「这块没数据」而不是「还没取」。
 * 收进来之后,首屏那块只要传 `eager`,其余各块什么都不用传。
 */
export function useSectionData<T>(
  fetcher: (signal: AbortSignal) => Promise<T>,
  query: unknown,
  { eager = false }: { eager?: boolean } = {}
) {
  const { ref, seen } = useInViewOnce<HTMLDivElement>();
  const ready = eager || seen;
  const { data, loading, error, reload } = useAnalyticsQuery<T>(
    fetcher,
    [JSON.stringify(query)],
    { enabled: ready }
  );
  // 还没轮到取数时也算 loading:否则骨架屏之前会先闪一下空态
  return { ref, data, error, reload, loading: (loading && !data) || !ready };
}
