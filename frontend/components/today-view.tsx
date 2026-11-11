"use client";

import { Gauge } from "lucide-react";

import { ReviewSection } from "@/components/review/review-section";
import { CheckinCard } from "@/components/today/checkin-card";
import { FoodCard } from "@/components/today/food-card";
import { NextWorkout } from "@/components/today/next-workout";
import { WeightCard } from "@/components/today/weight-card";
import { useAuth } from "@/lib/auth/auth-provider";
import { fill, useI18n } from "@/lib/i18n/provider";

/** A card for something that arrives in a later update, so the layout is already there. */
function Upcoming({ icon: Icon, title, body }: { icon: typeof Gauge; title: string; body: string }) {
  return (
    <section className="flex gap-3 rounded-2xl border border-dashed border-border p-5">
      <Icon className="mt-0.5 size-5 shrink-0 text-muted-foreground" aria-hidden />
      <div className="grid gap-1">
        <h2 className="font-semibold">{title}</h2>
        <p className="text-sm text-muted-foreground">{body}</p>
      </div>
    </section>
  );
}

export function TodayView() {
  const { dict } = useI18n();
  const t = dict.today;
  const { user } = useAuth();

  return (
    <div className="grid gap-5">
      <h1 className="text-4xl font-bold">
        {user?.display_name ? fill(t.greeting, { name: user.display_name }) : t.title}
      </h1>
      <NextWorkout />
      <ReviewSection />
      <div className="grid gap-5 md:grid-cols-2">
        <WeightCard />
        <FoodCard />
      </div>
      <CheckinCard />
      <Upcoming icon={Gauge} title={t.readinessTitle} body={t.readinessBody} />
    </div>
  );
}
