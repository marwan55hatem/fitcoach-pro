"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { Camera, Loader2 } from "lucide-react";
import { useId, useState } from "react";

import { FormError } from "@/components/field";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { api, ApiError, uploadFile } from "@/lib/api/client";
import { apiErrorMessage } from "@/lib/api/errors";
import type { FoodDraft, Meal, MealSlot, PersonalFood } from "@/lib/api/types";
import { useConsents } from "@/lib/consents";
import { fill, useI18n } from "@/lib/i18n/provider";
import { cn } from "@/lib/utils";

type Mode = "photo" | "text" | "mine";

function useEstimateError() {
  const { dict } = useI18n();
  const t = dict.food;
  return (error: unknown): { message: string; questions: string[] } => {
    if (error instanceof ApiError) {
      if (error.code === "no_food") return { message: t.noFood, questions: [] };
      if (error.code === "unclear") {
        const detail = error.detail as { questions?: string[] };
        return { message: t.unclear, questions: detail.questions ?? [] };
      }
      if (error.code === "too_large") return { message: t.tooLarge, questions: [] };
      if (error.code === "consent_required") return { message: t.aiOff, questions: [] };
    }
    return { message: apiErrorMessage(error, dict), questions: [] };
  };
}

/** Add to a meal slot: a photo (opens the phone camera), a description, or a saved food. */
export function AddMeal({
  date,
  slot,
  onDraft,
  onLogged,
}: {
  date: string;
  slot: MealSlot;
  onDraft: (draft: FoodDraft) => void;
  onLogged: (meal: Meal) => void;
}) {
  const { dict } = useI18n();
  const t = dict.food;
  const id = useId();
  const consents = useConsents();
  const aiOn = consents.data?.ai_processing ?? true;
  const [chosen, setMode] = useState<Mode | null>(null);
  // Until the user picks a tab: a photo, or saved foods when AI coaching is off.
  const mode: Mode = chosen ?? (aiOn ? "photo" : "mine");
  const [photo, setPhoto] = useState<File | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [text, setText] = useState("");
  const explain = useEstimateError();

  const estimate = useMutation({
    mutationFn: async () => {
      const file = mode === "photo" && photo ? await uploadFile("food", photo) : null;
      const note = text.trim() || undefined;
      return api<FoodDraft>("/food/estimate", {
        method: "POST",
        body: { file_id: file?.id, text: note, date, slot },
      });
    },
    onSuccess: onDraft,
  });
  const personal = useQuery({
    queryKey: ["personal-foods"],
    queryFn: () => api<PersonalFood[]>("/food/personal"),
    enabled: mode === "mine",
  });
  const logPersonal = useMutation({
    mutationFn: (food: PersonalFood) =>
      api<Meal>("/food/logs", {
        method: "POST",
        body: {
          date,
          slot,
          name: food.name,
          items: food.items,
          kcal: food.kcal,
          protein_g: food.protein_g,
          carbs_g: food.carbs_g,
          fat_g: food.fat_g,
        },
      }),
    onSuccess: onLogged,
  });

  const choose = (file: File | null) => {
    if (preview) URL.revokeObjectURL(preview);
    setPhoto(file);
    setPreview(file ? URL.createObjectURL(file) : null);
    estimate.reset();
  };
  const problem = estimate.isError ? explain(estimate.error) : null;
  const tabs: { key: Mode; label: string }[] = [
    { key: "photo", label: t.byPhoto },
    { key: "text", label: t.byText },
    { key: "mine", label: t.myFoods },
  ];

  return (
    <div className="grid gap-4 rounded-2xl border border-border bg-background p-4">
      <div role="tablist" aria-label={fill(t.addTo, { slot: t.slots[slot] })} className="grid grid-cols-3 gap-1 rounded-xl bg-muted p-1">
        {tabs.map((tab) => (
          <button
            key={tab.key}
            type="button"
            role="tab"
            aria-selected={mode === tab.key}
            onClick={() => {
              setMode(tab.key);
              estimate.reset();
            }}
            className={cn(
              "min-h-11 rounded-lg text-sm font-medium transition-colors focus-visible:outline-2 focus-visible:outline-ring",
              mode === tab.key ? "bg-card text-foreground shadow-sm" : "text-muted-foreground",
            )}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {mode !== "mine" && !aiOn && <FormError message={t.aiOff} />}

      {mode === "photo" && aiOn && (
        <div className="grid gap-3">
          <label
            htmlFor={`${id}-photo`}
            className="flex min-h-28 cursor-pointer items-center justify-center gap-3 overflow-hidden rounded-xl border border-dashed border-input bg-card text-muted-foreground hover:text-foreground focus-within:outline-2 focus-within:outline-ring"
          >
            {preview ? (
              // eslint-disable-next-line @next/next/no-img-element -- a local preview of the chosen file
              <img src={preview} alt="" className="max-h-56 w-full object-cover" />
            ) : (
              <>
                <Camera className="size-6" aria-hidden />
                <span>{t.choosePhoto}</span>
              </>
            )}
          </label>
          <input
            id={`${id}-photo`}
            type="file"
            accept="image/*"
            capture="environment"
            className="sr-only"
            aria-label={photo ? t.changePhoto : t.choosePhoto}
            onChange={(e) => choose(e.target.files?.[0] ?? null)}
          />
          <div className="grid gap-2">
            <Label htmlFor={`${id}-note`}>{t.note}</Label>
            <Textarea
              id={`${id}-note`}
              dir="auto"
              maxLength={1000}
              placeholder={t.notePlaceholder}
              value={text}
              onChange={(e) => setText(e.target.value)}
            />
          </div>
        </div>
      )}

      {mode === "text" && aiOn && (
        <div className="grid gap-2">
          <Label htmlFor={`${id}-text`}>{t.describe}</Label>
          <Textarea
            id={`${id}-text`}
            dir="auto"
            maxLength={1000}
            placeholder={t.describePlaceholder}
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
        </div>
      )}

      {mode !== "mine" && aiOn && (
        <>
          {problem && (
            <div className="grid gap-1" role="alert">
              <FormError message={problem.message} />
              {problem.questions.length > 0 && (
                <ul className="list-disc ps-6 text-sm text-muted-foreground" dir="auto">
                  {problem.questions.map((q) => (
                    <li key={q}>{q}</li>
                  ))}
                </ul>
              )}
            </div>
          )}
          <Button
            type="button"
            className="w-fit"
            disabled={estimate.isPending || (mode === "photo" ? !photo : !text.trim())}
            onClick={() => estimate.mutate()}
          >
            {estimate.isPending && <Loader2 className="size-4 animate-spin" aria-hidden />}
            {estimate.isPending ? t.estimating : t.estimate}
          </Button>
        </>
      )}

      {mode === "mine" && (
        <div className="grid gap-2">
          {personal.isPending && <p className="text-sm text-muted-foreground">{dict.common.loading}</p>}
          {personal.isError && <FormError message={dict.common.errorGeneric} />}
          {personal.data?.length === 0 && <p className="text-sm text-muted-foreground">{t.noPersonal}</p>}
          <ul className="grid gap-2">
            {personal.data?.map((food) => (
              <li key={food.id}>
                <Button
                  type="button"
                  variant="outline"
                  className="h-auto min-h-12 w-full justify-between whitespace-normal py-2 text-start"
                  aria-label={fill(t.logPersonal, { name: food.name })}
                  disabled={logPersonal.isPending}
                  onClick={() => logPersonal.mutate(food)}
                >
                  <span dir="auto">{food.name}</span>
                  <span className="shrink-0 font-heading tabular-nums text-gold">
                    {fill(t.kcalExact, { kcal: food.kcal })}
                  </span>
                </Button>
              </li>
            ))}
          </ul>
          <FormError message={logPersonal.isError ? apiErrorMessage(logPersonal.error, dict) : null} />
        </div>
      )}
    </div>
  );
}
