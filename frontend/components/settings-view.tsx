"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Field, FormError } from "@/components/field";
import { LanguageSwitch } from "@/components/language-switch";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { api, ApiError } from "@/lib/api/client";
import { apiErrorMessage } from "@/lib/api/errors";
import type { ConsentType } from "@/lib/api/types";
import { useAuth } from "@/lib/auth/auth-provider";
import { CONSENTS_KEY, useConsents } from "@/lib/consents";
import { useI18n } from "@/lib/i18n/provider";
import { useLocalePath } from "@/lib/i18n/use-locale-path";

// Health data is required to use the app at all; the others can change any time.
const OPTIONAL: ConsentType[] = ["ai_processing", "body_photos", "notifications"];

function Privacy() {
  const { dict } = useI18n();
  const t = dict.onboarding.consent;
  const client = useQueryClient();
  const consents = useConsents();
  const change = useMutation({
    mutationFn: ({ type, granted }: { type: ConsentType; granted: boolean }) =>
      api("/consents", { method: "PUT", body: { consents: { [type]: granted } } }),
    onSettled: () => void client.invalidateQueries({ queryKey: CONSENTS_KEY }),
  });
  return (
    <div className="grid gap-3">
      <h2 className="text-xl font-semibold">{dict.settings.privacy}</h2>
      <p className="text-sm text-muted-foreground">{dict.settings.privacyLead}</p>
      {OPTIONAL.map((type) => (
        <label key={type} className="flex items-start justify-between gap-4 rounded-2xl border border-border bg-card p-4">
          <span className="grid gap-1">
            <span className="font-medium">{t[type].title}</span>
            <span className="text-sm text-muted-foreground">{t[type].body}</span>
          </span>
          <Switch
            checked={consents.data?.[type] ?? false}
            disabled={!consents.data || change.isPending}
            onCheckedChange={(granted) => change.mutate({ type, granted })}
            aria-label={t[type].title}
            className="mt-1 shrink-0"
          />
        </label>
      ))}
      <FormError message={change.isError || consents.isError ? dict.common.errorGeneric : null} />
    </div>
  );
}

export function SettingsView() {
  const { dict } = useI18n();
  const auth = useAuth();
  const router = useRouter();
  const to = useLocalePath();
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const t = dict.settings;

  async function logout() {
    await auth.logout();
    router.replace(to("/login"));
  }

  async function deleteAccount(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await api("/me", { method: "DELETE", body: { password } });
      await auth.forget();
      router.replace(to("/login"));
    } catch (err) {
      setError(err instanceof ApiError && err.status === 401 ? t.wrongPassword : apiErrorMessage(err, dict));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="grid max-w-xl gap-8">
      <h1 className="text-4xl font-bold">{t.title}</h1>

      <div className="grid gap-2">
        <h2 className="text-xl font-semibold">{t.language}</h2>
        <LanguageSwitch className="w-fit border border-border" />
      </div>

      <Privacy />

      <div className="grid gap-2">
        <h2 className="text-xl font-semibold">{t.account}</h2>
        <p className="text-sm text-muted-foreground" dir="ltr">
          {auth.user?.email}
        </p>
        <Button variant="outline" className="w-fit" onClick={logout}>
          {t.logout}
        </Button>
      </div>

      <form onSubmit={deleteAccount} className="grid gap-4 rounded-2xl border border-destructive/40 p-5">
        <h2 className="text-xl font-semibold text-destructive">{t.deleteTitle}</h2>
        <p className="text-sm text-muted-foreground">{t.deleteBody}</p>
        <Field id="delete-password" label={t.deletePassword}>
          <Input
            id="delete-password"
            type="password"
            dir="ltr"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </Field>
        <FormError message={error} />
        <Button type="submit" variant="destructive" className="w-fit" disabled={!password || busy}>
          {t.deleteButton}
        </Button>
      </form>
    </section>
  );
}
