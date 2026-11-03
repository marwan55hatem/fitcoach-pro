"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, ApiError } from "@/lib/api/client";
import type { Program, SessionList, WorkoutSession } from "@/lib/api/types";

async function orNull<T>(load: () => Promise<T>): Promise<T | null> {
  try {
    return await load();
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return null;
    throw error;
  }
}

export const keys = {
  program: ["program", "current"] as const,
  open: ["session", "open"] as const,
  last: ["sessions", "last"] as const,
};

export function useProgram() {
  return useQuery({
    queryKey: keys.program,
    queryFn: () => orNull(() => api<Program>("/programs/current")),
  });
}

/** The unfinished workout, or null. There is at most one. */
export function useOpenSession() {
  return useQuery({
    queryKey: keys.open,
    queryFn: () => orNull(() => api<WorkoutSession>("/sessions/open")),
  });
}

/** The most recent workout, to work out which day comes next. */
export function useLastSession() {
  return useQuery({
    queryKey: keys.last,
    queryFn: async () => (await api<SessionList>("/sessions?limit=1")).items[0] ?? null,
  });
}

/** Start a program day. If a workout is already open, carry on with that one instead. */
export function useStartWorkout() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (dayId: string) => {
      try {
        return await api<WorkoutSession>("/sessions", {
          method: "POST",
          body: { program_day_id: dayId },
        });
      } catch (error) {
        if (error instanceof ApiError && error.code === "session_open") {
          return api<WorkoutSession>("/sessions/open");
        }
        throw error;
      }
    },
    onSuccess: (session) => client.setQueryData(keys.open, session),
  });
}
