"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { Controller, useForm } from "react-hook-form";
import { z } from "zod";

import { Field, FormError } from "@/components/field";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { authErrorMessage } from "@/lib/api/errors";
import { useAuth } from "@/lib/auth/auth-provider";
import { useI18n } from "@/lib/i18n/provider";
import { useLocalePath } from "@/lib/i18n/use-locale-path";

type Mode = "login" | "register";

function schemaFor(mode: Mode, dict: ReturnType<typeof useI18n>["dict"]) {
  return z
    .object({
      email: z.email(dict.auth.invalidEmail),
      password: z.string().max(128),
      displayName: z.string().trim().max(100),
      adult: z.boolean(),
    })
    .superRefine((v, ctx) => {
      const minimum = mode === "register" ? 10 : 1;
      if (v.password.length < minimum) {
        ctx.addIssue({ code: "custom", path: ["password"], message: dict.auth.passwordShort });
      }
      if (mode === "register" && !v.adult) {
        ctx.addIssue({ code: "custom", path: ["adult"], message: dict.auth.adultRequired });
      }
    });
}

type Values = z.infer<ReturnType<typeof schemaFor>>;

export function AuthForm({ mode }: { mode: Mode }) {
  const { dict } = useI18n();
  const auth = useAuth();
  const router = useRouter();
  const to = useLocalePath();
  const [error, setError] = useState<string | null>(null);
  // Set while this form logs in, so the "already logged in" redirect does not race it.
  const [submitted, setSubmitted] = useState(false);
  const {
    register,
    control,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<Values>({
    resolver: zodResolver(schemaFor(mode, dict)),
    defaultValues: { email: "", password: "", displayName: "", adult: false },
  });

  // Someone already logged in has nothing to do here.
  useEffect(() => {
    if (auth.status === "authenticated" && !submitted) router.replace(to("/today"));
  }, [auth.status, submitted, router, to]);

  const onSubmit = handleSubmit(async (values) => {
    setError(null);
    setSubmitted(true);
    try {
      if (mode === "login") await auth.login(values.email, values.password);
      else await auth.register(values.email, values.password, values.displayName);
      router.replace(to(mode === "login" ? "/today" : "/onboarding"));
    } catch (err) {
      setSubmitted(false);
      setError(authErrorMessage(err, dict));
    }
  });

  const t = dict.auth;
  return (
    <form onSubmit={onSubmit} noValidate className="grid gap-5">
      <div className="grid gap-1">
        <h1 className="text-4xl font-bold">{mode === "login" ? t.loginTitle : t.registerTitle}</h1>
        <p className="text-muted-foreground">{mode === "login" ? t.loginLead : t.registerLead}</p>
      </div>

      {mode === "register" && (
        <Field id="displayName" label={t.displayName} hint={t.displayNameHint}>
          <Input id="displayName" autoComplete="given-name" {...register("displayName")} />
        </Field>
      )}
      <Field id="email" label={t.email} error={errors.email?.message}>
        <Input
          id="email"
          type="email"
          inputMode="email"
          autoComplete="email"
          dir="ltr"
          aria-invalid={!!errors.email}
          {...register("email")}
        />
      </Field>
      <Field
        id="password"
        label={t.password}
        hint={mode === "register" ? t.passwordHint : undefined}
        error={errors.password?.message}
      >
        <Input
          id="password"
          type="password"
          dir="ltr"
          autoComplete={mode === "login" ? "current-password" : "new-password"}
          aria-invalid={!!errors.password}
          {...register("password")}
        />
      </Field>

      {mode === "register" && (
        <div className="grid gap-3 rounded-xl border border-gold/30 bg-gold/5 p-4">
          <p className="font-medium text-gold">{t.adultTitle}</p>
          <p className="text-sm text-muted-foreground">{t.adultBody}</p>
          <Controller
            control={control}
            name="adult"
            render={({ field }) => (
              <label className="flex min-h-11 cursor-pointer items-center gap-3">
                <Checkbox
                  checked={field.value}
                  onCheckedChange={(v) => field.onChange(v === true)}
                  aria-invalid={!!errors.adult}
                  aria-label={t.adultConfirm}
                  className="size-5"
                />
                <span>{t.adultConfirm}</span>
              </label>
            )}
          />
          {errors.adult && (
            <p role="alert" className="text-sm text-destructive">
              {errors.adult.message}
            </p>
          )}
        </div>
      )}

      <FormError message={error} />
      <Button type="submit" size="lg" disabled={isSubmitting} className="w-full">
        {isSubmitting ? dict.common.loading : mode === "login" ? t.login : t.register}
      </Button>
      <p className="text-center text-sm text-muted-foreground">
        {mode === "login" ? t.noAccount : t.haveAccount}{" "}
        <Link
          href={to(mode === "login" ? "/register" : "/login")}
          className="font-medium text-primary underline-offset-4 hover:underline"
        >
          {mode === "login" ? t.toRegister : t.toLogin}
        </Link>
      </p>
    </form>
  );
}
