"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { toast } from "sonner";

import { FormError } from "@/components/field";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api } from "@/lib/api/client";
import type { WeightHistory } from "@/lib/api/types";
import { fill, useI18n } from "@/lib/i18n/provider";
import { formatKg, parseNumber } from "@/lib/numbers";

const KEY = ["weight"] as const;

/** Log today's weight in one tap; shows the 7-day average and the weekly change. */
export function WeightCard() {
  const { dict } = useI18n();
  const t = dict.today;
  const client = useQueryClient();
  const history = useQuery({ queryKey: KEY, queryFn: () => api<WeightHistory>("/weight?days=30") });
  const [value, setValue] = useState("");
  const [invalid, setInvalid] = useState(false);
  const save = useMutation({
    mutationFn: (weight_kg: number) => api<WeightHistory>("/weight", { method: "POST", body: { weight_kg } }),
    onSuccess: (data) => {
      client.setQueryData(KEY, data);
      setValue("");
      toast.success(t.weightSaved);
    },
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const kg = parseNumber(value);
    if (kg === null || kg < 30 || kg > 300) {
      setInvalid(true);
      return;
    }
    setInvalid(false);
    save.mutate(kg);
  };

  const data = history.data;
  const change = data?.weekly_change_kg;
  return (
    <section className="grid gap-3 rounded-2xl border border-border bg-card p-5" aria-labelledby="weight-title">
      <h2 id="weight-title" className="text-xl font-semibold">
        {t.weightTitle}
      </h2>
      {data?.latest_kg != null ? (
        <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
          <span className="font-heading text-5xl font-bold tabular-nums" dir="ltr">
            {formatKg(data.latest_kg)}
            <span className="ms-1 text-xl text-muted-foreground">{dict.train.weight}</span>
          </span>
          <span className="grid text-sm text-muted-foreground">
            {data.average_7d != null && <span>{fill(t.weightLine, { average: formatKg(data.average_7d) })}</span>}
            {change != null && (
              <span>
                {fill(t.weightChange, { change: `${change > 0 ? "+" : ""}${formatKg(change)}` })}
              </span>
            )}
          </span>
        </div>
      ) : (
        history.isSuccess && <p className="text-sm text-muted-foreground">{t.weightEmpty}</p>
      )}
      <form onSubmit={submit} className="flex items-end gap-3" noValidate>
        <div className="grid flex-1 gap-2">
          <Label htmlFor="weight-today">{t.weightLabel}</Label>
          <Input
            id="weight-today"
            inputMode="decimal"
            autoComplete="off"
            dir="ltr"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            aria-invalid={invalid || undefined}
            aria-describedby={invalid ? "weight-today-error" : undefined}
          />
        </div>
        <Button type="submit" disabled={save.isPending}>
          {t.weightSave}
        </Button>
      </form>
      {invalid && (
        <p id="weight-today-error" role="alert" className="text-sm text-destructive">
          {t.weightRange}
        </p>
      )}
      <FormError message={save.isError || history.isError ? dict.common.errorGeneric : null} />
    </section>
  );
}
