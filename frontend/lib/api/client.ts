"use client";

import { getAccessToken, refreshAccessToken } from "./session";

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly detail: unknown,
  ) {
    super(typeof detail === "string" ? detail : `API error ${status}`);
  }

  /** The machine-readable code the API puts in detail.code, if any. */
  get code(): string | undefined {
    if (this.detail && typeof this.detail === "object" && "code" in this.detail) {
      const code = (this.detail as { code: unknown }).code;
      return typeof code === "string" ? code : undefined;
    }
    return undefined;
  }
}

/** Raised when the session is gone and the user has to log in again. */
export class SessionExpiredError extends ApiError {
  constructor() {
    super(401, "Session expired");
  }
}

let onSessionExpired: () => void = () => undefined;

export function setSessionExpiredHandler(handler: () => void): void {
  onSessionExpired = handler;
}

type Options = { method?: string; body?: unknown; signal?: AbortSignal };

async function send(path: string, opts: Options, token: string | null): Promise<Response> {
  const headers: Record<string, string> = { accept: "application/json" };
  if (opts.body !== undefined) headers["content-type"] = "application/json";
  if (token) headers.authorization = `Bearer ${token}`;
  return fetch(`/api/backend${path}`, {
    method: opts.method ?? "GET",
    headers,
    body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
    signal: opts.signal,
    cache: "no-store",
  });
}

/** A 401 for the token itself (not, say, a wrong password) asks for Bearer auth. */
function tokenRejected(res: Response): boolean {
  return res.status === 401 && (res.headers.get("www-authenticate") ?? "").startsWith("Bearer");
}

/** Call the API through the same-origin proxy, renewing the access token once on 401. */
export async function api<T>(path: string, opts: Options = {}): Promise<T> {
  let token = getAccessToken() ?? (await refreshAccessToken());
  let res = await send(path, opts, token);
  if (tokenRejected(res)) {
    token = await refreshAccessToken();
    if (!token) {
      onSessionExpired();
      throw new SessionExpiredError();
    }
    res = await send(path, opts, token);
    if (tokenRejected(res)) {
      onSessionExpired();
      throw new SessionExpiredError();
    }
  }
  if (res.status === 204) return undefined as T;
  const body: unknown = await res.json().catch(() => null);
  if (!res.ok) {
    const detail = body && typeof body === "object" && "detail" in body ? body.detail : body;
    throw new ApiError(res.status, detail);
  }
  return body as T;
}
