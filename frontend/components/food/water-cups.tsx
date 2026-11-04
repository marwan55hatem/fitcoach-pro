"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { GlassWater } from "lucide-react";

import { FormError } from "@/components/field";
import { api } from "@/lib/api/client";
import type { FoodDay } from "@/lib/api/types";
import { fill, useI18n } from "@/lib/i18n/provider";
import { cn } from "@/lib/utils";

export const CUP_ML = 250;

/** One tap per cup: tapping a cup fills up to it, tapping the last full cup empties it. */
export function WaterCups({ day, queryKey }: { day: FoodDay; queryKey: readonly unknown[] }) {
  const { dict } = useI18n();
  const t = dict.food;
  const client = useQueryClient();
  const cups = Math.round(day.water_ml / CUP_ML);
  const target = day.targets ? Math.max(1, Math.round(day.targets.water_ml / CUP_ML)) : 8;
  const shown = Math.max(target, cups + 1);
  const save = useMutation({
    mutationFn: (count: number) =>
      api(`/nutrition/days/${day.date}/water`, { method: "PUT", body: { ml: count * CUP_ML } }),
    onMutate: (count) => {
      client.setQueryData<FoodDay>(queryKey, (d) => (d ? { ...d, water_ml: count * CUP_ML } : d));
    },
    onSettled: () => void client.invalidateQueries({ queryKey }),
  });

  return (
    <section className="grid gap-3 rounded-2xl border border-border bg-card p-4" aria-labelledby="water-title">
      <div className="flex items-baseline justify-between gap-3">
        <h2 id="water-title" className="text-lg font-semibold">
          {t.water}
        </h2>
        <p className="text-sm tabular-nums text-muted-foreground">
          {day.targets
            ? fill(t.waterCups, { cups, target, ml: day.water_ml })
            : fill(t.waterCupsNoTarget, { cups, ml: day.water_ml })}
        </p>
      </div>
      <ol className="flex flex-wrap gap-1.5">
        {Array.from({ length: shown }, (_, i) => {
          const full = i < cups;
          return (
            <li key={i}>
              <button
                type="button"
                aria-pressed={full}
                aria-label={fill(t.cupLabel, { number: i + 1 })}
                onClick={() => save.mutate(full && i === cups - 1 ? i : i + 1)}
                className={cn(
                  "grid size-11 place-items-center rounded-xl border transition-colors focus-visible:outline-2 focus-visible:outline-ring",
                  full ? "border-chart-4 bg-chart-4/20 text-chart-4" : "border-border text-muted-foreground",
                )}
              >
                <GlassWater className="size-5" aria-hidden />
              </button>
            </li>
          );
        })}
      </ol>
      <FormError message={save.isError ? dict.common.errorGeneric : null} />
    </section>
  );
}
