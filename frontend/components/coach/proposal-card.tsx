"use client";

import { useMutation } from "@tanstack/react-query";
import { Check, Sparkles, X } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { api, ApiError } from "@/lib/api/client";
import type { Proposal } from "@/lib/coach/stream";
import { fill, useI18n } from "@/lib/i18n/provider";
import { isolated } from "@/lib/numbers";

function describe(proposal: Proposal, kinds: Record<string, string>): string {
  const p = proposal.payload;
  const template = kinds[proposal.kind] ?? proposal.kind;
  const change = typeof p.change_kcal === "number" ? p.change_kcal : 0;
  return fill(template, {
    from: String(p.from_kg ?? ""),
    to: String(p.to_kg ?? ""),
    change: isolated(`${change > 0 ? "+" : ""}${change}`),
    value: String(p.g_per_kg ?? ""),
  });
}

/**
 * A change the coach proposed. It is checked against the safety limits again when
 * approved; applying it arrives with the weekly review, so approve says so for now.
 */
export function ProposalCard({ proposal }: { proposal: Proposal }) {
  const { dict } = useI18n();
  const t = dict.coach;
  const [state, setState] = useState<"pending" | "rejected">("pending");
  const [note, setNote] = useState<string | null>(null);

  const approve = useMutation({
    mutationFn: () => api(`/recommendations/${proposal.id}/approve`, { method: "POST" }),
    onError: (error) => {
      if (error instanceof ApiError && error.code === "approval_not_ready") setNote(t.approveSoon);
      else if (error instanceof ApiError && error.code === "no_longer_valid") setNote(t.noLongerValid);
      else setNote(dict.common.errorGeneric);
    },
  });
  const reject = useMutation({
    mutationFn: () => api(`/recommendations/${proposal.id}/reject`, { method: "POST" }),
    onSuccess: () => setState("rejected"),
    onError: () => setNote(dict.common.errorGeneric),
  });

  return (
    <div
      role="group"
      aria-label={t.proposalTitle}
      className="grid gap-2 rounded-xl border border-gold/50 bg-gold/5 p-3 text-sm"
    >
      <p className="flex items-center gap-2 font-semibold text-gold">
        <Sparkles className="size-4" aria-hidden />
        {t.proposalTitle}
      </p>
      <p className="font-medium">{describe(proposal, t.kinds)}</p>
      {proposal.reason && (
        <p className="text-muted-foreground" dir="auto">
          {proposal.reason}
        </p>
      )}
      <p className="text-xs text-muted-foreground">{state === "rejected" ? t.rejected : t.pending}</p>
      {state === "pending" && (
        <div className="flex flex-wrap gap-2">
          <Button size="default" onClick={() => approve.mutate()} disabled={approve.isPending || reject.isPending}>
            <Check aria-hidden />
            {t.approve}
          </Button>
          <Button
            size="default"
            variant="outline"
            onClick={() => reject.mutate()}
            disabled={approve.isPending || reject.isPending}
          >
            <X aria-hidden />
            {t.reject}
          </Button>
        </div>
      )}
      {note && (
        <p role="status" className="text-muted-foreground">
          {note}
        </p>
      )}
    </div>
  );
}
