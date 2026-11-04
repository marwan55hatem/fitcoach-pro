"use client";

import { useMutation } from "@tanstack/react-query";
import { AlertTriangle, HelpCircle, Minus, Plus } from "lucide-react";
import { useState } from "react";

import { FormError } from "@/components/field";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api } from "@/lib/api/client";
import { apiErrorMessage } from "@/lib/api/errors";
import type { Meal } from "@/lib/api/types";
import {
  checkedMeal,
  confidenceLevel,
  PORTION_STEPS,
  type Portion,
  scaled,
  totalsOf,
} from "@/lib/food/estimate";
import { fill, useI18n } from "@/lib/i18n/provider";
import { isolated } from "@/lib/numbers";
import { cn } from "@/lib/utils";

const CONFIDENCE_TONE = {
  high: "border-success/50 bg-success/10 text-success",
  medium: "border-gold/50 bg-gold/10 text-gold",
  low: "border-destructive/50 bg-destructive/10 text-destructive",
} as const;

function stepFrom(factor: number, direction: 1 | -1): number {
  const index = PORTION_STEPS.findIndex((s) => Math.abs(s - factor) < 1e-9);
  const next = (index === -1 ? PORTION_STEPS.indexOf(1) : index) + direction;
  return PORTION_STEPS[Math.min(PORTION_STEPS.length - 1, Math.max(0, next))];
}

/**
 * Check an AI estimate before it counts: itemised macros and ranges, portions that
 * scale an item up or down, the AI's questions and any rule warnings.
 */
export function DraftEditor({
  draft,
  onSaved,
  onDiscarded,
}: {
  draft: Meal;
  onSaved: (meal: Meal) => void;
  onDiscarded: () => void;
}) {
  const { dict } = useI18n();
  const t = dict.food;
  const [name, setName] = useState(draft.name);
  const [portions, setPortions] = useState<Portion[]>(draft.items.map((item) => ({ item, factor: 1 })));
  const [keep, setKeep] = useState(false);
  const { questions, flags } = draft;
  const level = confidenceLevel(draft.confidence);

  const save = useMutation({
    mutationFn: async () => {
      const meal = await api<Meal>(`/food/logs/${draft.id}`, {
        method: "PUT",
        body: checkedMeal(draft, name.trim() || draft.name, portions),
      });
      if (keep) await api("/food/personal", { method: "POST", body: { meal_log_id: meal.id } });
      return meal;
    },
    onSuccess: onSaved,
  });
  const discard = useMutation({
    mutationFn: () => api(`/nutrition/meals/${draft.id}`, { method: "DELETE" }),
    onSuccess: onDiscarded,
  });

  const items = portions.map(scaled);
  const totals = totalsOf(items);
  const setFactor = (index: number, factor: number) =>
    setPortions((list) => list.map((p, i) => (i === index ? { ...p, factor } : p)));

  return (
    <div className="grid gap-4 rounded-2xl border border-primary/50 bg-background p-4" role="group" aria-labelledby={`review-${draft.id}`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 id={`review-${draft.id}`} className="text-lg font-semibold">
          {t.reviewTitle}
        </h3>
        <span className="flex flex-wrap gap-1.5">
          <span className="rounded-full border border-gold/50 px-2.5 py-0.5 text-xs text-gold">{t.draftBadge}</span>
          <span className={cn("rounded-full border px-2.5 py-0.5 text-xs font-medium", CONFIDENCE_TONE[level])}>
            {t.confidence[level]}
          </span>
        </span>
      </div>
      <p className="text-sm text-muted-foreground">{t.reviewLead}</p>

      {questions.length > 0 && (
        <div className="grid gap-1 rounded-xl bg-muted p-3 text-sm">
          <p className="flex items-center gap-2 font-medium">
            <HelpCircle className="size-4 text-gold" aria-hidden />
            {t.questions}
          </p>
          <ul className="list-disc ps-6 text-muted-foreground" dir="auto">
            {questions.map((q) => (
              <li key={q}>{q}</li>
            ))}
          </ul>
        </div>
      )}
      {flags.length > 0 && (
        <div className="grid gap-1 rounded-xl border border-gold/40 p-3 text-sm" role="alert">
          <p className="flex items-center gap-2 font-medium text-gold">
            <AlertTriangle className="size-4" aria-hidden />
            {t.flagged}
          </p>
          <ul className="list-disc ps-6 text-muted-foreground">
            {flags.map((f, i) => (
              <li key={i}>
                {f.item !== null && draft.items[f.item] ? <bdi>{draft.items[f.item].name}</bdi> : null}
                {f.item !== null ? ": " : ""}
                {t.flagCodes[f.code] ?? f.message}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="grid gap-2">
        <Label htmlFor={`name-${draft.id}`}>{t.mealName}</Label>
        <Input id={`name-${draft.id}`} dir="auto" value={name} onChange={(e) => setName(e.target.value)} />
      </div>

      <ul className="grid gap-2">
        {portions.map((p, index) => {
          const item = items[index];
          const label = p.item.units && p.factor === 1 ? p.item.units : item.grams !== undefined ? `${item.grams} ${t.gramsUnit}` : `×${p.factor}`;
          return (
            <li key={index} className="grid gap-2 rounded-xl bg-card p-3">
              <div className="flex items-start justify-between gap-3">
                <div className="grid">
                  <span className="font-medium" dir="auto">
                    {item.name}
                  </span>
                  <span className="text-xs text-muted-foreground">
                    {fill(t.macros, { p: item.protein_g ?? 0, c: item.carbs_g ?? 0, f: item.fat_g ?? 0 })}
                  </span>
                </div>
                <span className="shrink-0 font-heading text-lg tabular-nums text-gold">
                  {item.kcal_min === item.kcal_max
                    ? fill(t.kcalExact, { kcal: item.kcal_min ?? 0 })
                    : fill(t.kcalRange, { range: isolated(`${item.kcal_min ?? 0}–${item.kcal_max ?? 0}`) })}
                </span>
              </div>
              <div className="flex items-center gap-2" role="group" aria-label={fill(t.portionOf, { name: item.name })}>
                <Button
                  type="button"
                  variant="outline"
                  size="icon"
                  aria-label={fill(t.smaller, { name: item.name })}
                  disabled={p.factor <= PORTION_STEPS[0]}
                  onClick={() => setFactor(index, stepFrom(p.factor, -1))}
                >
                  <Minus aria-hidden />
                </Button>
                <output className="min-w-20 text-center font-heading text-lg tabular-nums" dir="ltr" aria-live="polite">
                  {label}
                </output>
                <Button
                  type="button"
                  variant="outline"
                  size="icon"
                  aria-label={fill(t.bigger, { name: item.name })}
                  disabled={p.factor >= PORTION_STEPS[PORTION_STEPS.length - 1]}
                  onClick={() => setFactor(index, stepFrom(p.factor, 1))}
                >
                  <Plus aria-hidden />
                </Button>
              </div>
            </li>
          );
        })}
      </ul>

      <div className="flex items-baseline justify-between gap-3 border-t border-border pt-3">
        <span className="font-semibold">{t.total}</span>
        <span className="grid text-end">
          <span className="font-heading text-2xl font-bold tabular-nums">
            {totals.kcal_min === totals.kcal_max
              ? fill(t.kcalExact, { kcal: totals.kcal })
              : fill(t.kcalRange, { range: isolated(`${totals.kcal_min}–${totals.kcal_max}`) })}
          </span>
          <span className="text-xs text-muted-foreground">
            {fill(t.macros, { p: totals.protein_g, c: totals.carbs_g, f: totals.fat_g })}
          </span>
        </span>
      </div>

      <label className="flex min-h-11 items-center gap-3 text-sm">
        <Checkbox checked={keep} onCheckedChange={(v) => setKeep(v === true)} aria-label={t.keepAsMine} />
        {t.keepAsMine}
      </label>
      <FormError message={save.isError || discard.isError ? apiErrorMessage(save.error ?? discard.error, dict) : null} />
      <div className="flex flex-wrap gap-3">
        <Button type="button" onClick={() => save.mutate()} disabled={save.isPending || discard.isPending}>
          {t.saveMeal}
        </Button>
        <Button
          type="button"
          variant="ghost"
          onClick={() => discard.mutate()}
          disabled={save.isPending || discard.isPending}
        >
          {t.discard}
        </Button>
      </div>
    </div>
  );
}
