"use client";

import { useCallback } from "react";

import { useI18n } from "./provider";

/** Build an app path in the current language: to("/today") -> "/ar/today". */
export function useLocalePath(): (path: string) => string {
  const { locale } = useI18n();
  return useCallback((path: string) => `/${locale}${path}`, [locale]);
}
