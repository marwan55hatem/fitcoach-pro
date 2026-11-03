"use client";

import { useState } from "react";

import { Field } from "@/components/field";
import { Input } from "@/components/ui/input";
import { ApiError } from "@/lib/api/client";
import { apiErrorMessage } from "@/lib/api/errors";
import {
  EQUIPMENT,
  type Activity,
  type Equipment,
  type Experience,
  type Goal,
  type Sex,
} from "@/lib/api/types";
import { useI18n } from "@/lib/i18n/provider";

import { ChoiceGroup, MultiChoice, NumberChoice } from "./choices";
import { StepFrame } from "./step-frame";

/** Profile answers gathered over three screens and saved together on the third. */
export type ProfileDraft = {
  birth_date: string;
  sex: Sex | null;
  height_cm: string;
  weight_kg: string;
  goal: Goal | null;
  experience: Experience | null;
  activity_level: Activity | null;
  days_per_week: number | null;
  session_minutes: number | null;
  equipment: Equipment[];
};

type StepProps = {
  draft: ProfileDraft;
  setDraft: (update: Partial<ProfileDraft>) => void;
  onDone: () => void;
};

function options<T extends string>(values: T[], labels: Record<T, string>) {
  return values.map((value) => ({ value, label: labels[value] }));
}

function age(birthDate: string): number {
  const born = new Date(`${birthDate}T00:00:00`);
  const now = new Date();
  let years = now.getFullYear() - born.getFullYear();
  const beforeBirthday =
    now.getMonth() < born.getMonth() ||
    (now.getMonth() === born.getMonth() && now.getDate() < born.getDate());
  if (beforeBirthday) years -= 1;
  return years;
}

export function BasicsStep({ draft, setDraft, onDone }: StepProps) {
  const { dict } = useI18n();
  const t = dict.onboarding.basics;
  const [errors, setErrors] = useState<Partial<Record<keyof ProfileDraft, string>>>({});

  const submit = () => {
    const next: typeof errors = {};
    const height = Number(draft.height_cm);
    const weight = Number(draft.weight_kg);
    if (!draft.birth_date) next.birth_date = t.birthRequired;
    else if (age(draft.birth_date) < 18) next.birth_date = t.underage;
    if (!draft.sex) next.sex = dict.onboarding.pickOne;
    if (!(height >= 100 && height <= 250)) next.height_cm = t.heightRange;
    if (!(weight >= 30 && weight <= 300)) next.weight_kg = t.weightRange;
    setErrors(next);
    if (Object.keys(next).length === 0) onDone();
  };

  return (
    <StepFrame title={t.title} action={dict.common.next} onSubmit={submit}>
      <Field id="birth_date" label={t.birthDate} error={errors.birth_date}>
        <Input
          id="birth_date"
          type="date"
          dir="ltr"
          value={draft.birth_date}
          max={new Date().toISOString().slice(0, 10)}
          onChange={(e) => setDraft({ birth_date: e.target.value })}
          aria-invalid={!!errors.birth_date}
        />
      </Field>
      <ChoiceGroup
        label={t.sex}
        columns={2}
        options={options<Sex>(["male", "female"], dict.options.sex)}
        value={draft.sex}
        onChange={(sex) => setDraft({ sex })}
        error={errors.sex}
      />
      <div className="grid grid-cols-2 gap-4">
        <Field id="height_cm" label={t.height} error={errors.height_cm}>
          <Input
            id="height_cm"
            inputMode="decimal"
            dir="ltr"
            value={draft.height_cm}
            onChange={(e) => setDraft({ height_cm: e.target.value.replace(",", ".") })}
            aria-invalid={!!errors.height_cm}
          />
        </Field>
        <Field id="weight_kg" label={t.weight} error={errors.weight_kg}>
          <Input
            id="weight_kg"
            inputMode="decimal"
            dir="ltr"
            value={draft.weight_kg}
            onChange={(e) => setDraft({ weight_kg: e.target.value.replace(",", ".") })}
            aria-invalid={!!errors.weight_kg}
          />
        </Field>
      </div>
    </StepFrame>
  );
}

export function GoalStep({ draft, setDraft, onDone }: StepProps) {
  const { dict } = useI18n();
  const t = dict.onboarding.goal;
  const [missing, setMissing] = useState(false);
  const submit = () => {
    const ok = draft.goal && draft.experience && draft.activity_level;
    setMissing(!ok);
    if (ok) onDone();
  };
  const err = (v: unknown) => (missing && !v ? dict.onboarding.pickOne : undefined);
  return (
    <StepFrame title={t.title} action={dict.common.next} onSubmit={submit}>
      <ChoiceGroup
        label={t.goal}
        options={options<Goal>(["lean_bulk", "recomp", "cut", "maintain"], dict.options.goal)}
        value={draft.goal}
        onChange={(goal) => setDraft({ goal })}
        error={err(draft.goal)}
      />
      <ChoiceGroup
        label={t.experience}
        options={options<Experience>(["beginner", "intermediate", "advanced"], dict.options.experience)}
        value={draft.experience}
        onChange={(experience) => setDraft({ experience })}
        error={err(draft.experience)}
      />
      <ChoiceGroup
        label={t.activity}
        options={options<Activity>(
          ["sedentary", "light", "moderate", "active", "very_active"],
          dict.options.activity,
        )}
        value={draft.activity_level}
        onChange={(activity_level) => setDraft({ activity_level })}
        error={err(draft.activity_level)}
      />
    </StepFrame>
  );
}

export function ScheduleStep({
  draft,
  setDraft,
  onSave,
}: Omit<StepProps, "onDone"> & { onSave: () => Promise<void> }) {
  const { dict } = useI18n();
  const t = dict.onboarding.schedule;
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    if (!draft.days_per_week || !draft.session_minutes || draft.equipment.length === 0) {
      setError(t.equipmentRequired);
      return;
    }
    setError(null);
    setBusy(true);
    try {
      await onSave();
    } catch (err) {
      setError(
        err instanceof ApiError && err.code === "underage"
          ? dict.onboarding.basics.underage
          : apiErrorMessage(err, dict),
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <StepFrame title={t.title} action={dict.common.next} onSubmit={submit} busy={busy} error={error}>
      <NumberChoice
        label={t.days}
        values={[2, 3, 4, 5, 6]}
        value={draft.days_per_week}
        onChange={(days_per_week) => setDraft({ days_per_week })}
      />
      <NumberChoice
        label={t.minutes}
        values={[30, 45, 60, 75, 90, 120]}
        value={draft.session_minutes}
        onChange={(session_minutes) => setDraft({ session_minutes })}
      />
      <MultiChoice
        label={t.equipment}
        options={options<Equipment>(EQUIPMENT, dict.options.equipment)}
        values={draft.equipment}
        onChange={(equipment) => setDraft({ equipment })}
      />
    </StepFrame>
  );
}
