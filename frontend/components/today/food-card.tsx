"use client";

import { useQuery } from "@tanstack/react-query";

import { FormError } from "@/components/field";
import { api, ApiError } from "@/lib/api/client";
import type { NutritionDay } from "@/lib/api/types";
import { fill, useI18n } from "@/lib/i18n/provider";

function Bar({ value, target, tone }: { value: number; target: number; tone: string }) {
  const share = target > 0 ? Math.min(1, value / target) : 0;
  return (
    <div className="h-2 overflow-hidden rounded-full bg-muted" aria-hidden>
      <div className={`h-full rounded-full ${tone}`} style={{ inlineSize: `${share * 100}%` }} />
    </div>
  );
}

/** Water and macros so far today against the targets (only confirmed meals count). */
export function FoodCard() {
  const { dict } = useI18n();
  const t = dict.today;
  const day = useQuery({
    queryKey: ["nutrition-day", "today"],
    queryFn: async () => {
      try {
        return await api<NutritionDay>("/nutrition/day");
      } catch (error) {
        // No plan access while the health check blocks coaching: nothing to show.
        if (error instanceof ApiError && error.status === 403) return null;
        throw error;
      }
    },
  });

  if (day.isPending) return <div className="h-40 animate-pulse rounded-2xl bg-card" />;
  if (day.isError) return <FormError message={dict.common.errorGeneric} />;
  if (!day.data) return null;
  const { totals, targets, water_ml: water } = day.data;
  const macros = targets
    ? [
        { label: t.kcal, value: totals.kcal, target: targets.kcal, unit: "", tone: "bg-primary" },
        { label: t.protein, value: totals.protein_g, target: targets.protein_g, unit: t.grams, tone: "bg-gold" },
        { label: t.carbs, value: totals.carbs_g, target: targets.carbs_g, unit: t.grams, tone: "bg-chart-4" },
        { label: t.fat, value: totals.fat_g, target: targets.fat_g, unit: t.grams, tone: "bg-success" },
      ]
    : [];

  return (
    <section className="grid gap-4 rounded-2xl border border-border bg-card p-5" aria-labelledby="food-title">
      <h2 id="food-title" className="text-xl font-semibold">
        {t.foodTitle}
      </h2>
      {totals.meals_counted === 0 && <p className="text-sm text-muted-foreground">{t.foodEmpty}</p>}
      <dl className="grid gap-3">
        <div className="grid gap-1.5">
          <div className="flex justify-between gap-3 text-sm">
            <dt>{t.water}</dt>
            <dd className="tabular-nums text-muted-foreground">
              {targets
                ? fill(t.waterLine, { ml: water, target: targets.water_ml })
                : fill(t.waterOnly, { ml: water })}
            </dd>
          </div>
          {targets && <Bar value={water} target={targets.water_ml} tone="bg-chart-4" />}
        </div>
        {macros.map((m) => (
          <div key={m.label} className="grid gap-1.5">
            <div className="flex justify-between gap-3 text-sm">
              <dt>{m.label}</dt>
              <dd className="tabular-nums text-muted-foreground">
                {fill(t.macroLine, { value: `${Math.round(m.value)}${m.unit}`, target: `${m.target}${m.unit}` })}
              </dd>
            </div>
            <Bar value={m.value} target={m.target} tone={m.tone} />
          </div>
        ))}
      </dl>
      {day.data.targets_withheld && <p className="text-sm text-muted-foreground">{t.withheld}</p>}
    </section>
  );
}
