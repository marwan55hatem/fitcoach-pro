"use client";

import type { Dictionary } from "@/lib/i18n/en";

import { ApiError } from "./client";

/** A sentence the user can act on, in their language, for an auth or API failure. */
export function authErrorMessage(error: unknown, dict: Dictionary): string {
  if (error instanceof ApiError) {
    if (error.status === 401) return dict.auth.invalidCredentials;
    if (error.status === 409) return dict.auth.emailTaken;
    if (error.status === 429) return dict.auth.tooMany;
    if (error.status === 503) return dict.common.busy;
  }
  return dict.common.errorGeneric;
}

export function apiErrorMessage(error: unknown, dict: Dictionary): string {
  if (error instanceof ApiError) {
    if (error.status === 429) return dict.auth.tooMany;
    if (error.status === 503) return dict.common.busy;
  }
  return dict.common.errorGeneric;
}
