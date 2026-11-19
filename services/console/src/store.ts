import { defineStore } from "pinia";
import {
  ApiError,
  fetchSummary,
  loginRequest,
  logoutRequest,
  probeSession,
  readToken,
  writeToken,
  type SessionUser,
} from "./api";

let rerun = false;

export const useConsoleStore = defineStore("console", {
  state: () => ({
    token: readToken(),
    connected: false,
    unauthorized: true,
    booted: false,
    lastRefreshAt: null as number | null,
    error: null as string | null,
    refreshing: false,
    authMode: "" as "" | "sso" | "token",
    user: null as SessionUser | null,
    ssoDown: false,
  }),
  actions: {
    clearConnection() {
      this.connected = false;
      this.unauthorized = true;
      this.user = null;
      this.error = null;
    },
    saveToken(token: string) {
      const value = token.trim();
      this.token = value;
      writeToken(value);
      if (!value) this.unauthorized = true;
    },
    async refresh() {
      if (this.refreshing) {
        rerun = true;
        return;
      }
      this.refreshing = true;
      try {
        do {
          rerun = false;
          await this.probe();
        } while (rerun);
      } finally {
        this.refreshing = false;
        this.booted = true;
      }
    },
    async probe() {
      this.error = null;
      try {
        const session = await probeSession();
        this.authMode = session.mode;
        if (session.mode === "sso") {
          // Cookie 有效就直接进；无效落登录页；unisso 挂了不清本地状态。
          this.user = session.user ?? null;
          this.connected = session.user != null;
          this.unauthorized = session.user == null;
          this.ssoDown = false;
          this.lastRefreshAt = Date.now();
          return;
        }
        if (!this.token) {
          this.authMode = "token";
          this.unauthorized = true;
          return;
        }
        await fetchSummary();
        this.authMode = "token";
        this.connected = true;
        this.unauthorized = false;
        this.lastRefreshAt = Date.now();
      } catch (err) {
        if (err instanceof ApiError && err.status === 503) {
          // unisso 不可达：保留本地身份，不当登出处理。
          this.ssoDown = true;
          this.error = null;
          if (this.user == null) {
            this.connected = false;
            this.unauthorized = true;
          }
          return;
        }
        this.connected = false;
        if (err instanceof ApiError && err.status === 401) {
          this.unauthorized = true;
          this.user = null;
          this.error = null;
          return;
        }
        this.error = err instanceof Error && err.message ? err.message : "网络错误";
      }
    },
    async login(email: string, password: string) {
      const session = await loginRequest(email, password);
      this.authMode = session.mode;
      this.user = session.user ?? null;
      this.connected = session.user != null;
      this.unauthorized = session.user == null;
      this.ssoDown = false;
      this.lastRefreshAt = Date.now();
    },
    async logout() {
      try {
        await logoutRequest();
      } catch {
        // unisso 不可达也要把本地会话清掉
      }
      this.clearConnection();
    },
  },
});
