"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";
import Link from "next/link";

import { describeProposal, ProposalCard } from "@/components/coach/proposal-card";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api/client";
import type { Recommendation } from "@/lib/api/types";
import { fill, useI18n } from "@/lib/i18n/provider";
import { useLocalePath } from "@/lib/i18n/use-locale-path";
import { cn } from "@/lib/utils";

const TONE: Record<string, string> = {
  approved: "text-success",
  rejected: "text-destructive",
  expired: "text-muted-foreground",
};

/** Every suggestion from the coach and the weekly reviews, newest first. */
export function ReviewHistory() {
  const { dict, locale } = useI18n();
  const t = dict.review;
  const to = useLocalePath();
  const all = useQuery({
    queryKey: ["recommendations", "all"],
    queryFn: () => api<Recommendation[]>("/recommendations"),
  });
  const day = (iso: string) => new Date(iso).toLocaleDateString(locale, { day: "numeric", month: "short", year: "numeric" });

  return (
    <section className="grid gap-4">
      <Button asChild variant="ghost" className="w-fit">
        <Link href={to("/today")}>
          <ArrowLeft className="rtl:rotate-180" aria-hidden />
          {t.back}
        </Link>
      </Button>
      <div className="grid gap-1">
        <h1 className="text-4xl font-bold">{t.historyTitle}</h1>
        <p className="text-muted-foreground">{t.historyLead}</p>
      </div>
      {all.isSuccess && all.data.length === 0 && <p className="text-muted-foreground">{t.historyEmpty}</p>}
      <ol className="grid gap-3">
        {all.data?.map((item) =>
          item.status === "pending" ? (
            <li key={item.id}>
              <ProposalCard proposal={item} />
            </li>
          ) : (
            <li key={item.id} className="grid gap-1 rounded-xl border border-border bg-card p-3 text-sm">
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <p className="font-medium">{describeProposal(item, dict.coach.kinds)}</p>
                <span className={cn("text-xs font-semibold", TONE[item.status])}>{t.status[item.status]}</span>
              </div>
              {item.reason && (
                <p className="text-muted-foreground" dir="auto">
                  {item.reason}
                </p>
              )}
              {item.decision_note && (
                <p className="text-muted-foreground" dir="auto">
                  {fill(t.yourReason, { reason: item.decision_note })}
                </p>
              )}
              <p className="flex flex-wrap gap-x-3 text-xs text-muted-foreground">
                <span>{item.source === "review" ? t.fromReview : t.fromCoach}</span>
                <span>{day(item.decided_at ?? item.created_at)}</span>
              </p>
            </li>
          ),
        )}
      </ol>
    </section>
  );
}
