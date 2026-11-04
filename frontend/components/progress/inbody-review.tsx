"use client";

import { useMutation } from "@tanstack/react-query";
import { AlertTriangle } from "lucide-react";
import { useState } from "react";

import { FormError } from "@/components/field";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api/client";
import { apiErrorMessage } from "@/lib/api/errors";
import { INBODY_FIELDS, type InbodyField, type InbodyScan } from "@/lib/api/types";
import { useI18n } from "@/lib/i18n/provider";
import { parseNumber } from "@/lib/numbers";
import { cn } from "@/lib/utils";

const LOW_CONFIDENCE = 0.6;

function text(value: number | null | undefined): string {
  return value === null || value === undefined ? "" : String(value);
}

/**
 * The AI's reading of a scan, as a table to check against the printout. Doubtful fields
 * (low confidence or flagged by the rules) are highlighted; nothing counts until confirm.
 */
export function InbodyReview({
  scan,
  onConfirmed,
}: {
  scan: InbodyScan;
  onConfirmed: (scan: InbodyScan) => void;
}) {
  const { dict } = useI18n();
  const t = dict.progress;
  const [current, setCurrent] = useState(scan);
  const [values, setValues] = useState<Record<string, string>>(() => ({
    taken_on: scan.taken_on,
    ...Object.fromEntries(INBODY_FIELDS.map((f) => [f, text(scan[f])])),
  }));

  const changes = (): Record<string, string | number | null> => {
    const out: Record<string, string | number | null> = {};
    if (values.taken_on && values.taken_on !== current.taken_on) out.taken_on = values.taken_on;
    for (const field of INBODY_FIELDS) {
      if (values[field] !== text(current[field])) out[field] = parseNumber(values[field]);
    }
    return out;
  };
  const save = useMutation({
    mutationFn: () => api<InbodyScan>(`/inbody/${current.id}`, { method: "PUT", body: changes() }),
    onSuccess: setCurrent,
  });
  const confirm = useMutation({
    mutationFn: async () => {
      if (Object.keys(changes()).length > 0) {
        setCurrent(await api<InbodyScan>(`/inbody/${current.id}`, { method: "PUT", body: changes() }));
      }
      return api<InbodyScan>(`/inbody/${current.id}/confirm`, { method: "POST" });
    },
    onSuccess: onConfirmed,
  });

  const flagsFor = (field: string) => current.flags.filter((f) => f.field === field);
  const rows: (InbodyField | "taken_on")[] = ["taken_on", ...INBODY_FIELDS];
  const busy = save.isPending || confirm.isPending;
  const error = save.error ?? confirm.error;

  return (
    <div className="grid gap-4 rounded-2xl border border-primary/50 bg-background p-4" role="group" aria-labelledby={`scan-${scan.id}`}>
      <div className="grid gap-1">
        <h3 id={`scan-${scan.id}`} className="text-lg font-semibold">
          {t.reviewTitle}
        </h3>
        <p className="text-sm text-muted-foreground">{t.reviewLead}</p>
      </div>
      <div className="grid gap-2">
        {rows.map((field) => {
          const flags = flagsFor(field);
          const confidence = field === "taken_on" ? 1 : (current.confidence[field] ?? 1);
          const doubtful = flags.length > 0 || confidence < LOW_CONFIDENCE;
          const id = `${scan.id}-${field}`;
          return (
            <div
              key={field}
              className={cn(
                "grid grid-cols-[1fr_8.5rem] items-center gap-x-3 gap-y-1 rounded-xl px-3 py-2",
                doubtful ? "border border-gold/60 bg-gold/10" : "bg-card",
              )}
            >
              <label htmlFor={id} className="flex items-center gap-2 text-sm">
                {doubtful && <AlertTriangle className="size-4 shrink-0 text-gold" aria-hidden />}
                {t.fields[field]}
                {doubtful && flags.length === 0 && <span className="text-xs text-gold">({t.notSure})</span>}
              </label>
              <Input
                id={id}
                type={field === "taken_on" ? "date" : "text"}
                inputMode={field === "taken_on" ? undefined : "decimal"}
                dir="ltr"
                className="h-11 text-end font-heading text-lg tabular-nums"
                aria-invalid={flags.length > 0 || undefined}
                aria-describedby={flags.length > 0 ? `${id}-flags` : undefined}
                value={values[field] ?? ""}
                onChange={(e) => setValues((v) => ({ ...v, [field]: e.target.value }))}
              />
              {flags.length > 0 && (
                <p id={`${id}-flags`} className="col-span-2 text-xs text-gold">
                  {flags.map((f) => t.flagCodes[f.code] ?? f.message).join(" ")}
                </p>
              )}
            </div>
          );
        })}
      </div>
      <FormError message={error ? apiErrorMessage(error, dict) : null} />
      <div className="flex flex-wrap gap-3">
        <Button type="button" disabled={busy} onClick={() => confirm.mutate()}>
          {t.confirm}
        </Button>
        <Button type="button" variant="outline" disabled={busy || Object.keys(changes()).length === 0} onClick={() => save.mutate()}>
          {t.saveChanges}
        </Button>
      </div>
    </div>
  );
}
