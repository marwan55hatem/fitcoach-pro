"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CloudOff, Trophy } from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useState, useSyncExternalStore } from "react";
import { toast } from "sonner";

import { FormError } from "@/components/field";
import { ExerciseCard } from "@/components/train/exercise-card";
import { RestTimer } from "@/components/train/rest-timer";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api/client";
import type { FinishedSession, ProgramExercise, SetLog, SetPatch, WorkoutSession } from "@/lib/api/types";
import { fill, useI18n } from "@/lib/i18n/provider";
import { useLocalePath } from "@/lib/i18n/use-locale-path";
import { slotsOf } from "@/lib/workout/plan";
import { keys, useOpenSession, useProgram } from "@/lib/workout/queries";
import { DEFAULT_REST_S, useRestTimer } from "@/lib/workout/rest-timer";
import {
  enqueueSetPatch,
  flushSetQueue,
  onSetQueueEvent,
  serverSetQueueSnapshot,
  setQueueSnapshot,
  subscribeSetQueue,
  withPending,
} from "@/lib/workout/set-queue";

function subscribeOnline(callback: () => void): () => void {
  window.addEventListener("online", callback);
  window.addEventListener("offline", callback);
  return () => {
    window.removeEventListener("online", callback);
    window.removeEventListener("offline", callback);
  };
}

function useOnline(): boolean {
  return useSyncExternalStore(subscribeOnline, () => navigator.onLine, () => true);
}

function Summary({ session }: { session: FinishedSession }) {
  const { dict } = useI18n();
  const t = dict.train;
  const to = useLocalePath();
  const minutes =
    session.started_at && session.ended_at
      ? Math.max(1, Math.round((Date.parse(session.ended_at) - Date.parse(session.started_at)) / 60_000))
      : null;
  const names = new Map(session.sets.map((s) => [s.exercise_id, s.exercise_name]));
  const stats = [
    { label: t.summaryDuration, value: minutes === null ? "–" : fill(t.minutesShort, { minutes }) },
    { label: t.summarySets, value: `${session.sets_done}/${session.sets_planned}` },
    { label: t.summaryVolume, value: fill(t.kgShort, { kg: Math.round(session.volume_kg) }) },
  ];
  return (
    <section className="grid gap-6">
      <h1 className="text-4xl font-bold">{t.summaryTitle}</h1>
      {session.day_name && (
        <p className="-mt-4 text-muted-foreground" dir="auto">
          {session.day_name}
        </p>
      )}
      <dl className="grid grid-cols-3 gap-3">
        {stats.map((stat) => (
          <div key={stat.label} className="grid gap-1 rounded-2xl border border-border bg-card p-4">
            <dt className="text-xs text-muted-foreground">{stat.label}</dt>
            <dd className="font-heading text-3xl font-bold tabular-nums" dir="ltr">
              {stat.value}
            </dd>
          </div>
        ))}
      </dl>
      <div className="grid gap-3 rounded-2xl border border-gold/40 bg-gold/5 p-4">
        <h2 className="flex items-center gap-2 text-xl font-semibold">
          <Trophy className="size-5 text-gold" aria-hidden />
          {t.records}
        </h2>
        {session.new_records.length === 0 ? (
          <p className="text-muted-foreground">{t.noRecords}</p>
        ) : (
          <ul className="grid gap-1" aria-label={t.records}>
            {session.new_records.map((id) => (
              <li key={id} dir="auto">
                {names.get(id) ?? ""}
              </li>
            ))}
          </ul>
        )}
      </div>
      <Button asChild className="w-fit">
        <Link href={to("/today")}>{t.done}</Link>
      </Button>
    </section>
  );
}

/** The workout in progress: one card per exercise, sets saved even with no signal. */
export function WorkoutView() {
  const { dict } = useI18n();
  const t = dict.train;
  const to = useLocalePath();
  const client = useQueryClient();
  const open = useOpenSession();
  const program = useProgram();
  const queue = useSyncExternalStore(subscribeSetQueue, setQueueSnapshot, serverSetQueueSnapshot);
  const queued = queue.entries;
  const online = useOnline();
  const timer = useRestTimer();
  const [confirming, setConfirming] = useState(false);
  const [finished, setFinished] = useState<FinishedSession | null>(null);

  // Saved sets go straight into the cached session, so nothing flickers back.
  useEffect(
    () =>
      onSetQueueEvent((event) => {
        if (event.type === "saved") {
          client.setQueryData<WorkoutSession | null>(keys.open, (s) =>
            s ? { ...s, sets: s.sets.map((x): SetLog => (x.id === event.set.id ? event.set : x)) } : s,
          );
        } else {
          toast.error(t.saveRejected);
          void client.invalidateQueries({ queryKey: keys.open });
        }
      }),
    [client, t.saveRejected],
  );

  const unsaved = queued.length;
  useEffect(() => {
    if (unsaved === 0) return;
    const warn = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [unsaved]);

  const planned = useMemo(() => {
    const map = new Map<string, ProgramExercise>();
    for (const day of program.data?.days ?? []) for (const ex of day.exercises) map.set(ex.id, ex);
    return map;
  }, [program.data]);

  const finish = useMutation({
    mutationFn: async (sessionId: string) => {
      if (!(await flushSetQueue())) throw new Error("unsaved");
      return api<FinishedSession>(`/sessions/${sessionId}/finish`, { method: "POST" });
    },
    onSuccess: (done) => {
      timer.stop();
      setConfirming(false);
      setFinished(done);
      client.setQueryData(keys.open, null);
      void client.invalidateQueries({ queryKey: keys.last });
    },
  });

  if (finished) return <Summary session={finished} />;
  if (open.isPending) return <p className="text-muted-foreground">{dict.common.loading}</p>;
  if (open.isError) {
    return (
      <div className="grid gap-3">
        <FormError message={dict.common.errorGeneric} />
        <Button variant="outline" className="w-fit" onClick={() => void open.refetch()}>
          {dict.common.retry}
        </Button>
      </div>
    );
  }
  if (!open.data) {
    return (
      <section className="grid gap-4">
        <h1 className="text-4xl font-bold">{t.workoutTitle}</h1>
        <p className="text-muted-foreground">{t.noOpen}</p>
        <Button asChild className="w-fit">
          <Link href={to("/train")}>{t.toWeek}</Link>
        </Button>
      </section>
    );
  }

  const session = withPending(open.data, queued);
  const slots = slotsOf(session.sets);
  const left = session.sets.filter((s) => !s.done).length;
  const restFor = (programExerciseId: string | null) =>
    (programExerciseId ? planned.get(programExerciseId)?.rest_s : null) ?? DEFAULT_REST_S;
  const change = (setId: string, patch: SetPatch) => enqueueSetPatch(session.id, setId, patch);
  const requestFinish = () => (left > 0 && !confirming ? setConfirming(true) : finish.mutate(session.id));

  return (
    <section className="grid gap-5 pb-24">
      <div>
        <h1 className="text-4xl font-bold" dir="auto">
          {session.day_name ?? t.workoutTitle}
        </h1>
        <p className="text-sm text-muted-foreground">
          {fill(dict.today.inProgressLine, { done: session.sets.length - left, planned: session.sets.length })}
        </p>
      </div>

      {unsaved > 0 && (queue.stalled || !online) && (
        <p role="status" className="flex items-start gap-2 rounded-xl border border-gold/50 bg-gold/10 p-3 text-sm">
          <CloudOff className="mt-0.5 size-4 shrink-0 text-gold" aria-hidden />
          {fill(t.unsaved, { count: unsaved })}
        </p>
      )}

      {slots.map((slot) => {
        const plan = slot.programExerciseId ? planned.get(slot.programExerciseId) : undefined;
        return (
          <ExerciseCard
            key={slot.key}
            session={session}
            slot={slot}
            plannedExerciseId={plan?.exercise_id ?? null}
            plannedName={plan?.name ?? null}
            online={online}
            onSetChange={change}
            onSetDone={() => timer.start(restFor(slot.programExerciseId))}
          />
        );
      })}

      <div className="grid gap-3 rounded-2xl border border-border bg-card p-4">
        {confirming && <p>{fill(t.finishUnfinished, { count: left })}</p>}
        <div className="flex flex-wrap gap-3">
          <Button size="lg" disabled={finish.isPending} onClick={requestFinish}>
            {confirming ? t.finishConfirm : t.finish}
          </Button>
          {confirming && (
            <Button size="lg" variant="outline" onClick={() => setConfirming(false)}>
              {t.keepGoing}
            </Button>
          )}
        </div>
        <FormError
          message={
            finish.isError
              ? finish.error instanceof Error && finish.error.message === "unsaved"
                ? t.finishOffline
                : t.finishError
              : null
          }
        />
      </div>

      {timer.active && (
        <RestTimer
          remaining={timer.remaining}
          total={timer.total}
          onAdd={() => timer.add(15)}
          onSkip={timer.stop}
        />
      )}
    </section>
  );
}
