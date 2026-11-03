"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Direction } from "radix-ui";
import { useState, type ReactNode } from "react";

import { Toaster } from "@/components/ui/sonner";
import { ApiError } from "@/lib/api/client";
import { AuthProvider } from "@/lib/auth/auth-provider";
import { dirOf, type Locale } from "@/lib/i18n/config";
import type { Dictionary } from "@/lib/i18n/en";
import { I18nProvider } from "@/lib/i18n/provider";

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 30_000,
        // Client errors (403 consent, 404, 422) will not fix themselves on retry.
        retry: (count, error) =>
          count < 2 && !(error instanceof ApiError && error.status < 500),
      },
    },
  });
}

export function Providers({
  locale,
  dict,
  children,
}: {
  locale: Locale;
  dict: Dictionary;
  children: ReactNode;
}) {
  const [queryClient] = useState(makeQueryClient);
  const dir = dirOf(locale);
  return (
    <QueryClientProvider client={queryClient}>
      <I18nProvider locale={locale} dict={dict}>
        <Direction.Provider dir={dir}>
          <AuthProvider>{children}</AuthProvider>
          <Toaster position="top-center" dir={dir} theme="dark" />
        </Direction.Provider>
      </I18nProvider>
    </QueryClientProvider>
  );
}
