import type { ExerciseLast, Program, ProgramDay, SetLog } from "@/lib/api/types";

/** One planned exercise in a workout (or an unplanned one), with its sets in order. */
export type Slot = {
  key: string;
  programExerciseId: string | null;
  sets: SetLog[];
  /** The exercise being done now: the one on the sets still to do, else the last one. */
  exerciseId: string;
  exerciseName: string;
};

/** Group the session's sets (already in program order from the API) by planned slot. */
export function slotsOf(sets: SetLog[]): Slot[] {
  const slots = new Map<string, SetLog[]>();
  for (const set of sets) {
    const key = set.program_exercise_id ?? `extra:${set.exercise_id}`;
    slots.set(key, [...(slots.get(key) ?? []), set]);
  }
  return [...slots.entries()].map(([key, rows]) => {
    const current = rows.find((s) => !s.done) ?? rows[rows.length - 1];
    return {
      key,
      programExerciseId: rows[0].program_exercise_id,
      sets: rows,
      exerciseId: current.exercise_id,
      exerciseName: current.exercise_name,
    };
  });
}

/** The day after the last one trained, going round the week; the first day to begin. */
export function nextDay(program: Program, lastDayId: string | null): ProgramDay | null {
  const days = program.days;
  if (days.length === 0) return null;
  const index = days.findIndex((d) => d.id === lastDayId);
  return index === -1 ? days[0] : days[(index + 1) % days.length];
}

/**
 * Epley estimate, as the API's training rules compute it (1 to 12 reps), used only to
 * show a "new best" badge while lifting. The API decides the records on finish.
 */
export function estimatedMax(weightKg: number, reps: number): number | null {
  if (reps < 1 || reps > 12 || weightKg <= 0) return null;
  return reps === 1 ? weightKg : weightKg * (1 + reps / 30);
}

export function beatsBest(set: SetLog, last: ExerciseLast | undefined): boolean {
  const best = last?.personal_best?.best_e1rm?.estimated_1rm_kg;
  if (!set.done || set.weight_kg === null || set.reps === null || !best) return false;
  const estimate = estimatedMax(set.weight_kg, set.reps);
  return estimate !== null && estimate > best + 0.05; // the API rounds the best to 0.1 kg
}
