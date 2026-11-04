"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeftRight, ListOrdered } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { FormError } from "@/components/field";
import { SetRow } from "@/components/train/set-row";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api/client";
import type {
  ExerciseDetail,
  ExerciseLast,
  SetPatch,
  Substitutes,
  WorkoutSession,
} from "@/lib/api/types";
import { fill, useI18n } from "@/lib/i18n/provider";
import { formatKg } from "@/lib/numbers";
import { beatsBest, type Slot } from "@/lib/workout/plan";
import { keys } from "@/lib/workout/queries";
import { flushSetQueue } from "@/lib/workout/set-queue";

const SIGNED_URL_FRESH_MS = 4 * 60_000; // image links expire after five minutes

/** Demo photos (start and end position). Tapping shows the next one. */
function Photos({ detail }: { detail: ExerciseDetail | undefined }) {
  const { dict } = useI18n();
  const [index, setIndex] = useState(0);
  const urls = detail?.image_urls ?? [];
  if (!detail) return <div className="aspect-[4/3] w-full animate-pulse rounded-xl bg-muted" />;
  if (urls.length === 0) {
    return <p className="text-sm text-muted-foreground">{dict.train.noPhoto}</p>;
  }
  const current = index % urls.length;
  return (
    <button
      type="button"
      onClick={() => setIndex(current + 1)}
      aria-label={fill(dict.train.photoOf, { current: current + 1, total: urls.length })}
      className="relative overflow-hidden rounded-xl bg-white focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
    >
      {/* Short-lived signed links to private storage: next/image would fetch and cache them on the server. */}
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src={urls[current]} alt="" className="aspect-[4/3] w-full object-contain" />
      {urls.length > 1 && (
        <span className="absolute inset-x-0 bottom-2 flex justify-center gap-1.5" aria-hidden>
          {urls.map((url, i) => (
            <span key={url} className={i === current ? "size-2 rounded-full bg-primary" : "size-2 rounded-full bg-black/30"} />
          ))}
        </span>
      )}
    </button>
  );
}

function LastTime({ last }: { last: ExerciseLast | undefined }) {
  const { dict, locale } = useI18n();
  const t = dict.train;
  if (!last) return null;
  if (!last.last_date) return <p className="text-sm text-muted-foreground">{t.firstTime}</p>;
  const date = new Date(`${last.last_date}T00:00:00`).toLocaleDateString(locale, {
    day: "numeric",
    month: "short",
  });
  const sets = last.last_sets
    .map((s) => (s.weight_kg === null ? `${s.reps}` : `${formatKg(s.weight_kg)}×${s.reps}`))
    .join(", ");
  const best = last.personal_best?.best_e1rm;
  return (
    <div className="grid gap-0.5 text-sm">
      <p className="text-muted-foreground">
        {fill(t.lastTime, { date, sets: "" })}
        <bdi dir="ltr" className="font-heading text-base tabular-nums text-foreground">
          {sets}
        </bdi>
      </p>
      {best && (
        <p className="text-gold">{fill(t.best, { weight: formatKg(best.weight_kg), reps: best.reps })}</p>
      )}
    </div>
  );
}

export function ExerciseCard({
  session,
  slot,
  plannedExerciseId,
  plannedName,
  online,
  onSetChange,
  onSetDone,
}: {
  session: WorkoutSession;
  slot: Slot;
  plannedExerciseId: string | null;
  plannedName: string | null;
  online: boolean;
  onSetChange: (setId: string, patch: SetPatch) => void;
  onSetDone: () => void;
}) {
  const { dict } = useI18n();
  const t = dict.train;
  const client = useQueryClient();
  const [howTo, setHowTo] = useState(false);
  const [swapping, setSwapping] = useState(false);

  const detail = useQuery({
    queryKey: ["exercise", slot.exerciseId],
    queryFn: () => api<ExerciseDetail>(`/exercises/${slot.exerciseId}`),
    staleTime: SIGNED_URL_FRESH_MS,
    refetchInterval: SIGNED_URL_FRESH_MS,
  });
  // "Last time" as it was when this workout began, before today's sets count.
  const last = useQuery({
    queryKey: ["exercise-last", slot.exerciseId, session.id],
    queryFn: () => api<ExerciseLast>(`/exercises/${slot.exerciseId}/last`),
    staleTime: Infinity,
  });
  const anchor = plannedExerciseId ?? slot.exerciseId;
  const subs = useQuery({
    queryKey: ["substitutes", anchor],
    queryFn: () => api<Substitutes>(`/exercises/${anchor}/substitutes`),
    enabled: swapping,
  });
  const swap = useMutation({
    mutationFn: async (exerciseId: string) => {
      await flushSetQueue();
      return api<WorkoutSession>(`/sessions/${session.id}/swap`, {
        method: "POST",
        body: { program_exercise_id: slot.programExerciseId, exercise_id: exerciseId },
      });
    },
    onSuccess: (updated, exerciseId) => {
      client.setQueryData(keys.open, updated);
      setSwapping(false);
      setHowTo(false);
      const name = updated.sets.find((s) => s.exercise_id === exerciseId)?.exercise_name ?? "";
      toast.success(fill(t.swapped, { name }));
    },
  });

  const first = slot.sets.find((s) => !s.done) ?? slot.sets[0];
  const reps =
    first.target_reps_min && first.target_reps_max && first.target_reps_max !== first.target_reps_min
      ? `${first.target_reps_min}–${first.target_reps_max}`
      : String(first.target_reps_min ?? "");
  const canSwap = slot.programExerciseId !== null && slot.sets.some((s) => !s.done);
  const muscles = detail.data ? [detail.data.muscle_primary, ...detail.data.muscles_secondary] : [];
  const options = (subs.data?.substitutes ?? []).filter((s) => s.id !== slot.exerciseId);
  const swapped = plannedExerciseId !== null && plannedExerciseId !== slot.exerciseId;

  return (
    <article className="grid gap-4 rounded-2xl border border-border bg-card p-4" aria-labelledby={`ex-${slot.key}`}>
      <header className="grid gap-1">
        <div className="flex items-start justify-between gap-3">
          <h2 id={`ex-${slot.key}`} className="text-2xl font-semibold leading-tight" dir="auto">
            {slot.exerciseName}
          </h2>
          {canSwap && (
            <Button
              variant="outline"
              className="shrink-0"
              aria-expanded={swapping}
              onClick={() => setSwapping(!swapping)}
            >
              <ArrowLeftRight className="size-4" aria-hidden />
              {t.swap}
            </Button>
          )}
        </div>
        {reps && (
          <p className="font-heading text-lg text-gold" dir="auto">
            {first.target_weight_kg === null
              ? fill(t.target, { reps })
              : fill(t.targetWeight, { reps, weight: formatKg(first.target_weight_kg) })}
          </p>
        )}
        <LastTime last={last.data} />
      </header>

      {swapping && (
        <div
          role="group"
          aria-labelledby={`swap-${slot.key}`}
          className="grid gap-3 rounded-xl border border-border bg-background p-3"
        >
          <p id={`swap-${slot.key}`} className="font-semibold">
            {t.swapTitle}
          </p>
          <p className="text-sm text-muted-foreground">{t.swapLead}</p>
          {!online && <FormError message={t.swapOffline} />}
          {subs.isPending && <p className="text-sm text-muted-foreground">{dict.common.loading}</p>}
          {subs.isError && <FormError message={dict.common.errorGeneric} />}
          {subs.isSuccess && options.length === 0 && !swapped && (
            <p className="text-sm text-muted-foreground">{t.swapNone}</p>
          )}
          <ul className="grid gap-2">
            {swapped && plannedName && (
              <li>
                <Button
                  variant="outline"
                  className="w-full justify-start"
                  disabled={!online || swap.isPending}
                  onClick={() => plannedExerciseId && swap.mutate(plannedExerciseId)}
                >
                  {fill(t.swapBack, { name: plannedName })}
                </Button>
              </li>
            )}
            {options.map((option) => (
              <li key={option.id}>
                <Button
                  variant="outline"
                  className="h-auto min-h-12 w-full justify-between whitespace-normal py-2 text-start"
                  disabled={!online || swap.isPending}
                  onClick={() => swap.mutate(option.id)}
                >
                  <span dir="auto">{option.name}</span>
                  <span className="shrink-0 text-xs text-muted-foreground">
                    {option.equipment ? (dict.options.equipment as Record<string, string>)[option.equipment] ?? option.equipment : ""}
                  </span>
                </Button>
              </li>
            ))}
          </ul>
          <FormError message={swap.isError ? t.swapError : null} />
        </div>
      )}

      <div className="grid gap-3">
        <button
          type="button"
          aria-expanded={howTo}
          onClick={() => setHowTo(!howTo)}
          className="flex min-h-11 w-fit items-center gap-2 rounded-lg text-sm font-medium text-primary focus-visible:outline-2 focus-visible:outline-ring"
        >
          <ListOrdered className="size-4" aria-hidden />
          {howTo ? t.hideHowTo : t.howTo}
        </button>
        {howTo && (
          <div className="grid gap-3">
            <Photos detail={detail.data} />
            {muscles.length > 0 && (
              <div className="grid gap-1.5">
                <p className="text-sm font-medium">{t.muscles}</p>
                <ul className="flex flex-wrap gap-1.5">
                  {muscles.map((m, i) => (
                    <li
                      key={m}
                      className={
                        i === 0
                          ? "rounded-full bg-primary/15 px-3 py-1 text-sm text-primary"
                          : "rounded-full bg-muted px-3 py-1 text-sm text-muted-foreground"
                      }
                    >
                      {dict.muscles[m] ?? m}
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {detail.data && detail.data.instructions.length > 0 && (
              <ol className="grid list-decimal gap-2 ps-5 text-sm leading-relaxed text-muted-foreground" dir="auto">
                {detail.data.instructions.map((step) => (
                  <li key={step}>{step}</li>
                ))}
              </ol>
            )}
          </div>
        )}
      </div>

      <div className="grid gap-1">
        <div className="grid grid-cols-[2.75rem_1fr_1fr_3rem] gap-2 px-1 text-center text-xs text-muted-foreground" aria-hidden>
          <span>#</span>
          <span>{t.weight}</span>
          <span>{t.reps}</span>
          <span />
        </div>
        <ol className="grid gap-1.5">
          {slot.sets.map((set, index) => (
            <SetRow
              key={`${set.id}:${set.exercise_id}`}
              set={set}
              number={index + 1}
              isBest={beatsBest(set, set.exercise_id === slot.exerciseId ? last.data : undefined)}
              onChange={(patch) => onSetChange(set.id, patch)}
              onDone={onSetDone}
            />
          ))}
        </ol>
      </div>
    </article>
  );
}
