"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";

import { ProgramView } from "@/components/program-view";
import { Button } from "@/components/ui/button";
import { api, ApiError } from "@/lib/api/client";
import type { Program } from "@/lib/api/types";
import { useAuth } from "@/lib/auth/auth-provider";
import { fill, useI18n } from "@/lib/i18n/provider";
import { useLocalePath } from "@/lib/i18n/use-locale-path";

export function TodayView() {
  const { dict } = useI18n();
  const { user } = useAuth();
  const to = useLocalePath();
  const program = useQuery({
    queryKey: ["program", "current"],
    queryFn: async () => {
      try {
        return await api<Program>("/programs/current");
      } catch (error) {
        if (error instanceof ApiError && error.status === 404) return null;
        throw error;
      }
    },
  });

  return (
    <section className="grid gap-6">
      <div>
        <h1 className="text-4xl font-bold">
          {user?.display_name ? fill(dict.today.greeting, { name: user.display_name }) : dict.today.title}
        </h1>
      </div>
      {program.isPending && <p className="text-muted-foreground">{dict.common.loading}</p>}
      {program.isError && <p className="text-destructive">{dict.common.errorGeneric}</p>}
      {program.data === null && (
        <div className="grid gap-3 rounded-2xl border border-border bg-card p-5">
          <p>{dict.today.noProgram}</p>
          <Button asChild className="w-fit">
            <Link href={to("/onboarding")}>{dict.today.finishSetup}</Link>
          </Button>
        </div>
      )}
      {program.data && (
        <div className="grid gap-3">
          <h2 className="text-2xl font-semibold">{dict.today.programTitle}</h2>
          <ProgramView program={program.data} />
        </div>
      )}
    </section>
  );
}
