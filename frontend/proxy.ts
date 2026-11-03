import { type NextRequest, NextResponse } from "next/server";

import { defaultLocale, isLocale, LOCALE_COOKIE, type Locale } from "@/lib/i18n/config";

const SESSION_COOKIE = "fc_session"; // set by the auth route handlers
const PUBLIC_PAGES = new Set(["login", "register"]);

function preferredLocale(request: NextRequest): Locale {
  const saved = request.cookies.get(LOCALE_COOKIE)?.value;
  if (isLocale(saved)) return saved;
  const accept = request.headers.get("accept-language") ?? "";
  return accept.toLowerCase().startsWith("ar") ? "ar" : defaultLocale;
}

/**
 * 1) Every page lives under /en or /ar: add the prefix the user prefers; /en goes to today.
 * 2) An optimistic login check: no session cookie means no need to render a private
 *    page first. The real check is the API rejecting the token.
 */
export function proxy(request: NextRequest) {
  const { pathname } = request.nextUrl;
  const [, first, second] = pathname.split("/");

  if (!isLocale(first)) {
    const url = request.nextUrl.clone();
    url.pathname = `/${preferredLocale(request)}${pathname === "/" ? "" : pathname}`;
    return NextResponse.redirect(url);
  }

  if (second === undefined || second === "") {
    // There is no page at /en itself: today is the home screen.
    const url = request.nextUrl.clone();
    url.pathname = `/${first}/today`;
    return NextResponse.redirect(url);
  }

  const isPrivate = !PUBLIC_PAGES.has(second);
  if (isPrivate && !request.cookies.has(SESSION_COOKIE)) {
    const url = request.nextUrl.clone();
    url.pathname = `/${first}/login`;
    url.search = "";
    return NextResponse.redirect(url);
  }

  const response = NextResponse.next();
  if (request.cookies.get(LOCALE_COOKIE)?.value !== first) {
    response.cookies.set({ name: LOCALE_COOKIE, value: first, path: "/", sameSite: "lax", maxAge: 31536000 });
  }
  return response;
}

export const config = {
  // Pages only: not the API routes, Next internals, the service worker or static files.
  matcher: ["/((?!api|_next|sw\\.js|manifest\\.webmanifest|icons|favicon\\.ico|.*\\.[a-z0-9]+$).*)"],
};
