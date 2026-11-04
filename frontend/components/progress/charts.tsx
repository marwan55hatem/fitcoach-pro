"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ReferenceArea,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { useI18n } from "@/lib/i18n/provider";

const AXIS = { stroke: "var(--muted-foreground)", fontSize: 12 };
const TOOLTIP = {
  contentStyle: {
    background: "var(--popover)",
    border: "1px solid var(--border)",
    borderRadius: 12,
    color: "var(--foreground)",
  },
  labelStyle: { color: "var(--muted-foreground)" },
};

export type Series = { key: string; label: string; color: string; dashed?: boolean };

/** Short day labels ("3 Nov") in the user's language. */
export function useShortDate() {
  const { locale } = useI18n();
  return (iso: string) =>
    new Date(`${iso}T12:00:00Z`).toLocaleDateString(locale, { day: "numeric", month: "short" });
}

/** Lines over time. Charts read left to right in both languages, like the numbers. */
export function TimeChart({
  data,
  series,
  label,
}: {
  data: Record<string, string | number | null>[];
  series: Series[];
  label: string;
}) {
  const short = useShortDate();
  return (
    <div className="h-56 w-full" dir="ltr" role="img" aria-label={label}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: -16 }}>
          <CartesianGrid stroke="var(--border)" vertical={false} />
          <XAxis dataKey="date" tickFormatter={short} {...AXIS} minTickGap={24} />
          <YAxis domain={["auto", "auto"]} {...AXIS} width={48} />
          <Tooltip {...TOOLTIP} labelFormatter={(v) => short(String(v))} />
          {series.length > 1 && <Legend wrapperStyle={{ fontSize: 12 }} />}
          {series.map((s) => (
            <Line
              key={s.key}
              type="monotone"
              dataKey={s.key}
              name={s.label}
              stroke={s.color}
              strokeWidth={s.dashed ? 2 : 2.5}
              strokeDasharray={s.dashed ? "6 4" : undefined}
              dot={data.length < 12}
              connectNulls
              isAnimationActive={false}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

/** Sets per muscle this week, with the target range for the user's level as a band. */
export function VolumeBars({
  data,
  min,
  max,
  label,
  setsLabel,
}: {
  data: { muscle: string; sets: number }[];
  min: number | null;
  max: number | null;
  label: string;
  setsLabel: string;
}) {
  return (
    <div className="w-full" dir="ltr" role="img" aria-label={label} style={{ height: 36 + data.length * 30 }}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} layout="vertical" margin={{ top: 4, right: 12, bottom: 4, left: 8 }}>
          <CartesianGrid stroke="var(--border)" horizontal={false} />
          {min !== null && max !== null && (
            <ReferenceArea x1={min} x2={max} fill="var(--success)" fillOpacity={0.12} stroke="none" />
          )}
          <XAxis type="number" {...AXIS} allowDecimals={false} domain={[0, (dataMax: number) => Math.max(dataMax, max ?? 0)]} />
          <YAxis type="category" dataKey="muscle" {...AXIS} width={96} />
          <Tooltip {...TOOLTIP} cursor={{ fill: "var(--accent)" }} />
          <Bar dataKey="sets" name={setsLabel} fill="var(--primary)" radius={[0, 6, 6, 0]} isAnimationActive={false} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
