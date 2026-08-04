import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";

import { ApiClient, ApiError } from "../api/client.ts";
import type { SessionResponse } from "../api/contracts.ts";

type AuthState = "loading" | "signed-out" | "signed-in";

interface AuthContextValue {
  state: AuthState;
  username: string | null;
  api: ApiClient;
  login: (username: string, pin: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>("loading");
  const [username, setUsername] = useState<string | null>(null);

  const markSignedOut = useCallback(() => {
    setState("signed-out");
    setUsername(null);
  }, []);
  const api = useMemo(() => new ApiClient(markSignedOut), [markSignedOut]);

  const acceptSession = useCallback(
    (session: SessionResponse) => {
      api.setCsrfToken(session.csrfToken);
      setUsername(session.username);
      setState("signed-in");
    },
    [api],
  );

  useEffect(() => {
    const controller = new AbortController();
    api
      .request<SessionResponse>("/api/auth/session", { signal: controller.signal })
      .then(acceptSession)
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === "AbortError") return;
        if (error instanceof ApiError && error.status === 401) {
          markSignedOut();
          return;
        }
        markSignedOut();
      });
    return () => controller.abort();
  }, [acceptSession, api, markSignedOut]);

  const login = useCallback(
    async (submittedUsername: string, pin: string) => {
      const session = await api.request<SessionResponse>("/api/auth/login", {
        method: "POST",
        body: JSON.stringify({ username: submittedUsername, pin }),
      });
      acceptSession(session);
    },
    [acceptSession, api],
  );

  const logout = useCallback(async () => {
    try {
      await api.request<void>("/api/auth/logout", { method: "POST" });
    } finally {
      api.setCsrfToken(null);
      markSignedOut();
    }
  }, [api, markSignedOut]);

  const value = useMemo(
    () => ({ state, username, api, login, logout }),
    [api, login, logout, state, username],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used inside AuthProvider");
  return value;
}
