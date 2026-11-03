"use client";

import { useQuery } from "@tanstack/react-query";
import { Apple, Dumbbell, House, LineChart, MessageCircle, Settings } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, type ReactNode } from "react";

import { Brand } from "@/components/brand";
import { api } from "@/lib/api/client";
import type { OnboardingStatus } from "@/lib/api/types";
import { useAuth } from "@/lib/auth/auth-provider";
import { useI18n } from "@/lib/i18n/provider";
import { useLocalePath } from "@/lib/i18n/use-locale-path";
import { cn } from "@/lib/utils";

const TABS = [
  { key: "today", icon: House },
  { key: "train", icon: Dumbbell },
  { key: "food", icon: Apple },
  { key: "progress", icon: LineChart },
  { key: "coach", icon: MessageCircle },
] as const;

export function Splash() {
  const { dict } = useI18n();
  return (
    <div className="grid min-h-dvh place-items-center" role="status" aria-live="polite">
      <div className="grid justify-items-center gap-3">
        <Brand />
        <p className="text-sm text-muted-foreground">{dict.common.loading}</p>
      </div>
    </div>
  );
}

/** Sends visitors who are logged out to login, and unfinished setups to onboarding. */
export function useSignedInUser(): { ready: boolean } {
  const { status } = useAuth();
  const router = useRouter();
  const to = useLocalePath();
  useEffect(() => {
    if (status === "anonymous") router.replace(to("/login"));
  }, [status, router, to]);
  return { ready: status === "authenticated" };
}

export function AppShell({ children }: { children: ReactNode }) {
  const { dict } = useI18n();
  const { ready } = useSignedInUser();
  const router = useRouter();
  const to = useLocalePath();
  const pathname = usePathname();

  const status = useQuery({
    queryKey: ["onboarding-status"],
    queryFn: () => api<OnboardingStatus>("/onboarding/status"),
    enabled: ready,
  });
  // Only redirect on a fresh answer, never on a stale cached one being refetched.
  const complete = status.isFetching ? undefined : status.data?.onboarding_complete;
  useEffect(() => {
    if (complete === false) router.replace(to("/onboarding"));
  }, [complete, router, to]);

  if (!ready || !status.data?.onboarding_complete) return <Splash />;

  const isActive = (key: string) => pathname.split("/")[2] === key;
  const link = (key: string, Icon: typeof House, label: string, layout: "tab" | "side") => (
    <Link
      key={key}
      href={to(`/${key}`)}
      aria-current={isActive(key) ? "page" : undefined}
      className={cn(
        "flex items-center rounded-xl text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring",
        layout === "tab"
          ? "min-h-14 flex-1 flex-col justify-center gap-1 text-[0.7rem]"
          : "min-h-11 gap-3 px-3 text-sm hover:bg-accent",
        isActive(key) && "text-primary hover:text-primary",
        isActive(key) && layout === "side" && "bg-accent",
      )}
    >
      <Icon className={layout === "tab" ? "size-6" : "size-5"} aria-hidden />
      <span>{label}</span>
    </Link>
  );

  return (
    <div className="md:grid md:min-h-dvh md:grid-cols-[15rem_1fr]">
      <aside className="hidden border-e border-sidebar-border bg-sidebar p-4 md:flex md:flex-col md:gap-6">
        <Brand className="px-2 pt-2" />
        <nav className="grid gap-1" aria-label={dict.common.appName}>
          {TABS.map(({ key, icon }) => link(key, icon, dict.nav[key], "side"))}
        </nav>
        <div className="mt-auto">{link("settings", Settings, dict.nav.settings, "side")}</div>
      </aside>

      <div className="flex min-h-dvh flex-col">
        <header className="flex items-center justify-between px-5 pt-[max(0.75rem,env(safe-area-inset-top))] pb-2 md:hidden">
          <Brand />
          <Link
            href={to("/settings")}
            aria-label={dict.nav.settings}
            className="grid size-11 place-items-center rounded-xl text-muted-foreground hover:bg-accent hover:text-foreground"
          >
            <Settings className="size-5" aria-hidden />
          </Link>
        </header>
        <main className="mx-auto w-full max-w-3xl flex-1 px-5 pb-28 pt-4 md:px-10 md:pb-10 md:pt-10">
          {children}
        </main>
        <nav
          aria-label={dict.common.appName}
          className="fixed inset-x-0 bottom-0 z-40 flex border-t border-border bg-background/95 px-2 pb-[env(safe-area-inset-bottom)] backdrop-blur md:hidden"
        >
          {TABS.map(({ key, icon }) => link(key, icon, dict.nav[key], "tab"))}
        </nav>
      </div>
    </div>
  );
}
