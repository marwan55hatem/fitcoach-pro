import type { FoodItem, FoodLogIn, Meal } from "@/lib/api/types";

/**
 * Display helpers for an estimate being checked. The API's food rules validate what is
 * saved; these only keep the numbers on screen in step while a portion is changed.
 */

export type Portion = { item: FoodItem; factor: number };

const round1 = (n: number) => Math.round(n * 10) / 10;

/** An item at `factor` times the estimated portion. */
export function scaled({ item, factor }: Portion): FoodItem {
  const scale = (n: number | undefined) => (n === undefined ? undefined : round1(n * factor));
  return {
    ...item,
    grams: item.grams === undefined ? undefined : Math.round(item.grams * factor),
    kcal_min: item.kcal_min === undefined ? undefined : Math.round(item.kcal_min * factor),
    kcal_max: item.kcal_max === undefined ? undefined : Math.round(item.kcal_max * factor),
    protein_g: scale(item.protein_g),
    carbs_g: scale(item.carbs_g),
    fat_g: scale(item.fat_g),
  };
}

/** Totals of the items, with the midpoint of the calorie range (as the API counts it). */
export function totalsOf(items: FoodItem[]) {
  const sum = (pick: (i: FoodItem) => number | undefined) =>
    items.reduce((acc, i) => acc + (pick(i) ?? 0), 0);
  const low = sum((i) => i.kcal_min);
  const high = sum((i) => i.kcal_max);
  return {
    kcal_min: low,
    kcal_max: high,
    kcal: Math.floor((low + high + 1) / 2),
    protein_g: round1(sum((i) => i.protein_g)),
    carbs_g: round1(sum((i) => i.carbs_g)),
    fat_g: round1(sum((i) => i.fat_g)),
  };
}

/** The checked meal, ready for PUT /food/logs/{id}. */
export function checkedMeal(meal: Meal, name: string, portions: Portion[]): FoodLogIn {
  const items = portions.map(scaled);
  const totals = totalsOf(items);
  const ranged = totals.kcal_min !== totals.kcal_max;
  return {
    date: meal.date,
    slot: meal.slot,
    name,
    items,
    kcal: totals.kcal,
    kcal_min: ranged ? totals.kcal_min : null,
    kcal_max: ranged ? totals.kcal_max : null,
    protein_g: totals.protein_g,
    carbs_g: totals.carbs_g,
    fat_g: totals.fat_g,
  };
}

export type ConfidenceLevel = "high" | "medium" | "low";

export function confidenceLevel(confidence: number | null): ConfidenceLevel {
  if (confidence === null || confidence < 0.45) return "low";
  return confidence < 0.75 ? "medium" : "high";
}

/** "775" for an exact meal, "700–850" for an estimate. */
export function kcalLabel(meal: Pick<Meal, "kcal" | "kcal_min" | "kcal_max">): string {
  if (meal.kcal_min !== null && meal.kcal_max !== null && meal.kcal_min !== meal.kcal_max) {
    return `${meal.kcal_min}–${meal.kcal_max}`;
  }
  return String(meal.kcal ?? 0);
}

export const PORTION_STEPS = [0.5, 0.75, 1, 1.25, 1.5, 2, 3];
