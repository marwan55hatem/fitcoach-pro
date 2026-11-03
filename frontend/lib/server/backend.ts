import "server-only";

import type { NextRequest, NextResponse } from "next/server";

/** The FastAPI backend. Server-side only: the browser only ever talks to this app. */
export const API_URL = process.env.API_URL ?? "http://localhost:8001";

// The refresh token lives only in an httpOnly cookie, scoped to the auth routes.
export const REFRESH_COOKIE = "fc_refresh";
// A harmless flag the proxy can see on page requests, so it can redirect early.
export const SESSION_COOKIE = "fc_session";
const REFRESH_MAX_AGE_S = 30 * 24 * 60 * 60; // matches REFRESH_TOKEN_DAYS on the API

export type TokenPair = {
  access_token: string;
  refresh_token: string;
  expires_in: number;
};

function isLocalhost(request: NextRequest): boolean {
  const host = request.nextUrl.hostname;
  return host === "localhost" || host === "127.0.0.1" || host === "[::1]";
}

/** The real client address for the API's rate limiter (the API trusts this server). */
export function forwardedFor(request: NextRequest): string {
  const incoming = request.headers.get("x-forwarded-for");
  const first = incoming?.split(",")[0]?.trim();
  return first || request.headers.get("x-real-ip") || "127.0.0.1";
}

export async function callApi(
  request: NextRequest,
  path: string,
  init: { method: string; body?: unknown; token?: string },
): Promise<Response> {
  const headers: Record<string, string> = {
    "x-forwarded-for": forwardedFor(request),
  };
  if (init.body !== undefined) headers["content-type"] = "application/json";
  if (init.token) headers.authorization = `Bearer ${init.token}`;
  return fetch(`${API_URL}${path}`, {
    method: init.method,
    headers,
    body: init.body === undefined ? undefined : JSON.stringify(init.body),
    cache: "no-store",
  });
}

export function setSessionCookies(
  request: NextRequest,
  response: NextResponse,
  refreshToken: string,
): void {
  const secure = !isLocalhost(request);
  response.cookies.set({
    name: REFRESH_COOKIE,
    value: refreshToken,
    httpOnly: true,
    secure,
    sameSite: "lax",
    path: "/api/auth",
    maxAge: REFRESH_MAX_AGE_S,
  });
  response.cookies.set({
    name: SESSION_COOKIE,
    value: "1",
    httpOnly: true,
    secure,
    sameSite: "lax",
    path: "/",
    maxAge: REFRESH_MAX_AGE_S,
  });
}

export function clearSessionCookies(response: NextResponse): void {
  response.cookies.set({ name: REFRESH_COOKIE, value: "", path: "/api/auth", maxAge: 0 });
  response.cookies.set({ name: SESSION_COOKIE, value: "", path: "/", maxAge: 0 });
}

/** Auth routes change cookies, so refuse cross-site callers outright. */
export function isSameOrigin(request: NextRequest): boolean {
  const origin = request.headers.get("origin");
  return origin === null || origin === request.nextUrl.origin;
}
