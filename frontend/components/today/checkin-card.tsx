"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent, type KeyboardEvent } from "react";
import { toast } from "sonner";

import { FormError } from "@/components/field";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api } from "@/lib/api/client";
import { CHECKIN_SCORES, type Checkin, type CheckinIn, type CheckinScore } from "@/lib/api/types";
import { fill, useI18n } from "@/lib/i18n/provider";
import { parseNumber } from "@/lib/numbers";
import { cn } from "@/lib/utils";

const KEY = ["checkins", 7] as const;
const SCORES = [1, 2, 3, 4, 5];

/** 1 to 5 as a row of tappable squares (a radio group). */
function ScoreScale({
  name,
  label,
  low,
  high,
  value,
  onChange,
}: {
  name: string;
  label: string;
  low: string;
  high: string;
  value: number | null;
  onChange: (value: number) => void;
}) {
  const move = (event: KeyboardEvent<HTMLButtonElement>, score: number) => {
    const rtl = getComputedStyle(event.currentTarget).direction === "rtl";
    const step = { ArrowUp: 1, ArrowDown: -1, ArrowRight: rtl ? -1 : 1, ArrowLeft: rtl ? 1 : -1 }[event.key];
    if (!step) return;
    event.preventDefault();
    const next = Math.min(5, Math.max(1, score + step));
    onChange(next);
    (event.currentTarget.parentElement?.children[next - 1] as HTMLElement | undefined)?.focus();
  };
  return (
    <div className="grid gap-2">
      <p id={`${name}-label`} className="text-sm font-medium">
        {label}
      </p>
      <div role="radiogroup" aria-labelledby={`${name}-label`} className="grid grid-cols-5 gap-2">
        {SCORES.map((score) => {
          const checked = value === score;
          return (
            <button
              key={score}
              type="button"
              role="radio"
              aria-checked={checked}
              aria-label={score === 1 ? `1, ${low}` : score === 5 ? `5, ${high}` : String(score)}
              tabIndex={checked || (value === null && score === 1) ? 0 : -1}
              onClick={() => onChange(score)}
              onKeyDown={(e) => move(e, score)}
              className={cn(
                "h-12 rounded-xl border font-heading text-xl tabular-nums transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring",
                checked
                  ? "border-primary bg-primary text-primary-foreground"
                  : "border-border bg-background text-muted-foreground hover:text-foreground",
              )}
            >
              {score}
            </button>
          );
        })}
      </div>
      <div className="flex justify-between text-xs text-muted-foreground" aria-hidden>
        <span>{low}</span>
        <span>{high}</span>
      </div>
    </div>
  );
}

const EMPTY: CheckinIn = { sleep_h: null, energy: null, motivation: null, soreness: null, stress: null };

function fromCheckin(c: Checkin | undefined): CheckinIn {
  if (!c) return EMPTY;
  return { sleep_h: c.sleep_h, energy: c.energy, motivation: c.motivation, soreness: c.soreness, stress: c.stress };
}

/** The weekly check-in. One per day is kept; the latest of the past week is shown. */
export function CheckinCard() {
  const { dict, locale } = useI18n();
  const t = dict.today;
  const client = useQueryClient();
  const recent = useQuery({ queryKey: KEY, queryFn: () => api<Checkin[]>("/checkins?days=7") });
  const latest = recent.data
    ?.slice()
    .sort((a, b) => b.date.localeCompare(a.date))[0];
  // Edits start from the latest answers; null until the user changes something.
  const [draft, setDraft] = useState<CheckinIn | null>(null);
  const [sleep, setSleep] = useState<string | null>(null);
  const [empty, setEmpty] = useState(false);
  const values = draft ?? fromCheckin(latest);
  const sleepText = sleep ?? (values.sleep_h === null ? "" : String(values.sleep_h));

  const save = useMutation({
    mutationFn: (body: CheckinIn) => api<Checkin>("/checkins", { method: "POST", body }),
    onSuccess: () => {
      setDraft(null);
      setSleep(null);
      toast.success(t.checkinSaved);
      void client.invalidateQueries({ queryKey: KEY });
    },
  });

  const setScore = (key: CheckinScore, score: number) => {
    setEmpty(false);
    setDraft({ ...values, [key]: score });
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const hours = parseNumber(sleepText);
    const body: CheckinIn = { ...values, sleep_h: hours !== null && hours <= 24 ? hours : null };
    if (Object.values(body).every((v) => v === null)) {
      setEmpty(true);
      return;
    }
    save.mutate(body);
  };

  const doneOn = latest
    ? new Date(`${latest.date}T00:00:00`).toLocaleDateString(locale, { weekday: "long", day: "numeric", month: "short" })
    : null;

  return (
    <section className="grid gap-4 rounded-2xl border border-border bg-card p-5" aria-labelledby="checkin-title">
      <div className="grid gap-1">
        <h2 id="checkin-title" className="text-xl font-semibold">
          {t.checkinTitle}
        </h2>
        <p className="text-sm text-muted-foreground">{doneOn ? fill(t.checkinDone, { date: doneOn }) : t.checkinLead}</p>
      </div>
      <form onSubmit={submit} className="grid gap-5" noValidate>
        <div className="grid gap-2">
          <Label htmlFor="sleep-hours">{t.sleep}</Label>
          <Input
            id="sleep-hours"
            inputMode="decimal"
            autoComplete="off"
            dir="ltr"
            className="w-32"
            value={sleepText}
            onChange={(e) => {
              setEmpty(false);
              setSleep(e.target.value);
            }}
          />
        </div>
        {CHECKIN_SCORES.map((key) => (
          <ScoreScale
            key={key}
            name={`checkin-${key}`}
            label={t.scores[key]}
            low={t.scoreLow}
            high={t.scoreHigh}
            value={values[key]}
            onChange={(score) => setScore(key, score)}
          />
        ))}
        {empty && (
          <p role="alert" className="text-sm text-destructive">
            {t.checkinEmpty}
          </p>
        )}
        <FormError message={save.isError || recent.isError ? dict.common.errorGeneric : null} />
        <Button type="submit" className="w-fit" disabled={save.isPending}>
          {t.checkinSave}
        </Button>
      </form>
    </section>
  );
}
