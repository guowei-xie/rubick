import { createContext, useContext, useEffect, useState } from "react";
import { getMe, User, withBase } from "./api";

interface AuthCtx {
  user: User | null;
  loading: boolean;
  setToken: (token: string, user: User) => void;
  logout: () => void;
}

const Ctx = createContext<AuthCtx>(null as any);
export const useAuth = () => useContext(Ctx);

/** 能建任务的角色集合。与后端 permission_service.AUTHOR_ROLES 同一口径,前端只在此列一份。 */
export const MANAGER_ROLES = ["admin", "developer"] as const;

/** 能进任务编辑器的角色 = 管理员或开发者。
 *  注意它只回答「有没有这项职能」,**不回答「能不能动某个任务」** ——
 *  后者一律读服务端算好的 `task.can_manage`,前端绝不自己算团队规则。 */
export const isManager = (user: User | null | undefined): boolean =>
  !!user && (MANAGER_ROLES as readonly string[]).includes(user.role);

/** 平台管理员:不受团队约束(可见/可编辑全部团队的全部任务)。 */
export const isPlatformAdmin = (user: User | null | undefined): boolean =>
  user?.role === "admin";

/** 我所属的团队(随 /auth/me 下发)。 */
export const myTeams = (user: User | null | undefined) => user?.teams ?? [];

/** 有没有团队 —— 没有就**不能建任务**(需求:开发者必须先有团队)。 */
export const hasTeam = (user: User | null | undefined): boolean => myTeams(user).length > 0;

/** 我管理的团队(**不含**平台管理员的全通 —— 他走全平台口径,不属于任何一队)。 */
export const managedTeams = (user: User | null | undefined) =>
  myTeams(user).filter((t) => t.is_team_admin);

/** 能不能看运营分析 = 平台管理员(全平台),或任一团队的团队管理员(只看那个团队)。
 *
 *  与后端 analytics_service.resolve_scope 同一口径。它只回答「有没有这项职能」,
 *  **真正的闸门在后端** —— 这里只负责不显示一个点了会 403 的入口。
 *  注意不能写成 roles=["admin","developer"]:团队管理员的 role 就是 developer,
 *  那样会把所有开发者都放进来。 */
export const canSeeAnalytics = (user: User | null | undefined): boolean =>
  isPlatformAdmin(user) || managedTeams(user).length > 0;

/** 我在某团队是不是团队管理员。平台管理员对任意团队恒为真(与后端
 *  team_service.require_team_admin 的豁免同一口径)。 */
export const isTeamAdminOf = (user: User | null | undefined, teamId?: number | null): boolean =>
  isPlatformAdmin(user) ||
  (teamId != null && myTeams(user).some((t) => t.id === teamId && t.is_team_admin));

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const token = localStorage.getItem("rubic_token");
    if (!token) {
      setLoading(false);
      return;
    }
    getMe()
      .then(setUser)
      .catch(() => localStorage.removeItem("rubic_token"))
      .finally(() => setLoading(false));
  }, []);

  const setToken = (token: string, u: User) => {
    localStorage.setItem("rubic_token", token);
    setUser(u);
  };
  const logout = () => {
    localStorage.removeItem("rubic_token");
    setUser(null);
    location.href = withBase("/login");
  };

  return <Ctx.Provider value={{ user, loading, setToken, logout }}>{children}</Ctx.Provider>;
}
