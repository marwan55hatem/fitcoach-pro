"use client";

import { api, ApiError } from "@/lib/api/client";
import type { SetLog, SetPatch, WorkoutSession } from "@/lib/api/types";

/**
 * Set changes waiting to reach the API, so a set logged with no signal is not lost.
 *
 * Changes to the same set are merged (the newest value wins) and sent one at a time.
 * A dropped connection or a server error keeps the change and retries with backoff and
 * as soon as the browser is back online. The queue lives in memory only: workout data
 * is health data and never goes into browser storage, so the page warns before closing
 * while something is unsaved.
 */
export type PendingSet = { sessionId: string; setId: string; patch: SetPatch };
export type QueueEvent =
  | { type: "saved"; entry: PendingSet; set: SetLog }
  | { type: "rejected"; entry: PendingSet; error: unknown };

const pending = new Map<string, PendingSet>(); // by set id, oldest first
let inFlight: PendingSet | null = null;
let loop: Promise<boolean> | null = null;
let failures = 0;
let retryTimer: ReturnType<typeof setTimeout> | null = null;

const listeners = new Set<() => void>();
const eventHandlers = new Set<(event: QueueEvent) => void>();
export type QueueState = { entries: PendingSet[]; stalled: boolean };
let snapshot: QueueState = { entries: [], stalled: false };

function changed(): void {
  snapshot = {
    entries: [...(inFlight ? [inFlight] : []), ...pending.values()],
    // Waiting for a retry: worth telling the user (a normal save takes a moment).
    stalled: retryTimer !== null,
  };
  listeners.forEach((listener) => listener());
}

function emit(event: QueueEvent): void {
  eventHandlers.forEach((handler) => handler(event));
}

/** Worth retrying: the request never arrived, or the server failed or was busy. */
function retryable(error: unknown): boolean {
  if (error instanceof ApiError) return error.status >= 500 || error.status === 408 || error.status === 429;
  return true; // fetch itself threw: offline or the connection dropped
}

function scheduleRetry(): void {
  if (retryTimer) return;
  const delay = Math.min(30_000, 2_000 * 2 ** Math.min(failures, 4));
  retryTimer = setTimeout(() => {
    retryTimer = null;
    changed();
    void flushSetQueue();
  }, delay);
}

async function run(): Promise<boolean> {
  while (pending.size > 0) {
    const [setId, entry] = pending.entries().next().value as [string, PendingSet];
    pending.delete(setId);
    inFlight = entry;
    changed();
    try {
      const set = await api<SetLog>(`/sessions/${entry.sessionId}/sets/${setId}`, {
        method: "PATCH",
        body: entry.patch,
      });
      failures = 0;
      emit({ type: "saved", entry, set });
    } catch (error) {
      if (retryable(error)) {
        // Put it back first in line, under anything newer typed in the meantime.
        const newer = pending.get(setId);
        const rest = [...pending.entries()].filter(([id]) => id !== setId);
        pending.clear();
        pending.set(setId, { ...entry, patch: { ...entry.patch, ...newer?.patch } });
        rest.forEach(([id, value]) => pending.set(id, value));
        inFlight = null;
        failures += 1;
        scheduleRetry();
        changed();
        return false;
      }
      emit({ type: "rejected", entry, error });
    }
    inFlight = null;
    changed();
  }
  return true;
}

/** Send everything waiting. Resolves true once the queue is empty. */
export function flushSetQueue(): Promise<boolean> {
  if (pending.size === 0 && !loop) return Promise.resolve(true);
  loop ??= run().finally(() => {
    loop = null;
  });
  return loop;
}

export function enqueueSetPatch(sessionId: string, setId: string, patch: SetPatch): void {
  const previous = pending.get(setId);
  pending.set(setId, { sessionId, setId, patch: { ...previous?.patch, ...patch } });
  changed();
  if (!retryTimer) void flushSetQueue();
}

export function subscribeSetQueue(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function setQueueSnapshot(): QueueState {
  return snapshot;
}

const EMPTY: QueueState = { entries: [], stalled: false };
export function serverSetQueueSnapshot(): QueueState {
  return EMPTY;
}

export function onSetQueueEvent(handler: (event: QueueEvent) => void): () => void {
  eventHandlers.add(handler);
  return () => eventHandlers.delete(handler);
}

/** The session as the user sees it: the server's copy with unsaved changes on top. */
export function withPending(session: WorkoutSession, queued: PendingSet[]): WorkoutSession {
  const mine = queued.filter((entry) => entry.sessionId === session.id);
  if (mine.length === 0) return session;
  return {
    ...session,
    sets: session.sets.map((set) =>
      mine.reduce((acc, entry) => (entry.setId === set.id ? { ...acc, ...entry.patch } : acc), set),
    ),
  };
}

if (typeof window !== "undefined") {
  window.addEventListener("online", () => {
    if (retryTimer) clearTimeout(retryTimer);
    retryTimer = null;
    changed();
    void flushSetQueue();
  });
}
