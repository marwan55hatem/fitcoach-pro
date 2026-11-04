"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Trophy } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { FormError } from "@/components/field";
import { TimeChart, useShortDate, VolumeBars } from "@/components/progress/charts";
import { InbodyReview } from "@/components/progress/inbody-review";
import { InbodyUpload } from "@/components/progress/inbody-upload";
import { PhotosSection } from "@/components/progress/photos-section";
import { api } from "@/lib/api/client";
import type {
  InbodyScan,
  InbodyTrend,
  PersonalRecord,
  VolumeStats,
  WeightHistory,
} from "@/lib/api/types";
import { fill, useI18n } from "@/lib/i18n/provider";
import { formatKg } from "@/lib/numbers";

function Card({ id, title, lead, children }: { id: string; title: string; lead?: string; children: React.ReactNode }) {
  return (
    <section className="grid gap-4 rounded-2xl border border-border bg-card p-4" aria-labelledby={id}>
      <div className="grid gap-1">
        <h2 id={id} className="text-xl font-semibold">
          {title}
        </h2>
        {lead && <p className="text-sm text-muted-foreground">{lead}</p>}
      </div>
      {children}
    </section>
  );
}

function Loading({ query }: { query: { isPending: boolean; isError: boolean } }) {
  const { dict } = useI18n();
  if (query.isPending) return <div className="h-40 animate-pulse rounded-xl bg-muted" />;
  if (query.isError) return <FormError message={dict.common.errorGeneric} />;
  return null;
}

function WeightCard() {
  const { dict } = useI18n();
  const t = dict.progress;
  const weight = useQuery({ queryKey: ["weight", 90], queryFn: () => api<WeightHistory>("/weight?days=90") });
  return (
    <Card id="weight-chart" title={t.weightTitle}>
      <Loading query={weight} />
      {weight.data && weight.data.points.length === 0 && <p className="text-sm text-muted-foreground">{t.weightEmpty}</p>}
      {weight.data && weight.data.points.length > 0 && (
        <TimeChart
          label={t.weightTitle}
          data={weight.data.points.map((p) => ({ date: p.date, weight: p.weight_kg, average: p.average_7d }))}
          series={[
            { key: "weight", label: t.weight, color: "var(--muted-foreground)" },
            { key: "average", label: t.average, color: "var(--primary)" },
          ]}
        />
      )}
    </Card>
  );
}

function InbodyCard() {
  const { dict } = useI18n();
  const t = dict.progress;
  const short = useShortDate();
  const client = useQueryClient();
  const [fresh, setFresh] = useState<InbodyScan[]>([]);
  const drafts = useQuery({ queryKey: ["inbody", "drafts"], queryFn: () => api<InbodyScan[]>("/inbody/drafts") });
  const trend = useQuery({ queryKey: ["inbody", "trend"], queryFn: () => api<InbodyTrend>("/inbody/trend") });

  const pending = [...fresh, ...(drafts.data ?? []).filter((d) => !fresh.some((f) => f.id === d.id))];
  const change = trend.data?.change;
  const sign = (n: number | null) => (n === null ? "–" : `${n > 0 ? "+" : ""}${formatKg(n)}`);

  return (
    <Card id="inbody" title={t.inbodyTitle} lead={t.inbodyLead}>
      <InbodyUpload onDraft={(scan) => setFresh((list) => [scan, ...list])} />
      {pending.length > 0 && (
        <div className="grid gap-3">
          <h3 className="font-semibold">{t.drafts}</h3>
          {pending.map((scan) => (
            <InbodyReview
              key={scan.id}
              scan={scan}
              onConfirmed={() => {
                toast.success(t.confirmed);
                setFresh((list) => list.filter((s) => s.id !== scan.id));
                void client.invalidateQueries({ queryKey: ["inbody"] });
                void client.invalidateQueries({ queryKey: ["food-day"] }); // targets may use the new BMR
              }}
            />
          ))}
        </div>
      )}
      <div className="grid gap-3">
        <h3 className="font-semibold">{t.trendTitle}</h3>
        <Loading query={trend} />
        {trend.data && trend.data.points.length === 0 && <p className="text-sm text-muted-foreground">{t.noScans}</p>}
        {trend.data && trend.data.points.length === 1 && <p className="text-sm text-muted-foreground">{t.oneScan}</p>}
        {trend.data && trend.data.points.length > 0 && (
          <>
            <TimeChart
              label={t.trendTitle}
              data={trend.data.points.map((p) => ({ date: p.date, muscle: p.smm_kg, fat: p.body_fat_mass_kg }))}
              series={[
                { key: "muscle", label: t.muscle, color: "var(--success)" },
                { key: "fat", label: t.fat, color: "var(--gold)" },
              ]}
            />
            <TimeChart
              label={t.bodyFat}
              data={trend.data.points.map((p) => ({ date: p.date, pbf: p.pbf_pct }))}
              series={[{ key: "pbf", label: t.bodyFat, color: "var(--chart-4)" }]}
            />
          </>
        )}
        {change && (
          <div className="grid gap-1 rounded-xl bg-muted p-3 text-sm">
            <p className="font-medium" dir="auto">
              {fill(t.changeSince, {
                date: short(change.from_date),
                smm: sign(change.smm_kg),
                fat: sign(change.body_fat_mass_kg),
              })}
            </p>
            {change.interpretation_code && (
              <p className="text-muted-foreground">
                {t.interpretations[change.interpretation_code] ?? change.interpretation}
              </p>
            )}
          </div>
        )}
      </div>
    </Card>
  );
}

function VolumeCard() {
  const { dict } = useI18n();
  const t = dict.progress;
  const volume = useQuery({ queryKey: ["volume", 1], queryFn: () => api<VolumeStats>("/stats/volume?weeks=1") });
  const week = volume.data?.weeks[0];
  const data = week
    ? Object.entries(week.sets_by_muscle)
        .map(([muscle, sets]) => ({ muscle: dict.muscles[muscle] ?? muscle, sets }))
        .sort((a, b) => b.sets - a.sets)
    : [];
  return (
    <Card
      id="volume"
      title={t.volumeTitle}
      lead={
        volume.data?.target_min != null && volume.data.target_max != null
          ? fill(t.volumeLead, { min: volume.data.target_min, max: volume.data.target_max })
          : undefined
      }
    >
      <Loading query={volume} />
      {volume.data && data.length === 0 && <p className="text-sm text-muted-foreground">{t.volumeEmpty}</p>}
      {data.length > 0 && (
        <VolumeBars
          data={data}
          min={volume.data?.target_min ?? null}
          max={volume.data?.target_max ?? null}
          label={t.volumeTitle}
          setsLabel={t.sets}
        />
      )}
    </Card>
  );
}

function RecordsCard() {
  const { dict } = useI18n();
  const t = dict.progress;
  const short = useShortDate();
  const records = useQuery({ queryKey: ["records"], queryFn: () => api<PersonalRecord[]>("/stats/records") });
  return (
    <Card id="records" title={t.recordsTitle}>
      <Loading query={records} />
      {records.data?.length === 0 && <p className="text-sm text-muted-foreground">{t.recordsEmpty}</p>}
      <ul className="divide-y divide-border">
        {records.data?.map((r) => (
          <li key={r.exercise_id} className="flex items-start gap-3 py-3">
            <Trophy className="mt-0.5 size-5 shrink-0 text-gold" aria-hidden />
            <div className="grid flex-1 gap-0.5">
              <span className="font-medium" dir="auto">
                {r.name}
              </span>
              {r.heaviest && (
                <span className="text-sm text-muted-foreground">
                  {fill(t.heaviest, { kg: formatKg(r.heaviest.weight_kg), reps: r.heaviest.reps })}
                </span>
              )}
              {r.best_e1rm?.estimated_1rm_kg != null && (
                <span className="text-sm text-gold">
                  {fill(t.estimatedMax, { kg: formatKg(r.best_e1rm.estimated_1rm_kg) })}
                </span>
              )}
            </div>
            <span className="shrink-0 text-xs text-muted-foreground">{short(r.date)}</span>
          </li>
        ))}
      </ul>
    </Card>
  );
}

export function ProgressView() {
  const { dict } = useI18n();
  return (
    <div className="grid gap-5">
      <h1 className="text-4xl font-bold">{dict.progress.title}</h1>
      <WeightCard />
      <InbodyCard />
      <PhotosSection />
      <VolumeCard />
      <RecordsCard />
    </div>
  );
}
