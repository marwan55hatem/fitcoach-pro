import { type NextRequest, NextResponse } from "next/server";

import {
  callApi,
  clearSessionCookies,
  REFRESH_COOKIE,
  setSessionCookies,
  type TokenPair,
} from "@/lib/server/backend";
import { forbidden, isSameOrigin, type SessionOut, unavailable } from "@/lib/server/session";

/** Swap the refresh cookie for a new access token. The API rotates the refresh token. */
export async function POST(request: NextRequest) {
  if (!isSameOrigin(request)) return forbidden();
  const refreshToken = request.cookies.get(REFRESH_COOKIE)?.value;
  if (!refreshToken) {
    const response = NextResponse.json({ detail: "Not logged in." }, { status: 401 });
    clearSessionCookies(response);
    return response;
  }
  let res: Response;
  try {
    res = await callApi(request, "/auth/refresh", {
      method: "POST",
      body: { refresh_token: refreshToken },
    });
  } catch {
    return unavailable();
  }
  if (res.status === 401 || res.status === 422) {
    const response = NextResponse.json({ detail: "Session expired." }, { status: 401 });
    clearSessionCookies(response);
    return response;
  }
  if (!res.ok) {
    return NextResponse.json({ detail: "Try again." }, { status: res.status });
  }
  const tokens = (await res.json()) as TokenPair;
  const response = NextResponse.json<SessionOut>({
    access_token: tokens.access_token,
    expires_in: tokens.expires_in,
  });
  setSessionCookies(request, response, tokens.refresh_token);
  return response;
}
