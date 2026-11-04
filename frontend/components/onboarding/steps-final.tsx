"use client";

import { Camera, ScanLine, Sparkles } from "lucide-react";
import { useState } from "react";

import { FormError } from "@/components/field";
import { ProgramView } from "@/components/program-view";
import { InbodyReview } from "@/components/progress/inbody-review";
import { InbodyUpload } from "@/components/progress/inbody-upload";
import { PhotoCapture } from "@/components/progress/photos-section";
import { Button } from "@/components/ui/button";
import { api, ApiError } from "@/lib/api/client";
import { apiErrorMessage } from "@/lib/api/errors";
import { type ConsentType, type GeneratedProgram, type InbodyScan, PHOTO_VIEWS } from "@/lib/api/types";
import { useI18n } from "@/lib/i18n/provider";

import { StepFrame } from "./step-frame";

/** Optional extras: an InBody scan (read by the AI, checked by the user) and starting photos. */
export function ExtrasStep({
  consents,
  onDone,
}: {
  consents: Record<ConsentType, boolean>;
  onDone: () => void;
}) {
  const { dict } = useI18n();
  const t = dict.onboarding.extras;
  const [scans, setScans] = useState<InbodyScan[]>([]);
  const [confirmed, setConfirmed] = useState<string[]>([]);
  return (
    <StepFrame title={t.title} lead={t.lead} action={t.continue} onSubmit={onDone}>
      <section className="grid gap-3 rounded-2xl border border-border bg-card p-5" aria-labelledby="extras-inbody">
        <h2 id="extras-inbody" className="flex items-center gap-2 text-xl font-semibold">
          <ScanLine className="size-5 text-primary" aria-hidden />
          {t.inbodyTitle}
        </h2>
        <p className="text-sm text-muted-foreground">{t.inbodyBody}</p>
        <InbodyUpload onDraft={(scan) => setScans((list) => [scan, ...list])} />
        {scans
          .filter((scan) => !confirmed.includes(scan.id))
          .map((scan) => (
            <InbodyReview
              key={scan.id}
              scan={scan}
              onConfirmed={() => setConfirmed((ids) => [...ids, scan.id])}
            />
          ))}
        {confirmed.length > 0 && (
          <p className="text-sm text-success" role="status">
            {dict.progress.confirmed}
          </p>
        )}
      </section>
      <section className="grid gap-3 rounded-2xl border border-border bg-card p-5" aria-labelledby="extras-photos">
        <h2 id="extras-photos" className="flex items-center gap-2 text-xl font-semibold">
          <Camera className="size-5 text-primary" aria-hidden />
          {t.photosTitle}
        </h2>
        <p className="text-sm text-muted-foreground">{t.photosBody}</p>
        {consents.body_photos ? (
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
            {PHOTO_VIEWS.map((view) => (
              <PhotoCapture key={view} view={view} />
            ))}
          </div>
        ) : (
          <p className="rounded-xl bg-muted p-3 text-sm text-muted-foreground">{t.photosOff}</p>
        )}
      </section>
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
