import { useRef, useState } from "react";
import { Avatar, Space } from "antd";
import { lookupUsers } from "../api";

/** 飞书通讯录里的一个候选人。`id` 仅历史用户(之前被授权过、已落库)才有。 */
export interface LookupUser {
  id?: number | null;
  open_id: string;
  name?: string;
  email?: string;
  avatar?: string;
}

/** 「怎么展示一个候选人」这条规则只在这里写一次(同 TeamCandidateSelect 的立场)。 */
const toOption = (r: LookupUser) => ({
  // 统一用 open_id 作选中值:搜索命中者此刻可能尚未落库,真正要用他时才按 open_id 建行
  value: r.open_id,
  // 供 showSearch 兜底过滤 & 选中后回填文本用
  title: [r.name, r.email].filter(Boolean).join(" "),
  label: (
    <Space size={6}>
      <Avatar size={20} src={r.avatar}>{(r.name || "?").slice(0, 1)}</Avatar>
      <span>{r.name}</span>
      {r.email && <span style={{ color: "#999" }}>{r.email}</span>}
    </Space>
  ),
});

/** 搜同事的选择器逻辑:防抖搜索 + option 渲染 + 已见候选的资料缓存。
 *
 *  授权(GrantModal)与代订阅(SubscribersModal)是两件事、两个弹窗,但「挑一个同事」
 *  是同一个控件;此前两边各抄了一份,结果多选那边修好的「跨搜索保留资料」没有回流到单选那边。
 *
 *  **cache 是候选资料的唯一存放处**:每次搜索都会整片换掉 options,而选中的人可能来自上一批
 *  —— 只靠 options 找,提交时就拿不到他的姓名/头像了(单选也一样:选完再输入一个新词即中招)。
 */
export function useUserPicker() {
  const [hits, setHits] = useState<string[]>([]);
  const cache = useRef<Record<string, LookupUser>>({});
  const timer = useRef<any>(null);

  const fetchNow = (q: string) =>
    lookupUsers(q).then((rows: LookupUser[]) => {
      rows.forEach((r) => (cache.current[r.open_id] = r));
      setHits(rows.map((r) => r.open_id));
    });

  // 输入框搜索防抖:避免每敲一个字就打一次飞书/DB(live 搜索尤其贵)
  const onSearch = (q: string) => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => fetchNow(q), 350);
  };

  return {
    options: hits.map((id) => toOption(cache.current[id])),
    onSearch,
    fetchNow,
    profileOf: (openId?: string) => (openId ? cache.current[openId] : undefined),
  };
}
