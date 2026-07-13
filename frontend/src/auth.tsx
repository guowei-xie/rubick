import { createContext, useContext, useEffect, useState } from "react";
import { getMe, User } from "./api";

interface AuthCtx {
  user: User | null;
  loading: boolean;
  setToken: (token: string, user: User) => void;
  logout: () => void;
}

const Ctx = createContext<AuthCtx>(null as any);
export const useAuth = () => useContext(Ctx);

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
    location.href = "/login";
  };

  return <Ctx.Provider value={{ user, loading, setToken, logout }}>{children}</Ctx.Provider>;
}
