import { useEffect, useState } from "react";
import { Select } from "antd";
import { teamCandidates } from "../api";

/**
 * 「选一个开发者加入本团队」的下拉。团队页与团队管理页共用。
 *
 * 候选人来自 `GET /teams/{id}/candidates`(登录过的开发者且尚不在本团队),
 * 刻意不走飞书通讯录 —— 团队成员必须已经是平台上的开发者。
 * 「怎么展示一个候选人」(姓名 · 邮箱)这条规则只在这里写一次。
 *
 * reloadKey:调用方在增删成员后 +1,用来重取候选(刚加进去的人要从候选里消失)。
 */
export default function TeamCandidateSelect({
  teamId,
  value,
  onChange,
  reloadKey = 0,
  placeholder = "选择开发者加入本团队",
  width = 280,
}: {
  teamId: number;
  value?: number;
  onChange: (v?: number) => void;
  reloadKey?: number;
  placeholder?: string;
  width?: number;
}) {
  const [options, setOptions] = useState<{ value: number; label: string }[]>([]);

  const load = (q?: string) =>
    teamCandidates(teamId, q).then((rows) =>
      setOptions(
        rows.map((r) => ({
          value: r.user_id,
          label: [r.name, r.email].filter(Boolean).join(" · "),
        }))
      )
    );

  useEffect(() => {
    load();
  }, [teamId, reloadKey]);

  return (
    <Select
      showSearch
      // 服务端按 q 过滤(候选可能超过一屏),故关掉本地过滤
      filterOption={false}
      placeholder={placeholder}
      style={{ width }}
      value={value}
      onSearch={load}
      onChange={onChange}
      options={options}
      notFoundContent="没有可加入的开发者(需先由管理员把对方设为开发者角色并登录过)"
    />
  );
}
