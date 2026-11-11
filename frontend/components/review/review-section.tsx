"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarCheck, History, Loader2 } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { ProposalCard } from "@/components/coach/proposal-card";
import { Button } from "@/components/ui/button";
import { api, ApiError } from "@/lib/api/client";
import type { Recommendation, WeeklyReview } from "@/lib/api/types";
import { fill, useI18n } from "@/lib/i18n/provider";
import { useLocalePath } from "@/lib/i18n/use-locale-path";

export const PENDING_KEY = ["recommendations", "pending"] as const;
const LATEST_KEY = ["review", "latest"] as const;

/** The weekly summary and the inbox of changes waiting for the user's approval. */
export function ReviewSection() {
  const { dict, locale } = useI18n();
  const t = dict.review;
  const to = useLocalePath();
  const client = useQueryClient();
  const [note, setNote] = useState<string | null>(null);

  const latest = useQuery({ queryKey: LATEST_KEY, queryFn: () => api<WeeklyReview | null>("/review/latest") });
  const pending = useQuery({
    queryKey: PENDING_KEY,
    queryFn: () => api<Recommendation[]>("/recommendations?status=pending"),
  });
  const run = useMutation({
    mutationFn: () => api<WeeklyReview>("/review/run", { method: "POST" }),
    onSuccess: (review) => {
      setNote(null);
      client.setQueryData(LATEST_KEY, review);
      void client.invalidateQueries({ queryKey: PENDING_KEY });
    },
    onError: (error) =>
      setNote(error instanceof ApiError && error.code === "review_already_ran" ? t.ranToday : dict.common.errorGeneric),
  });

  const review = latest.data;
  const date = review
    ? new Date(`${review.run_on}T12:00:00Z`).toLocaleDateString(locale, { weekday: "long", day: "numeric", month: "long" })
    : null;
  const items = pending.data ?? [];

  return (
    <section aria-labelledby="review-title" className="grid gap-4 rounded-2xl border border-border bg-card p-5">
      <div className="flex items-start gap-3">
        <CalendarCheck className="mt-1 size-5 shrink-0 text-gold" aria-hidden />
        <div className="grid flex-1 gap-1">
          <h2 id="review-title" className="text-xl font-semibold">
            {t.summaryTitle}
          </h2>
          {date && <p className="text-xs text-muted-foreground">{fill(t.runOn, { date })}</p>}
          <p className="leading-relaxed" dir="auto">
            {review ? (review.summary ?? t.summaryRules) : t.summaryNone}
          </p>
        </div>
      </div>
      <div className="flex flex-wrap gap-2">
        <Button variant="outline" onClick={() => run.mutate()} disabled={run.isPending}>
          {run.isPending && <Loader2 className="animate-spin" aria-hidden />}
          {t.run}
        </Button>
        <Button asChild variant="ghost">
          <Link href={to("/review")}>
            <History aria-hidden />
            {t.history}
          </Link>
        </Button>
      </div>
      {note && (
        <p role="status" className="text-sm text-muted-foreground">
          {note}
        </p>
      )}

      <div className="grid gap-3">
        <h3 className="font-semibold">{t.inboxTitle}</h3>
        {pending.isSuccess && items.length === 0 && <p className="text-sm text-muted-foreground">{t.inboxEmpty}</p>}
        {items.map((item) => (
          <ProposalCard key={item.id} proposal={item} />
        ))}
      </div>
    </section>
  );
}
