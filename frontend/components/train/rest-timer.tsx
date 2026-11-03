"use client";

import { Button } from "@/components/ui/button";
import { fill, useI18n } from "@/lib/i18n/provider";
import { formatClock } from "@/lib/workout/rest-timer";

/** Floats above the tab bar while resting: a big clock and a draining bar. */
export function RestTimer({
  remaining,
  total,
  onAdd,
  onSkip,
}: {
  remaining: number;
  total: number;
  onAdd: () => void;
  onSkip: () => void;
}) {
  const { dict } = useI18n();
  const t = dict.train;
  const over = remaining === 0;
  const share = total > 0 ? remaining / total : 0;
  return (
    <div className="fixed inset-x-3 bottom-[calc(4.25rem+env(safe-area-inset-bottom))] z-50 mx-auto max-w-md overflow-hidden rounded-2xl border border-border bg-popover shadow-2xl md:bottom-6">
      {/* Drains toward the reading start, so it mirrors in Arabic. */}
      <div
        className="h-1.5 bg-primary transition-[inline-size] duration-300 ease-linear"
        style={{ inlineSize: `${share * 100}%` }}
        aria-hidden
      />
      <div className="flex items-center gap-3 p-3">
        <div className="grid flex-1">
          <span className="text-xs text-muted-foreground">{t.rest}</span>
          <span
            role="timer"
            aria-label={over ? t.restDone : fill(t.restLeft, { time: formatClock(remaining) })}
            className={over ? "font-heading text-2xl font-bold text-success" : "font-heading text-5xl font-bold tabular-nums leading-none"}
            dir={over ? "auto" : "ltr"}
          >
            {over ? t.restDone : formatClock(remaining)}
          </span>
        </div>
        {!over && (
          <Button variant="outline" onClick={onAdd} dir="ltr">
            {t.addRest}
          </Button>
        )}
        <Button variant={over ? "default" : "ghost"} onClick={onSkip}>
          {over ? dict.train.done : t.skipRest}
        </Button>
      </div>
      <p className="sr-only" aria-live="assertive">
        {over ? t.restDone : ""}
      </p>
    </div>
  );
}
