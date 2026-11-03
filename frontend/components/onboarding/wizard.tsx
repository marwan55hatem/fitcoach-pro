"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, ArrowRight } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Splash, useSignedInUser } from "@/components/app-shell";
import { Brand } from "@/components/brand";
import { LanguageSwitch } from "@/components/language-switch";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api/client";
import type {
  ConsentType,
  Consents,
  Diet,
  Limitations,
  OnboardingStatus,
  Profile,
  ProfileIn,
  Screening,
} from "@/lib/api/types";
import { fill, useI18n } from "@/lib/i18n/provider";
import { useLocalePath } from "@/lib/i18n/use-locale-path";

import { Barbell } from "./barbell";
import { ExtrasStep, SummaryStep } from "./steps-final";
import { ConsentStep, FoodStep, InjuriesStep, ScreeningStep } from "./steps-health";
import { BasicsStep, GoalStep, type ProfileDraft, ScheduleStep } from "./steps-profile";

const STEPS = [
  "consent",
  "basics",
  "goal",
  "schedule",
  "injuries",
  "food",
  "screening",
  "extras",
  "summary",
] as const;
type Step = (typeof STEPS)[number];
const PLATES = STEPS.length - 1; // every step before the summary loads a plate

type Snapshot = {
  status: OnboardingStatus;
  consents: Record<ConsentType, boolean>;
  profile: Profile | null;
  limitations: Limitations | null;
  diet: Diet | null;
  screening: Screening | null;
};

async function loadSnapshot(): Promise<Snapshot> {
  const [status, consentList] = await Promise.all([
    api<OnboardingStatus>("/onboarding/status"),
    api<Consents>("/consents"),
  ]);
  const consents = Object.fromEntries(
    consentList.consents.map((c) => [c.type, c.granted]),
  ) as Record<ConsentType, boolean>;
  if (!consents.health_data) {
    return { status, consents, profile: null, limitations: null, diet: null, screening: null };
  }
  const [profile, limitations, diet, screening] = await Promise.all([
    api<Profile>("/onboarding/profile"),
    api<Limitations>("/onboarding/limitations"),
    api<Diet | null>("/onboarding/diet"),
    api<Screening | null>("/onboarding/screening"),
  ]);
  return { status, consents, profile, limitations, diet, screening };
}

/** Where to pick up: the first step the API does not have yet. */
function resumeAt(s: Snapshot): Step {
  const done = (key: string) => s.status.steps.find((x) => x.key === key)?.completed ?? false;
  if (!done("consent")) return "consent";
  if (!done("profile")) return "basics";
  if (!done("limitations")) return "injuries";
  if (!done("diet")) return "food";
  if (!done("screening") || (s.status.clearance_required && !s.status.clearance_confirmed)) {
    return "screening";
  }
  return "summary";
}

function draftFrom(profile: Profile | null): ProfileDraft {
  return {
    birth_date: profile?.birth_date ?? "",
    sex: profile?.sex ?? null,
    height_cm: profile?.height_cm ? String(profile.height_cm) : "",
    weight_kg: profile?.weight_kg ? String(profile.weight_kg) : "",
    goal: profile?.goal ?? null,
    experience: profile?.experience ?? null,
    activity_level: profile?.activity_level ?? null,
    days_per_week: profile?.days_per_week ?? null,
    session_minutes: profile?.session_minutes ?? null,
    equipment: profile?.equipment ?? [],
  };
}

function timezone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "Africa/Cairo";
  } catch {
    return "Africa/Cairo";
  }
}

export function OnboardingWizard() {
  const { dict, locale } = useI18n();
  const { ready } = useSignedInUser();
  const router = useRouter();
  const to = useLocalePath();
  const queryClient = useQueryClient();

  const snapshot = useQuery({ queryKey: ["onboarding-snapshot"], queryFn: loadSnapshot, enabled: ready });
  const [step, setStep] = useState<Step | null>(null);
  const [draft, setDraftState] = useState<ProfileDraft>(() => draftFrom(null));
  const [consents, setConsents] = useState<Record<ConsentType, boolean> | null>(null);
  const [screening, setScreening] = useState<Screening | null>(null);

  // Start once, where the saved answers end. Later refetches never move the user.
  useEffect(() => {
    if (step !== null || !snapshot.data) return;
    /* eslint-disable react-hooks/set-state-in-effect -- one-time hydration from the API */
    setDraftState(draftFrom(snapshot.data.profile));
    setConsents(snapshot.data.consents);
    setScreening(snapshot.data.screening);
    setStep(resumeAt(snapshot.data));
    /* eslint-enable react-hooks/set-state-in-effect */
  }, [snapshot.data, step]);

  if (!ready || snapshot.isPending || step === null || consents === null) {
    if (snapshot.isError) {
      return (
        <div className="grid min-h-dvh place-items-center gap-3 p-6 text-center">
          <p>{dict.common.errorGeneric}</p>
          <Button onClick={() => snapshot.refetch()}>{dict.common.retry}</Button>
        </div>
      );
    }
    return <Splash />;
  }

  const index = STEPS.indexOf(step);
  const go = (next: Step) => {
    setStep(next);
    window.scrollTo({ top: 0 });
  };
  const forward = () => go(STEPS[Math.min(index + 1, STEPS.length - 1)]);
  const setDraft = (update: Partial<ProfileDraft>) => setDraftState((d) => ({ ...d, ...update }));

  const saveProfile = async () => {
    const body: ProfileIn = {
      birth_date: draft.birth_date,
      sex: draft.sex!,
      height_cm: Number(draft.height_cm),
      weight_kg: Number(draft.weight_kg),
      goal: draft.goal!,
      experience: draft.experience!,
      activity_level: draft.activity_level!,
      days_per_week: draft.days_per_week!,
      session_minutes: draft.session_minutes!,
      equipment: draft.equipment,
      timezone: timezone(),
      language: locale,
    };
    await api("/onboarding/profile", { method: "PUT", body });
    forward();
  };

  const finish = async () => {
    // Drop cached answers (e.g. "setup not finished") so the app shell reads fresh ones.
    queryClient.removeQueries({ queryKey: ["onboarding-status"] });
    queryClient.removeQueries({ queryKey: ["program"] });
    router.replace(to("/today"));
  };

  const BackIcon = locale === "ar" ? ArrowRight : ArrowLeft;
  // Going back from the result of the health check would hide it; the step has its own edit.
  const canGoBack = index > 0 && step !== "summary";

  return (
    <div className="mx-auto flex min-h-dvh w-full max-w-xl flex-col px-5 pt-[max(0.75rem,env(safe-area-inset-top))]">
      <header className="flex items-center justify-between py-2">
        {canGoBack ? (
          <Button variant="ghost" size="icon" aria-label={dict.common.back} onClick={() => go(STEPS[index - 1])}>
            <BackIcon aria-hidden />
          </Button>
        ) : (
          <Brand />
        )}
        <LanguageSwitch />
      </header>

      <div className="grid gap-2 pb-6 pt-4">
        <Barbell
          loaded={index}
          plates={PLATES}
          label={fill(dict.onboarding.stepOf, { current: index + 1, total: STEPS.length })}
        />
        <p className="text-center text-sm text-muted-foreground">
          {fill(dict.onboarding.stepOf, { current: index + 1, total: STEPS.length })}
          {": "}
          {dict.onboarding.steps[step]}
        </p>
      </div>

      <div className="flex flex-1 flex-col" key={step}>
        {step === "consent" && (
          <ConsentStep
            initial={consents}
            onDone={(granted) => {
              setConsents(granted);
              forward();
            }}
          />
        )}
        {step === "basics" && <BasicsStep draft={draft} setDraft={setDraft} onDone={forward} />}
        {step === "goal" && <GoalStep draft={draft} setDraft={setDraft} onDone={forward} />}
        {step === "schedule" && <ScheduleStep draft={draft} setDraft={setDraft} onSave={saveProfile} />}
        {step === "injuries" && (
          <InjuriesStep
            initial={snapshot.data?.limitations?.limitations ?? []}
            onDone={forward}
          />
        )}
        {step === "food" && <FoodStep initial={snapshot.data?.diet ?? null} onDone={forward} />}
        {step === "screening" && (
          <ScreeningStep sex={draft.sex} current={screening} onScreened={setScreening} onDone={forward} />
        )}
        {step === "extras" && <ExtrasStep onDone={forward} />}
        {step === "summary" && (
          <SummaryStep
            useAi={consents.ai_processing}
            planAllowed={
              screening === null ||
              screening.risk_level === "ok" ||
              screening.clearance_confirmed
            }
            onAccepted={finish}
          />
        )}
      </div>
    </div>
  );
}

