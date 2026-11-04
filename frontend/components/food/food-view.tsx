"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight, Plus, Trash2, X } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { AddMeal } from "@/components/food/add-meal";
import { DraftEditor } from "@/components/food/draft-editor";
import { Rings } from "@/components/food/rings";
import { WaterCups } from "@/components/food/water-cups";
import { FormError } from "@/components/field";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api/client";
import { type FoodDay, type Meal, MEAL_SLOTS, type MealSlot } from "@/lib/api/types";
import { kcalLabel } from "@/lib/food/estimate";
import { fill, useI18n } from "@/lib/i18n/provider";

function shift(date: string, days: number): string {
  const d = new Date(`${date}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

/** The day's food: rings against targets, water, and the six meal slots. */
export function FoodView() {
  const { dict, locale } = useI18n();
  const t = dict.food;
  const client = useQueryClient();
  const [date, setDate] = useState<string | null>(null); // null: today in the user's timezone
  const [adding, setAdding] = useState<MealSlot | null>(null);

  const key = ["food-day", date ?? "today"] as const;
  const day = useQuery({
    queryKey: key,
    queryFn: () => api<FoodDay>(date ? `/nutrition/day?date=${date}` : "/nutrition/day"),
  });
  const today = useQuery({
    queryKey: ["food-day", "today"],
    queryFn: () => api<FoodDay>("/nutrition/day"),
    enabled: date !== null,
  });
  const todayDate = date === null ? day.data?.date : today.data?.date;

  const refresh = () => {
    void client.invalidateQueries({ queryKey: ["food-day"] });
    void client.invalidateQueries({ queryKey: ["nutrition-day"] }); // the Today card
  };
  const remove = useMutation({
    mutationFn: (meal: Meal) => api(`/nutrition/meals/${meal.id}`, { method: "DELETE" }),
    onSuccess: () => {
      toast.success(t.deleted);
      refresh();
    },
  });

  if (day.isPending) return <p className="text-muted-foreground">{dict.common.loading}</p>;
  if (day.isError) {
    return (
      <section className="grid gap-3">
        <h1 className="text-4xl font-bold">{t.title}</h1>
        <FormError message={dict.common.errorGeneric} />
        <Button variant="outline" className="w-fit" onClick={() => void day.refetch()}>
          {dict.common.retry}
        </Button>
      </section>
    );
  }

  const data = day.data;
  const isToday = data.date === todayDate;
  const label = isToday
    ? t.today
    : new Date(`${data.date}T12:00:00Z`).toLocaleDateString(locale, { weekday: "long", day: "numeric", month: "long" });
  const ranged = data.totals.kcal_min !== data.totals.kcal_max;

  return (
    <section className="grid gap-5">
      <div className="flex items-center justify-between gap-3">
        <h1 className="text-4xl font-bold">{t.title}</h1>
        <div className="flex items-center gap-1">
          <Button variant="ghost" size="icon" aria-label={t.previousDay} onClick={() => setDate(shift(data.date, -1))}>
            <ChevronLeft className="rtl:rotate-180" aria-hidden />
          </Button>
          <span className="min-w-20 text-center text-sm font-medium">{label}</span>
          <Button
            variant="ghost"
            size="icon"
            aria-label={t.nextDay}
            disabled={isToday}
            onClick={() => {
              const next = shift(data.date, 1);
              setDate(next === todayDate ? null : next);
            }}
          >
            <ChevronRight className="rtl:rotate-180" aria-hidden />
          </Button>
        </div>
      </div>

      {data.targets ? (
        <div className="grid gap-2 rounded-2xl border border-border bg-card p-4">
          <Rings
            kcal={data.totals.kcal}
            kcalTarget={data.targets.kcal}
            protein={data.totals.protein_g}
            proteinTarget={data.targets.protein_g}
          />
          {ranged && (
            <p className="text-center text-xs text-muted-foreground">
              {fill(t.dayRange, { min: data.totals.kcal_min, max: data.totals.kcal_max })}
            </p>
          )}
        </div>
      ) : (
        <p className="rounded-2xl border border-border bg-card p-4 text-sm text-muted-foreground">{t.withheld}</p>
      )}

      <WaterCups day={data} queryKey={key} />

      {MEAL_SLOTS.map((slot) => {
        const meals = data.meals.filter((m) => m.slot === slot);
        return (
          <section key={slot} className="grid gap-3 rounded-2xl border border-border bg-card p-4" aria-labelledby={`slot-${slot}`}>
            <div className="flex items-center justify-between gap-3">
              <h2 id={`slot-${slot}`} className="text-lg font-semibold">
                {t.slots[slot]}
              </h2>
              <Button
                variant={adding === slot ? "ghost" : "outline"}
                aria-expanded={adding === slot}
                aria-label={adding === slot ? t.closeAdd : fill(t.addTo, { slot: t.slots[slot] })}
                onClick={() => setAdding(adding === slot ? null : slot)}
              >
                {adding === slot ? <X aria-hidden /> : <Plus aria-hidden />}
                {adding === slot ? t.closeAdd : t.add}
              </Button>
            </div>

            {meals.length === 0 && adding !== slot && <p className="text-sm text-muted-foreground">{t.empty}</p>}
            <ul className="grid gap-2">
              {meals.map((meal) => (
                <li key={meal.id}>
                  {!meal.confirmed ? (
                    <DraftEditor
                      draft={meal}
                      onSaved={() => {
                        toast.success(t.saved);
                        refresh();
                      }}
                      onDiscarded={refresh}
                    />
                  ) : (
                    <div className="flex items-center gap-3 rounded-xl bg-background p-3">
                      <div className="grid flex-1">
                        <span className="font-medium" dir="auto">
                          {meal.name}
                        </span>
                        <span className="text-xs text-muted-foreground">
                          {fill(t.macros, { p: meal.protein_g ?? 0, c: meal.carbs_g ?? 0, f: meal.fat_g ?? 0 })}
                        </span>
                      </div>
                      <span className="shrink-0 font-heading text-lg tabular-nums text-gold" dir="ltr">
                        {kcalLabel(meal)}
                      </span>
                      <Button
                        variant="ghost"
                        size="icon"
                        aria-label={fill(t.deleteMeal, { name: meal.name })}
                        disabled={remove.isPending}
                        onClick={() => remove.mutate(meal)}
                      >
                        <Trash2 aria-hidden />
                      </Button>
                    </div>
                  )}
                </li>
              ))}
            </ul>

            {adding === slot && (
              <AddMeal
                date={data.date}
                slot={slot}
                onDraft={(draft) => {
                  setAdding(null);
                  client.setQueryData<FoodDay>(key, (d) => (d ? { ...d, meals: [...d.meals, draft] } : d));
                  refresh();
                }}
                onLogged={() => {
                  toast.success(t.logged);
                  setAdding(null);
                  refresh();
                }}
              />
            )}
          </section>
        );
      })}
    </section>
  );
}
