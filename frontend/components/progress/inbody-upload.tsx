"use client";

import { useMutation } from "@tanstack/react-query";
import { FileUp, Loader2 } from "lucide-react";
import { useId, useState } from "react";

import { FormError } from "@/components/field";
import { api, ApiError, uploadFile } from "@/lib/api/client";
import { apiErrorMessage } from "@/lib/api/errors";
import type { InbodyScan } from "@/lib/api/types";
import { useConsents } from "@/lib/consents";
import { useI18n } from "@/lib/i18n/provider";

/** Pick a photo or PDF of the printout; it is uploaded privately and read into a draft. */
export function InbodyUpload({ onDraft }: { onDraft: (scan: InbodyScan) => void }) {
  const { dict } = useI18n();
  const t = dict.progress;
  const id = useId();
  const consents = useConsents();
  const [stage, setStage] = useState<"uploading" | "reading" | null>(null);

  const read = useMutation({
    mutationFn: async (file: File) => {
      setStage("uploading");
      const stored = await uploadFile("inbody", file);
      setStage("reading");
      return api<InbodyScan>("/inbody/analyze", { method: "POST", body: { file_id: stored.id } });
    },
    onSuccess: onDraft,
    onSettled: () => setStage(null),
  });

  if (consents.data && !consents.data.ai_processing) {
    return <p className="rounded-xl bg-muted p-3 text-sm text-muted-foreground">{t.aiOff}</p>;
  }
  const error = read.error;
  const message =
    error instanceof ApiError && error.code === "too_large"
      ? dict.food.tooLarge
      : error instanceof ApiError && (error.code === "invalid_image" || error.code === "unsupported_type")
        ? t.unreadable
        : error
          ? apiErrorMessage(error, dict)
          : null;

  return (
    <div className="grid gap-2">
      <label
        htmlFor={`${id}-scan`}
        aria-disabled={read.isPending}
        className="flex min-h-12 w-fit cursor-pointer items-center gap-2 rounded-xl border border-input bg-card px-4 font-medium hover:bg-accent focus-within:outline-2 focus-within:outline-ring"
      >
        {read.isPending ? <Loader2 className="size-4 animate-spin" aria-hidden /> : <FileUp className="size-4" aria-hidden />}
        {stage === "uploading" ? t.uploading : stage === "reading" ? t.reading : t.addScan}
      </label>
      <input
        id={`${id}-scan`}
        type="file"
        accept="image/*,application/pdf"
        className="sr-only"
        aria-label={t.addScan}
        disabled={read.isPending}
        onChange={(e) => {
          const file = e.target.files?.[0];
          e.target.value = "";
          if (file) read.mutate(file);
        }}
      />
      <p className="sr-only" aria-live="polite">
        {stage === "uploading" ? t.uploading : stage === "reading" ? t.reading : ""}
      </p>
      <FormError message={message} />
    </div>
  );
}
