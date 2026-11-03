"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { Field, FormError } from "@/components/field";
import { LanguageSwitch } from "@/components/language-switch";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api, ApiError } from "@/lib/api/client";
import { apiErrorMessage } from "@/lib/api/errors";
import { useAuth } from "@/lib/auth/auth-provider";
import { useI18n } from "@/lib/i18n/provider";
import { useLocalePath } from "@/lib/i18n/use-locale-path";

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
