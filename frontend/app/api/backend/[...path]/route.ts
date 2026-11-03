import { type NextRequest, NextResponse } from "next/server";

import { API_URL, forwardedFor } from "@/lib/server/backend";

/**
 * Same-origin proxy to the API, so the browser never needs CORS or the API's address.
 * The caller's access token (held in memory by the page) is passed through; cookies are
 * not. Token endpoints are not reachable here: refresh tokens stay server-side.
 */
const PASS_REQUEST_HEADERS = ["authorization", "content-type", "accept"];
// www-authenticate marks a rejected token (vs. e.g. a wrong password), so keep it.
const PASS_RESPONSE_HEADERS = [
  "content-type",
  "content-disposition",
  "retry-after",
  "www-authenticate",
];

async function forward(request: NextRequest, ctx: RouteContext<"/api/backend/[...path]">) {
  const { path } = await ctx.params;
  if ((path[0] === "auth" && path[1] !== "me") || path.some((p) => p === ".." || p === ".")) {
    return NextResponse.json({ detail: "Not found" }, { status: 404 });
  }
  const target = new URL(`${API_URL}/${path.map(encodeURIComponent).join("/")}`);
  target.search = request.nextUrl.search;

  const headers = new Headers({ "x-forwarded-for": forwardedFor(request) });
  for (const name of PASS_REQUEST_HEADERS) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  const hasBody = !["GET", "HEAD"].includes(request.method);

  let res: Response;
  try {
    res = await fetch(target, {
      method: request.method,
      headers,
      body: hasBody ? await request.arrayBuffer() : undefined,
      cache: "no-store",
    });
  } catch {
    return NextResponse.json({ detail: "The API is unreachable." }, { status: 502 });
  }
  const out = new Headers({ "cache-control": "no-store" });
  for (const name of PASS_RESPONSE_HEADERS) {
    const value = res.headers.get(name);
    if (value) out.set(name, value);
  }
  return new NextResponse(res.status === 204 ? null : res.body, {
    status: res.status,
    headers: out,
  });
}

export const GET = forward;
export const POST = forward;
export const PUT = forward;
export const PATCH = forward;
export const DELETE = forward;
