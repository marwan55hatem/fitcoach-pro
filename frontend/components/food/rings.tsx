"use client";

import { fill, useI18n } from "@/lib/i18n/provider";

/** A ring that fills toward a target; past the target it turns gold and stays full. */
function Ring({
  label,
  value,
  target,
  unit,
  tone,
}: {
  label: string;
  value: number;
  target: number;
  unit: string;
  tone: string;
}) {
  const { dict } = useI18n();
  const size = 132;
  const stroke = 12;
  const radius = (size - stroke) / 2;
  const length = 2 * Math.PI * radius;
  const share = target > 0 ? Math.min(1, value / target) : 0;
  const over = target > 0 && value > target;
  return (
    <figure className="grid justify-items-center gap-1">
      <div className="relative" style={{ width: size, height: size }}>
        <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="-rotate-90" aria-hidden>
          <circle cx={size / 2} cy={size / 2} r={radius} fill="none" stroke="var(--muted)" strokeWidth={stroke} />
          <circle
            cx={size / 2}
            cy={size / 2}
            r={radius}
            fill="none"
            stroke={over ? "var(--gold)" : tone}
            strokeWidth={stroke}
            strokeLinecap="round"
            strokeDasharray={length}
            strokeDashoffset={length * (1 - share)}
            className="transition-[stroke-dashoffset] duration-500"
          />
        </svg>
        <div className="absolute inset-0 grid place-content-center text-center">
          <span className="font-heading text-3xl font-bold tabular-nums leading-none" dir="ltr">
            {Math.round(value)}
          </span>
          <span className="text-xs text-muted-foreground">
            {fill(dict.food.ofTarget, { target: `${Math.round(target)} ${unit}` })}
          </span>
        </div>
      </div>
      <figcaption className="text-sm font-medium">{label}</figcaption>
    </figure>
  );
}

export function Rings({
  kcal,
  kcalTarget,
  protein,
  proteinTarget,
}: {
  kcal: number;
  kcalTarget: number;
  protein: number;
  proteinTarget: number;
}) {
  const { dict } = useI18n();
  const t = dict.food;
  return (
    <div className="flex justify-center gap-6 sm:gap-10">
      <Ring label={t.calories} value={kcal} target={kcalTarget} unit={t.kcalUnit} tone="var(--primary)" />
      <Ring label={t.protein} value={protein} target={proteinTarget} unit={t.gramsUnit} tone="var(--success)" />
    </div>
  );
}
