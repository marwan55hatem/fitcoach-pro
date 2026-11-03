import type { NextRequest } from "next/server";

import {
  forbidden,
  isSameOrigin,
  readJson,
  startSession,
  unavailable,
} from "@/lib/server/session";

export async function POST(request: NextRequest) {
  if (!isSameOrigin(request)) return forbidden();
  const body = await readJson(request);
  try {
    return await startSession(request, {
      email: String(body.email ?? ""),
      password: String(body.password ?? ""),
    });
  } catch {
    return unavailable();
  }
}
