"use client";

import { cn } from "@/lib/utils";

/**
 * Setup progress drawn as a barbell: each finished step loads a plate, alternating
 * sides from the collar outwards, so a finished setup is a fully loaded bar.
 * It mirrors itself in right-to-left layouts.
 */
export function Barbell({
  loaded,
  plates,
  label,
}: {
  loaded: number;
  plates: number;
  label: string;
}) {
  const perSide = Math.ceil(plates / 2);
  // Plate i (0-based, inner to outer) on each side; side A takes even steps, side B odd.
  const side = (offset: 0 | 1) =>
    Array.from({ length: perSide }, (_, i) => i * 2 + offset).filter((n) => n < plates);
  const plate = (n: number, i: number) => (
    <span
      key={n}
      aria-hidden
      className={cn(
        "block w-2.5 rounded-[3px] transition-[background-color,transform] duration-300 motion-safe:ease-out sm:w-3",
        n < loaded ? "scale-y-100 bg-primary" : "scale-y-75 bg-plate",
      )}
      // Inner plates are the biggest, like real bumper plates sorted heavy to light.
      style={{ height: `${2.75 - i * 0.3}rem` }}
    />
  );

  return (
    <div
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={plates}
      aria-valuenow={loaded}
      aria-label={label}
      className="flex items-center justify-center"
    >
      <span aria-hidden className="h-1.5 w-6 rounded-s-full bg-gold/70" />
      <span className="flex flex-row-reverse items-center gap-1">{side(0).map(plate)}</span>
      <span aria-hidden className="h-2.5 w-1.5 bg-gold" />
      <span aria-hidden className="h-1.5 w-20 bg-gold/70 sm:w-32" />
      <span aria-hidden className="h-2.5 w-1.5 bg-gold" />
      <span className="flex items-center gap-1">{side(1).map(plate)}</span>
      <span aria-hidden className="h-1.5 w-6 rounded-e-full bg-gold/70" />
    </div>
  );
}
