"use client";

import React, { createContext, useCallback, useContext, useEffect, useState } from "react";
import { api, session } from "./api";
import type { Me } from "./types";

interface AuthState {
  me: Me | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => void;
  can: (permission: string) => boolean;
  refresh: () => Promise<void>;
}

const AuthContext = createContext<AuthState | undefined>(undefined);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    if (!session.token()) {
      setMe(null);
      setLoading(false);
      return;
    }
    try {
      setMe(await api<Me>("/auth/me"));
    } catch {
      setMe(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const login = async (email: string, password: string) => {
    const res = await api<{ access_token: string; me: Me }>("/auth/login", { method: "POST", body: { email, password } });
    session.setToken(res.access_token);
    setMe(res.me);
  };

  const logout = () => {
    session.setToken(null);
    setMe(null);
    window.location.href = "/login";
  };

  // UI convenience only — the API enforces every permission itself.
  const can = (permission: string) => !!me && me.permissions.includes(permission);

  return <AuthContext.Provider value={{ me, loading, login, logout, can, refresh }}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}
