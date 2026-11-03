"use client";

import { Camera, ScanLine, Sparkles } from "lucide-react";
import { useState } from "react";

import { FormError } from "@/components/field";
import { ProgramView } from "@/components/program-view";
import { Button } from "@/components/ui/button";
import { api, ApiError } from "@/lib/api/client";
import { apiErrorMessage } from "@/lib/api/errors";
import type { GeneratedProgram } from "@/lib/api/types";
import { useI18n } from "@/lib/i18n/provider";

import { StepFrame } from "./step-frame";

/** Placeholder for the InBody and body-photo steps, which arrive with their backends. */
export function ExtrasStep({ onDone }: { onDone: () => void }) {
  const { dict } = useI18n();
  const t = dict.onboarding.extras;
  return (
    <StepFrame title={t.title} action={dict.common.skip} onSubmit={onDone}>
      <div className="flex gap-4 rounded-2xl border border-dashed border-border p-5 text-muted-foreground">
        <span className="flex gap-2" aria-hidden>
          <ScanLine className="size-6" />
          <Camera className="size-6" />
        </span>
        <p>{t.body}</p>
      </div>
    </StepFrame>
  );
}

export function SummaryStep({
  useAi,
  planAllowed,
  onAccepted,
}: {
  useAi: boolean;
  planAllowed: boolean;
  onAccepted: () => Promise<void>;
}) {
  const { dict } = useI18n();
  const t = dict.onboarding.summary;
  const [program, setProgram] = useState<GeneratedProgram | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const generate = async () => {
    setBusy(true);
    setError(null);
    try {
      setProgram(
        await api<GeneratedProgram>("/programs/generate", {
          method: "POST",
          body: { use_ai: useAi },
        }),
      );
    } catch (err) {
      setError(
        err instanceof ApiError && err.code === "no_valid_program"
          ? t.noValidProgram
          : err instanceof ApiError && err.code === "plan_access_denied"
            ? t.blocked
            : apiErrorMessage(err, dict),
      );
    } finally {
      setBusy(false);
    }
  };

  const accept = async () => {
    if (!program) return;
    setBusy(true);
    setError(null);
    try {
      await api(`/programs/${program.id}/accept`, { method: "POST" });
      await onAccepted();
    } catch (err) {
      setError(apiErrorMessage(err, dict));
      setBusy(false);
    }
  };

  if (!planAllowed) {
    return (
      <div className="grid gap-3">
        <h1 className="text-4xl font-bold">{t.title}</h1>
        <p className="text-muted-foreground">{t.blocked}</p>
      </div>
    );
  }

  if (!program) {
    return (
      <StepFrame
        title={t.title}
        lead={t.lead}
        action={busy ? t.generating : t.generate}
        busy={busy}
        error={error}
        onSubmit={generate}
      >
        {busy && (
          <p role="status" className="flex items-center gap-2 text-muted-foreground">
            <Sparkles className="size-4 animate-pulse text-gold" aria-hidden />
            {t.generating}
          </p>
        )}
      </StepFrame>
    );
  }

  const source =
    program.source === "ai" ? t.byAi : program.fallback_reason === "ai_unavailable" && useAi ? t.aiUnavailable : t.byRules;

  return (
    <StepFrame
      title={t.title}
      lead={t.lead}
      action={t.accept}
      busy={busy}
      error={null}
      onSubmit={accept}
      secondary={
        <Button type="button" variant="ghost" disabled={busy} onClick={generate}>
          {t.regenerate}
        </Button>
      }
    >
      <p className="text-sm text-muted-foreground">{source}</p>
      {program.warnings.length > 0 && (
        <div className="grid gap-1 rounded-2xl border border-gold/40 bg-gold/5 p-4">
          <p className="font-medium text-gold">{t.warnings}</p>
          <ul className="list-disc space-y-1 ps-5 text-sm text-muted-foreground" dir="auto">
            {program.warnings.map((w, i) => (
              <li key={i}>{w.message}</li>
            ))}
          </ul>
        </div>
      )}
      <ProgramView program={program} />
      <FormError message={error} />
    </StepFrame>
  );
}
