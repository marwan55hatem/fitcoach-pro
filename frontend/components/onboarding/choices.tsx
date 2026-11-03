"use client";

import { Check } from "lucide-react";
import { useId, type KeyboardEvent } from "react";

import { cn } from "@/lib/utils";

const chip =
  "flex min-h-12 items-center justify-between gap-3 rounded-xl border px-4 py-2 text-start transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring";
const on = "border-primary bg-primary/10 text-foreground";
const off = "border-border bg-card text-muted-foreground hover:border-muted-foreground/50 hover:text-foreground";

type Option<T extends string> = { value: T; label: string };

/** One answer from a short list, as large tappable cards (a radio group). */
export function ChoiceGroup<T extends string>({
  label,
  options,
  value,
  onChange,
  columns = 1,
  error,
}: {
  label: string;
  options: Option<T>[];
  value: T | null;
  onChange: (value: T) => void;
  columns?: 1 | 2 | 3;
  error?: string;
}) {
  const id = useId();
  const move = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    const forward = ["ArrowDown", "ArrowRight"].includes(event.key);
    const backward = ["ArrowUp", "ArrowLeft"].includes(event.key);
    if (!forward && !backward) return;
    event.preventDefault();
    const next = (index + (forward ? 1 : -1) + options.length) % options.length;
    onChange(options[next].value);
    const group = event.currentTarget.parentElement;
    (group?.children[next] as HTMLElement | undefined)?.focus();
  };
  return (
    <div className="grid gap-2">
      <p id={id} className="text-sm font-medium">
        {label}
      </p>
      <div
        role="radiogroup"
        aria-labelledby={id}
        aria-invalid={!!error}
        className={cn("grid gap-2", columns === 2 && "grid-cols-2", columns === 3 && "grid-cols-3")}
      >
        {options.map((option, index) => {
          const checked = option.value === value;
          return (
            <button
              key={option.value}
              type="button"
              role="radio"
              aria-checked={checked}
              tabIndex={checked || (value === null && index === 0) ? 0 : -1}
              onClick={() => onChange(option.value)}
              onKeyDown={(e) => move(e, index)}
              className={cn(chip, checked ? on : off)}
            >
              <span>{option.label}</span>
              {checked && <Check className="size-4 shrink-0 text-primary" aria-hidden />}
            </button>
          );
        })}
      </div>
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
    </div>
  );
}

/** Several answers from a list, as toggle buttons. */
export function MultiChoice<T extends string>({
  label,
  options,
  values,
  onChange,
  error,
}: {
  label: string;
  options: Option<T>[];
  values: T[];
  onChange: (values: T[]) => void;
  error?: string;
}) {
  const id = useId();
  const toggle = (v: T) =>
    onChange(values.includes(v) ? values.filter((x) => x !== v) : [...values, v]);
  return (
    <div className="grid gap-2" role="group" aria-labelledby={id}>
      <p id={id} className="text-sm font-medium">
        {label}
      </p>
      <div className="grid grid-cols-2 gap-2">
        {options.map((option) => {
          const pressed = values.includes(option.value);
          return (
            <button
              key={option.value}
              type="button"
              aria-pressed={pressed}
              onClick={() => toggle(option.value)}
              className={cn(chip, pressed ? on : off)}
            >
              <span>{option.label}</span>
              {pressed && <Check className="size-4 shrink-0 text-primary" aria-hidden />}
            </button>
          );
        })}
      </div>
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
    </div>
  );
}

/** A number picked by tapping, for small ranges like days per week. */
export function NumberChoice({
  label,
  values,
  value,
  onChange,
  format = String,
}: {
  label: string;
  values: number[];
  value: number | null;
  onChange: (value: number) => void;
  format?: (n: number) => string;
}) {
  return (
    <ChoiceGroup
      label={label}
      columns={values.length > 4 ? 3 : 2}
      options={values.map((n) => ({ value: String(n), label: format(n) }))}
      value={value === null ? null : String(value)}
      onChange={(v) => onChange(Number(v))}
    />
  );
}
