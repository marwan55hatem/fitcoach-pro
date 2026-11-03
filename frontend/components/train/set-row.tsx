"use client";

import { Check, Trophy } from "lucide-react";
import { useState } from "react";

import type { SetLog, SetPatch } from "@/lib/api/types";
import { fill, useI18n } from "@/lib/i18n/provider";
import { formatKg, parseNumber } from "@/lib/numbers";
import { cn } from "@/lib/utils";

const input =
  "h-12 w-full min-w-0 rounded-xl border border-input bg-background px-3 text-center font-heading text-2xl tabular-nums outline-none placeholder:text-muted-foreground/60 focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/40 aria-invalid:border-destructive";

/**
 * One set: weight and reps, then a big tick. Values are kept while typing and sent when
 * a field loses focus or the set is ticked; the parent queues the change.
 */
export function SetRow({
  set,
  number: n,
  isBest,
  onChange,
  onDone,
}: {
  set: SetLog;
  number: number; // its place in the card, which a swap keeps continuous
  isBest: boolean;
  onChange: (patch: SetPatch) => void;
  onDone: () => void;
}) {
  const { dict } = useI18n();
  const t = dict.train;
  const [weight, setWeight] = useState(set.weight_kg === null ? "" : formatKg(set.weight_kg));
  const [reps, setReps] = useState(set.reps === null ? "" : String(set.reps));
  const [needReps, setNeedReps] = useState(false);

  const commitWeight = () => {
    const value = parseNumber(weight);
    if (value !== set.weight_kg && (value === null || value <= 1000)) onChange({ weight_kg: value });
  };
  const commitReps = () => {
    const value = parseNumber(reps);
    if (value !== set.reps && (value === null || (Number.isInteger(value) && value <= 100))) {
      // A done set keeps its reps; clearing them is refused by the API.
      if (value !== null || !set.done) onChange({ reps: value });
    }
  };

  const toggle = () => {
    if (set.done) {
      onChange({ done: false });
      return;
    }
    const r = parseNumber(reps);
    if (r === null || !Number.isInteger(r) || r > 100) {
      setNeedReps(true);
      return;
    }
    setNeedReps(false);
    const w = parseNumber(weight) ?? set.target_weight_kg;
    if (w !== null && weight === "") setWeight(formatKg(w));
    onChange({ weight_kg: w, reps: r, done: true });
    onDone();
  };

  const repsHint = set.target_reps_min
    ? set.target_reps_max && set.target_reps_max !== set.target_reps_min
      ? `${set.target_reps_min}–${set.target_reps_max}`
      : String(set.target_reps_min)
    : "";

  return (
    <li className="grid gap-1">
      <div
        className={cn(
          "grid grid-cols-[2.75rem_1fr_1fr_3rem] items-center gap-2 rounded-xl p-1 transition-colors",
          set.done && "bg-success/10",
        )}
      >
        <span className="relative grid place-items-center font-heading text-xl font-semibold tabular-nums text-muted-foreground" dir="ltr">
          {n}
          {isBest && (
            <Trophy className="absolute -top-2 -end-1 size-4 text-gold" role="img" aria-label={t.newBest} />
          )}
        </span>
        <input
          className={input}
          inputMode="decimal"
          autoComplete="off"
          dir="ltr"
          aria-label={fill(t.weightOf, { number: n })}
          placeholder={set.target_weight_kg === null ? "–" : formatKg(set.target_weight_kg)}
          value={weight}
          onChange={(e) => setWeight(e.target.value)}
          onBlur={commitWeight}
        />
        <input
          className={input}
          inputMode="numeric"
          autoComplete="off"
          dir="ltr"
          aria-label={fill(t.repsOf, { number: n })}
          aria-invalid={needReps || undefined}
          placeholder={repsHint}
          value={reps}
          onChange={(e) => {
            setReps(e.target.value);
            setNeedReps(false);
          }}
          onBlur={commitReps}
        />
        <button
          type="button"
          onClick={toggle}
          aria-pressed={set.done}
          aria-label={fill(set.done ? t.markUndone : t.markDone, { number: n })}
          className={cn(
            "grid size-12 place-items-center rounded-xl border-2 transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring",
            set.done
              ? "border-success bg-success text-background"
              : "border-input text-muted-foreground hover:border-success hover:text-success",
          )}
        >
          <Check className="size-6" strokeWidth={3} aria-hidden />
        </button>
      </div>
      {needReps && (
        <p role="alert" className="ps-14 text-sm text-destructive">
          {t.needReps}
        </p>
      )}
    </li>
  );
}
