"use client";

/**
 * The access token lives only in this module's memory: never localStorage, never a
 * readable cookie. A reload gets a new one from the httpOnly refresh cookie.
 */
let accessToken: string | null = null;
let inFlight: Promise<string | null> | null = null;

export function getAccessToken(): string | null {
  return accessToken;
}

export function setAccessToken(token: string | null): void {
  accessToken = token;
}

async function requestRefresh(): Promise<string | null> {
  const res = await fetch("/api/auth/refresh", { method: "POST", cache: "no-store" });
  if (res.status === 401) return null;
  if (!res.ok) throw new Error(`refresh failed: ${res.status}`);
  const body = (await res.json()) as { access_token: string };
  return body.access_token;
}

/**
 * Get a new access token. The API rotates refresh tokens and treats a reused one as
 * theft, so only one refresh may run at a time: shared within the tab, and serialised
 * across tabs with the Web Locks API where the browser has it.
 */
export function refreshAccessToken(): Promise<string | null> {
  if (!inFlight) {
    const run = () => requestRefresh();
    // `await` flattens the lock's Promise<Promise<...>> typing to the token itself.
    const viaLock = async (): Promise<string | null> =>
      await navigator.locks.request("fitcoach-refresh", run);
    const locked =
      typeof navigator !== "undefined" && navigator.locks ? viaLock() : run();
    inFlight = locked
      .then((token) => {
        accessToken = token;
        return token;
      })
      .finally(() => {
        inFlight = null;
      });
  }
  return inFlight;
}
