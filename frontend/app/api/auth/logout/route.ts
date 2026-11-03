import { type NextRequest, NextResponse } from "next/server";

import { callApi, clearSessionCookies, REFRESH_COOKIE } from "@/lib/server/backend";
import { forbidden, isSameOrigin } from "@/lib/server/session";

/** Revoke the refresh token on the API and clear the cookies, even if the API is down. */
export async function POST(request: NextRequest) {
  if (!isSameOrigin(request)) return forbidden();
  const refreshToken = request.cookies.get(REFRESH_COOKIE)?.value;
  if (refreshToken) {
    await callApi(request, "/auth/logout", {
      method: "POST",
      body: { refresh_token: refreshToken },
    }).catch(() => undefined);
  }
  const response = new NextResponse(null, { status: 204 });
  clearSessionCookies(response);
  return response;
}
