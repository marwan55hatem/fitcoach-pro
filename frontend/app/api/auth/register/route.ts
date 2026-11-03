import type { NextRequest } from "next/server";

import { callApi } from "@/lib/server/backend";
import {
  forbidden,
  isSameOrigin,
  readJson,
  relayError,
  startSession,
  unavailable,
} from "@/lib/server/session";

/** Create the account, then log straight in so the browser gets a session cookie. */
export async function POST(request: NextRequest) {
  if (!isSameOrigin(request)) return forbidden();
  const body = await readJson(request);
  const email = String(body.email ?? "");
  const password = String(body.password ?? "");
  const displayName = typeof body.display_name === "string" ? body.display_name : null;
  try {
    const res = await callApi(request, "/auth/register", {
      method: "POST",
      body: { email, password, display_name: displayName || null },
    });
    if (!res.ok) return relayError(res);
    return await startSession(request, { email, password });
  } catch {
    return unavailable();
  }
}
