import "server-only";

import { type NextRequest, NextResponse } from "next/server";

import { callApi, isSameOrigin, setSessionCookies, type TokenPair } from "./backend";

/** What the browser gets: the short-lived access token only, never the refresh token. */
export type SessionOut = { access_token: string; expires_in: number };

export function forbidden(): NextResponse {
  return NextResponse.json({ detail: "Cross-site request refused." }, { status: 403 });
}

/** Pass the API's error through unchanged (status and detail), or a 502 if it is down. */
export async function relayError(res: Response): Promise<NextResponse> {
  const body: unknown = await res.json().catch(() => ({ detail: res.statusText }));
  const headers = new Headers();
  const retry = res.headers.get("retry-after");
  if (retry) headers.set("retry-after", retry);
  return NextResponse.json(body, { status: res.status, headers });
}

export function unavailable(): NextResponse {
  return NextResponse.json({ detail: "The API is unreachable." }, { status: 502 });
}

export async function startSession(
  request: NextRequest,
  credentials: { email: string; password: string },
): Promise<NextResponse> {
  const res = await callApi(request, "/auth/login", { method: "POST", body: credentials });
  if (!res.ok) return relayError(res);
  const tokens = (await res.json()) as TokenPair;
  const response = NextResponse.json<SessionOut>({
    access_token: tokens.access_token,
    expires_in: tokens.expires_in,
  });
  setSessionCookies(request, response, tokens.refresh_token);
  return response;
}

export async function readJson(request: NextRequest): Promise<Record<string, unknown>> {
  const body: unknown = await request.json().catch(() => null);
  return body && typeof body === "object" ? (body as Record<string, unknown>) : {};
}

export { isSameOrigin };
