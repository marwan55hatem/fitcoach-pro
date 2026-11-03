"use client";

import { createContext, useContext, type ReactNode } from "react";

import type { Locale } from "./config";
import type { Dictionary } from "./en";

type I18n = { locale: Locale; dict: Dictionary };

const I18nContext = createContext<I18n | null>(null);

export function I18nProvider({
  locale,
  dict,
  children,
}: I18n & { children: ReactNode }) {
  return <I18nContext value={{ locale, dict }}>{children}</I18nContext>;
}

export function useI18n(): I18n {
  const value = useContext(I18nContext);
  if (!value) throw new Error("useI18n must be used inside I18nProvider");
  return value;
}

/** Fill {placeholders}: fill("Step {n}", { n: 2 }) -> "Step 2". */
export function fill(text: string, values: Record<string, string | number>): string {
  return text.replace(/\{(\w+)\}/g, (match, key: string) =>
    key in values ? String(values[key]) : match,
  );
}
