"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Check, Sparkles, X } from "lucide-react";
import { useId, useState, type FormEvent } from "react";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { api, ApiError } from "@/lib/api/client";
import type { Proposal } from "@/lib/coach/stream";
import { fill, useI18n } from "@/lib/i18n/provider";
import { isolated } from "@/lib/numbers";

type Status = "pending" | "approved" | "rejected" | "expired";

export function describeProposal(proposal: Proposal, kinds: Record<string, string>): string {
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
 * A change the coach or the weekly review proposed. Approving checks it against the
 * safety limits again and then applies it; rejecting can keep a reason for the coach.
 */
export function ProposalCard({ proposal }: { proposal: Proposal }) {
  const { dict } = useI18n();
  const t = dict.coach;
  const id = useId();
  const client = useQueryClient();
  const [status, setStatus] = useState<Status>((proposal.status as Status | undefined) ?? "pending");
  const [note, setNote] = useState<string | null>(null);
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");
  const [remember, setRemember] = useState(false);

  const failed = (error: Error) => {
    if (error instanceof ApiError && error.code === "no_longer_valid") setNote(t.noLongerValid);
    else if (error instanceof ApiError && error.code === "expired") {
      setStatus("expired");
      setNote(t.expired);
    } else if (error instanceof ApiError && error.code === "not_pending") setNote(t.decided);
    else setNote(dict.common.errorGeneric);
  };
  // Approving changes the program or targets: refresh everything that shows them. Lists
  // of suggestions are only marked stale, so this card stays to show its result.
  const refresh = () => {
    void client.invalidateQueries({ queryKey: ["recommendations"], refetchType: "none" });
    void client.invalidateQueries({ predicate: (query) => query.queryKey[0] !== "recommendations" });
  };

  const approve = useMutation({
    mutationFn: () => api<{ applied_at: string | null }>(`/recommendations/${proposal.id}/approve`, { method: "POST" }),
    onSuccess: (data) => {
      setStatus("approved");
      setNote(data.applied_at ? t.approved : t.approvedAdvice);
      refresh();
    },
    onError: failed,
  });
  const reject = useMutation({
    mutationFn: () =>
      api(`/recommendations/${proposal.id}/reject`, {
        method: "POST",
        body: { reason: reason.trim() || null, remember: remember && reason.trim().length > 0 },
      }),
    onSuccess: () => {
      setStatus("rejected");
      setRejecting(false);
      setNote(null);
      refresh();
    },
    onError: failed,
  });
  const busy = approve.isPending || reject.isPending;

  const submitReject = (event: FormEvent) => {
    event.preventDefault();
    reject.mutate();
  };

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
      <p className="font-medium">{describeProposal(proposal, t.kinds)}</p>
      {proposal.reason && (
        <p className="text-muted-foreground" dir="auto">
          {proposal.reason}
        </p>
      )}
      <p className="text-xs text-muted-foreground">
        {status === "pending" ? t.pending : dict.review.status[status]}
      </p>
      {status === "pending" && !rejecting && (
        <div className="flex flex-wrap gap-2">
          <Button onClick={() => approve.mutate()} disabled={busy}>
            <Check aria-hidden />
            {t.approve}
          </Button>
          <Button variant="outline" onClick={() => setRejecting(true)} disabled={busy}>
            <X aria-hidden />
            {t.reject}
          </Button>
        </div>
      )}
      {status === "pending" && rejecting && (
        <form onSubmit={submitReject} className="grid gap-2">
          <Label htmlFor={`${id}-reason`}>{t.rejectReason}</Label>
          <Textarea
            id={`${id}-reason`}
            dir="auto"
            rows={2}
            maxLength={200}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          />
          <div className="flex min-h-11 items-center gap-3">
            <Checkbox
              id={`${id}-remember`}
              checked={remember}
              disabled={!reason.trim()}
              onCheckedChange={(value) => setRemember(value === true)}
            />
            <Label htmlFor={`${id}-remember`}>{t.rememberReason}</Label>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button type="submit" variant="destructive" disabled={busy}>
              {t.confirmReject}
            </Button>
            <Button type="button" variant="ghost" onClick={() => setRejecting(false)} disabled={busy}>
              {t.cancel}
            </Button>
          </div>
        </form>
      )}
      {note && (
        <p role="status" className="text-muted-foreground">
          {note}
        </p>
      )}
    </div>
  );
}
