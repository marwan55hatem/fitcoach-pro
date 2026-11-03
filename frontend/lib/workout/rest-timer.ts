"use client";

import { useCallback, useEffect, useRef, useState } from "react";

export const DEFAULT_REST_S = 90;

type Rest = { endsAt: number; total: number };

/** Three short beeps. The audio context is unlocked by the tap that started the rest. */
function beep(ctx: AudioContext): void {
  for (let i = 0; i < 3; i++) {
    const start = ctx.currentTime + i * 0.25;
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.frequency.value = 880;
    gain.gain.setValueAtTime(0.25, start);
    gain.gain.exponentialRampToValueAtTime(0.001, start + 0.18);
    osc.connect(gain).connect(ctx.destination);
    osc.start(start);
    osc.stop(start + 0.2);
  }
}

/**
 * A rest countdown measured against the clock, so it stays right when the phone
 * throttles a background tab. At zero it beeps and vibrates once.
 */
export function useRestTimer() {
  const [rest, setRest] = useState<Rest | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const audio = useRef<AudioContext | null>(null);

  const start = useCallback((seconds: number) => {
    try {
      audio.current ??= new AudioContext();
      void audio.current.resume();
    } catch {
      audio.current = null; // no audio on this device: vibration only
    }
    const at = Date.now();
    setNow(at);
    setRest({ endsAt: at + seconds * 1000, total: seconds });
  }, []);

  const add = useCallback((seconds: number) => {
    setRest((r) => (r ? { endsAt: r.endsAt + seconds * 1000, total: r.total + seconds } : r));
  }, []);

  const stop = useCallback(() => setRest(null), []);

  useEffect(() => {
    if (!rest) return;
    const id = setInterval(() => {
      const at = Date.now();
      setNow(at);
      if (at >= rest.endsAt) {
        clearInterval(id);
        if (audio.current) beep(audio.current);
        navigator.vibrate?.([200, 100, 200]);
        setTimeout(() => setRest((r) => (r === rest ? null : r)), 4_000);
      }
    }, 250);
    return () => clearInterval(id);
  }, [rest]);

  const remaining = rest ? Math.max(0, Math.ceil((rest.endsAt - now) / 1000)) : 0;
  return { active: rest !== null, remaining, total: rest?.total ?? 0, start, add, stop };
}

export function formatClock(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}
