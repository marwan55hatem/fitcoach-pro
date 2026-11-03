"use client";

import { Plus, ShieldAlert, ShieldCheck, Trash2 } from "lucide-react";
import { useState } from "react";

import { Field } from "@/components/field";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { api } from "@/lib/api/client";
import { apiErrorMessage } from "@/lib/api/errors";
import {
  BODY_AREAS,
  CONSENTS,
  SCREENING_QUESTIONS,
  type BodyArea,
  type Budget,
  type ConsentType,
  type Diet,
  type LimitationIn,
  type Screening,
  type ScreeningQuestion,
  type Severity,
  type Sex,
} from "@/lib/api/types";
import { useI18n } from "@/lib/i18n/provider";
import { cn } from "@/lib/utils";

import { ChoiceGroup, NumberChoice } from "./choices";
import { StepFrame } from "./step-frame";

/** Run a save; show a friendly error instead of throwing. */
function useSave() {
  const { dict } = useI18n();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const run = async (work: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await work();
    } catch (err) {
      setError(apiErrorMessage(err, dict));
    } finally {
      setBusy(false);
    }
  };
  return { busy, error, setError, run };
}

// --- consent ------------------------------------------------------------------------

export function ConsentStep({
  initial,
  onDone,
}: {
  initial: Record<ConsentType, boolean>;
  onDone: (granted: Record<ConsentType, boolean>) => void;
}) {
  const { dict } = useI18n();
  const t = dict.onboarding.consent;
  const [values, setValues] = useState(initial);
  const save = useSave();

  const submit = () =>
    save.run(async () => {
      if (!values.health_data) {
        save.setError(t.required);
        return;
      }
      await api("/consents", { method: "PUT", body: { consents: values } });
      onDone(values);
    });

  return (
    <StepFrame
      title={t.title}
      lead={t.lead}
      action={dict.common.next}
      onSubmit={submit}
      busy={save.busy}
      error={save.error}
    >
      {CONSENTS.map((type) => (
        <label
          key={type}
          className={cn(
            "flex cursor-pointer items-start justify-between gap-4 rounded-2xl border bg-card p-4",
            values[type] ? "border-primary/60" : "border-border",
          )}
        >
          <span className="grid gap-1">
            <span className="font-medium">{t[type].title}</span>
            <span className="text-sm text-muted-foreground">{t[type].body}</span>
          </span>
          <Switch
            checked={values[type]}
            onCheckedChange={(checked) => setValues({ ...values, [type]: checked })}
            aria-label={t[type].title}
            className="mt-1 shrink-0"
          />
        </label>
      ))}
    </StepFrame>
  );
}

// --- injuries -----------------------------------------------------------------------

export function InjuriesStep({
  initial,
  onDone,
}: {
  initial: LimitationIn[];
  onDone: () => void;
}) {
  const { dict } = useI18n();
  const t = dict.onboarding.injuries;
  const [items, setItems] = useState<LimitationIn[]>(initial);
  const [none, setNone] = useState(initial.length === 0);
  const save = useSave();

  const update = (index: number, change: Partial<LimitationIn>) =>
    setItems(items.map((item, i) => (i === index ? { ...item, ...change } : item)));
  const add = () => {
    setNone(false);
    setItems([...items, { body_area: "knee", severity: "mild", description: null, active: true }]);
  };

  const submit = () =>
    save.run(async () => {
      const limitations = none ? [] : items;
      await api("/onboarding/limitations", { method: "PUT", body: { limitations } });
      onDone();
    });

  return (
    <StepFrame
      title={t.title}
      lead={t.lead}
      action={dict.common.next}
      onSubmit={submit}
      busy={save.busy}
      error={save.error}
    >
      <label className="flex min-h-12 cursor-pointer items-center gap-3 rounded-xl border border-border bg-card px-4">
        <Checkbox
          checked={none}
          onCheckedChange={(v) => {
            setNone(v === true);
            if (v === true) setItems([]);
          }}
          aria-label={t.none}
          className="size-5"
        />
        <span>{t.none}</span>
      </label>

      {!none &&
        items.map((item, index) => (
          <fieldset key={index} className="grid gap-4 rounded-2xl border border-border bg-card p-4">
            <legend className="sr-only">{dict.options.bodyArea[item.body_area]}</legend>
            <ChoiceGroup
              label={t.area}
              columns={2}
              options={BODY_AREAS.map((value) => ({ value, label: dict.options.bodyArea[value] }))}
              value={item.body_area}
              onChange={(body_area: BodyArea) => update(index, { body_area })}
            />
            <ChoiceGroup
              label={t.severity}
              columns={3}
              options={(["mild", "moderate", "severe"] as Severity[]).map((value) => ({
                value,
                label: dict.options.severity[value],
              }))}
              value={item.severity}
              onChange={(severity: Severity) => update(index, { severity })}
            />
            <Field id={`injury-notes-${index}`} label={t.notes}>
              <Input
                id={`injury-notes-${index}`}
                maxLength={500}
                value={item.description ?? ""}
                onChange={(e) => update(index, { description: e.target.value || null })}
              />
            </Field>
            <Button
              type="button"
              variant="ghost"
              className="w-fit text-muted-foreground"
              onClick={() => setItems(items.filter((_, i) => i !== index))}
            >
              <Trash2 aria-hidden />
              {t.remove}
            </Button>
          </fieldset>
        ))}

      <Button type="button" variant="outline" onClick={add} className="w-fit">
        <Plus aria-hidden />
        {t.add}
      </Button>
    </StepFrame>
  );
}

// --- food ---------------------------------------------------------------------------

const toList = (text: string) =>
  text
    .split(/[,،]/)
    .map((s) => s.trim())
    .filter(Boolean)
    .slice(0, 30);

export function FoodStep({ initial, onDone }: { initial: Diet | null; onDone: () => void }) {
  const { dict } = useI18n();
  const t = dict.onboarding.food;
  const [meals, setMeals] = useState<number | null>(initial?.meals_per_day ?? null);
  const [budget, setBudget] = useState<Budget | null>(initial?.budget_level ?? null);
  const [text, setText] = useState({
    allergies: initial?.allergies.join(", ") ?? "",
    intolerances: initial?.intolerances.join(", ") ?? "",
    dislikes: initial?.dislikes.join(", ") ?? "",
    cuisine_hints: initial?.cuisine_hints.join(", ") ?? "",
  });
  const save = useSave();

  const submit = () =>
    save.run(async () => {
      if (!meals) {
        save.setError(`${t.meals}: ${dict.onboarding.pickOne}`);
        return;
      }
      const body: Diet = {
        meals_per_day: meals,
        budget_level: budget,
        allergies: toList(text.allergies),
        intolerances: toList(text.intolerances),
        dislikes: toList(text.dislikes),
        cuisine_hints: toList(text.cuisine_hints),
      };
      await api("/onboarding/diet", { method: "PUT", body });
      onDone();
    });

  const listField = (key: keyof typeof text, label: string) => (
    <Field id={key} label={label} hint={t.listHint}>
      <Input
        id={key}
        value={text[key]}
        onChange={(e) => setText({ ...text, [key]: e.target.value })}
      />
    </Field>
  );

  return (
    <StepFrame title={t.title} action={dict.common.next} onSubmit={submit} busy={save.busy} error={save.error}>
      <NumberChoice label={t.meals} values={[2, 3, 4, 5, 6]} value={meals} onChange={setMeals} />
      <ChoiceGroup
        label={t.budget}
        columns={3}
        options={(["low", "medium", "high"] as Budget[]).map((value) => ({
          value,
          label: dict.options.budget[value],
        }))}
        value={budget}
        onChange={setBudget}
      />
      {listField("allergies", t.allergies)}
      {listField("intolerances", t.intolerances)}
      {listField("dislikes", t.dislikes)}
      {listField("cuisine_hints", t.cuisine)}
    </StepFrame>
  );
}

// --- health screening ---------------------------------------------------------------

type Answers = Record<ScreeningQuestion, boolean | null>;

export function ScreeningStep({
  sex,
  current,
  onScreened,
  onDone,
}: {
  sex: Sex | null;
  current: Screening | null;
  onScreened: (screening: Screening) => void;
  onDone: () => void;
}) {
  const { dict } = useI18n();
  const t = dict.onboarding.screening;
  const questions = SCREENING_QUESTIONS.filter((q) => q !== "pregnant" || sex !== "male");
  const [answers, setAnswers] = useState<Answers>(
    () => Object.fromEntries(SCREENING_QUESTIONS.map((q) => [q, null])) as Answers,
  );
  const [freeText, setFreeText] = useState("");
  const [result, setResult] = useState<Screening | null>(current);
  const [editing, setEditing] = useState(current === null);
  const [cleared, setCleared] = useState(false);
  const save = useSave();

  const submitAnswers = () =>
    save.run(async () => {
      if (questions.some((q) => answers[q] === null)) {
        save.setError(dict.onboarding.pickOne);
        return;
      }
      const body = {
        ...Object.fromEntries(SCREENING_QUESTIONS.map((q) => [q, answers[q] === true])),
        free_text: freeText.trim() || null,
      };
      const screening = await api<Screening>("/onboarding/screening", { method: "POST", body });
      setResult(screening);
      setEditing(false);
      onScreened(screening);
    });

  const confirmClearance = () =>
    save.run(async () => {
      if (!cleared) {
        save.setError(t.clearanceTick);
        return;
      }
      const screening = await api<Screening>("/onboarding/clearance", {
        method: "POST",
        body: { confirm: true },
      });
      onScreened(screening);
      onDone();
    });

  if (!editing && result) {
    const needsClearance = result.clearance_required && !result.clearance_confirmed;
    const reasonText = (code: string, fallback: string) =>
      (dict.reasons as Record<string, string>)[code] ?? fallback;
    return (
      <StepFrame
        title={
          result.risk_level === "ok"
            ? t.okTitle
            : result.risk_level === "blocked"
              ? t.blockedTitle
              : t.cautionTitle
        }
        action={needsClearance ? t.clearanceButton : dict.common.next}
        onSubmit={needsClearance ? confirmClearance : onDone}
        busy={save.busy}
        error={save.error}
        secondary={
          <Button type="button" variant="ghost" onClick={() => setEditing(true)}>
            {t.edit}
          </Button>
        }
      >
        <div
          className={cn(
            "flex gap-3 rounded-2xl border p-4",
            result.risk_level === "ok" ? "border-success/40 bg-success/5" : "border-gold/40 bg-gold/5",
          )}
        >
          {result.risk_level === "ok" ? (
            <ShieldCheck className="size-6 shrink-0 text-success" aria-hidden />
          ) : (
            <ShieldAlert className="size-6 shrink-0 text-gold" aria-hidden />
          )}
          <div className="grid gap-2">
            {result.risk_level === "ok" ? (
              <p>{t.okBody}</p>
            ) : (
              <>
                <p>{t.reasonsLead}</p>
                <ul className="list-disc space-y-1 ps-5">
                  {result.reasons.map((r) => (
                    <li key={r.code}>{reasonText(r.code, r.message)}</li>
                  ))}
                </ul>
              </>
            )}
            {result.notes.map((n) => (
              <p key={n.code} className="text-sm text-muted-foreground">
                {reasonText(n.code, n.message)}
              </p>
            ))}
            {result.disordered_eating_flag && (
              <p className="text-sm text-muted-foreground">{t.eatingNote}</p>
            )}
          </div>
        </div>
        {needsClearance && (
          <div className="grid gap-3">
            <p className="text-sm text-muted-foreground">{t.clearanceBody}</p>
            <label className="flex min-h-12 cursor-pointer items-center gap-3 rounded-xl border border-border bg-card px-4">
              <Checkbox
                checked={cleared}
                onCheckedChange={(v) => setCleared(v === true)}
                aria-label={t.clearanceConfirm}
                className="size-5"
              />
              <span>{t.clearanceConfirm}</span>
            </label>
          </div>
        )}
      </StepFrame>
    );
  }

  return (
    <StepFrame
      title={t.title}
      lead={t.lead}
      action={t.submit}
      onSubmit={submitAnswers}
      busy={save.busy}
      error={save.error}
    >
      {questions.map((q) => (
        <ChoiceGroup
          key={q}
          label={dict.screeningQuestions[q]}
          columns={2}
          options={[
            { value: "no", label: dict.common.no },
            { value: "yes", label: dict.common.yes },
          ]}
          value={answers[q] === null ? null : answers[q] ? "yes" : "no"}
          onChange={(v) => setAnswers({ ...answers, [q]: v === "yes" })}
        />
      ))}
      <Field id="free_text" label={t.freeText}>
        <Textarea
          id="free_text"
          maxLength={1000}
          value={freeText}
          onChange={(e) => setFreeText(e.target.value)}
        />
      </Field>
    </StepFrame>
  );
}
