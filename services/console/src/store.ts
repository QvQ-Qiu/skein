import { defineStore } from "pinia";
import { ApiError, fetchSummary, readToken, writeToken } from "./api";

let rerun = false;

export const useConsoleStore = defineStore("console", {
  state: () => ({
    token: readToken(),
    connected: false,
    unauthorized: readToken().length === 0,
    lastRefreshAt: null as number | null,
    error: null as string | null,
    refreshing: false,
  }),
  actions: {
    clearConnection() {
      this.connected = false;
      this.unauthorized = true;
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
      }
    },
    async probe() {
      this.error = null;
      try {
        await fetchSummary();
        this.connected = true;
        this.unauthorized = false;
        this.lastRefreshAt = Date.now();
      } catch (err) {
        this.connected = false;
        if (err instanceof ApiError && err.status === 401) {
          this.unauthorized = true;
          this.error = null;
          return;
        }
        this.error = err instanceof Error && err.message ? err.message : "网络错误";
      }
    },
  },
});
