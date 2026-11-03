"use client";

import { useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";

import { api, ApiError, setSessionExpiredHandler } from "@/lib/api/client";
import { refreshAccessToken, setAccessToken } from "@/lib/api/session";
import type { User } from "@/lib/api/types";

type Status = "loading" | "authenticated" | "anonymous";

type Auth = {
  status: Status;
  user: User | null;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string, displayName: string) => Promise<void>;
  logout: () => Promise<void>;
  /** After the account is deleted on the API: drop the local session without a call. */
  forget: () => Promise<void>;
};

const AuthContext = createContext<Auth | null>(null);

async function startSession(path: string, body: unknown): Promise<void> {
  const res = await fetch(path, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  const data: unknown = await res.json().catch(() => null);
  if (!res.ok) {
    const detail = data && typeof data === "object" && "detail" in data ? data.detail : data;
    throw new ApiError(res.status, detail);
  }
  setAccessToken((data as { access_token: string }).access_token);
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<Status>("loading");
  const [user, setUser] = useState<User | null>(null);

  const loadUser = useCallback(async () => {
    const me = await api<User>("/auth/me");
    setUser(me);
    setStatus("authenticated");
  }, []);

  const drop = useCallback(() => {
    setAccessToken(null);
    setUser(null);
    setStatus("anonymous");
    queryClient.clear();
  }, [queryClient]);

  useEffect(() => {
    setSessionExpiredHandler(drop);
    let cancelled = false;
    refreshAccessToken()
      .then(async (token) => {
        if (cancelled) return;
        if (token) await loadUser();
        else setStatus("anonymous");
      })
      .catch(() => {
        if (!cancelled) setStatus("anonymous");
      });
    return () => {
      cancelled = true;
    };
  }, [drop, loadUser]);

  const login = useCallback(
    async (email: string, password: string) => {
      await startSession("/api/auth/login", { email, password });
      await loadUser();
    },
    [loadUser],
  );

  const register = useCallback(
    async (email: string, password: string, displayName: string) => {
      await startSession("/api/auth/register", { email, password, display_name: displayName });
      await loadUser();
    },
    [loadUser],
  );

  const forget = useCallback(async () => {
    await fetch("/api/auth/logout", { method: "POST" }).catch(() => undefined);
    drop();
  }, [drop]);

  return (
    <AuthContext value={{ status, user, login, register, logout: forget, forget }}>
      {children}
    </AuthContext>
  );
}

export function useAuth(): Auth {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used inside AuthProvider");
  return value;
}
