"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, BellRing, Send, Trash2 } from "lucide-react";
import Link from "next/link";
import { useEffect, useId, useState, type ReactNode } from "react";
import { toast } from "sonner";

import { FormError } from "@/components/field";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { api, ApiError } from "@/lib/api/client";
import type {
  ChannelType,
  NotificationPrefs,
  NotificationSettings,
  ReminderSetting,
  ReminderType,
} from "@/lib/api/types";
import { CONSENTS_KEY } from "@/lib/consents";
import { fill, useI18n } from "@/lib/i18n/provider";
import { useLocalePath } from "@/lib/i18n/use-locale-path";
import { cn } from "@/lib/utils";

const KEY = ["notifications", "settings"] as const;
const WEEKLY: ReminderType[] = ["weigh_in", "progress_photo", "checkin"];
const NO_TIME: ReminderType[] = ["meals", "water", "review_ready"];

type PushState = "unsupported" | "installed-only" | "blocked" | "off" | "on";

/** A browser push key ("base64url") as the bytes PushManager wants. */
function keyBytes(base64: string): Uint8Array<ArrayBuffer> {
  const padded = (base64 + "=".repeat((4 - (base64.length % 4)) % 4)).replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(padded);
  const out = new Uint8Array(new ArrayBuffer(raw.length));
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return out;
}

async function pushState(): Promise<PushState> {
  if (typeof window === "undefined" || !("Notification" in window) || !("serviceWorker" in navigator)) {
    return "unsupported";
  }
  if (Notification.permission === "denied") return "blocked";
  const reg = await navigator.serviceWorker.getRegistration();
  if (!reg || !("pushManager" in reg)) return "installed-only"; // the worker runs in production builds
  return (await reg.pushManager.getSubscription()) ? "on" : "off";
}

function deviceName(): string {
  const ua = navigator.userAgent;
  if (/iPhone|iPad/.test(ua)) return "iPhone";
  if (/Android/.test(ua)) return "Android";
  if (/Windows/.test(ua)) return "Windows";
  if (/Mac/.test(ua)) return "Mac";
  return "Browser";
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="grid gap-3">
      <h2 className="text-xl font-semibold">{title}</h2>
      {children}
    </section>
  );
}

/** Reminder settings, devices (this browser, Telegram) and a test button. */
export function NotificationsView() {
  const { dict, locale } = useI18n();
  const t = dict.notifications;
  const to = useLocalePath();
  const client = useQueryClient();
  const [linking, setLinking] = useState<{ code: string; link: string | null } | null>(null);
  const [push, setPush] = useState<PushState | null>(null);
  const [error, setError] = useState<string | null>(null);

  const settings = useQuery({
    queryKey: KEY,
    queryFn: () => api<NotificationSettings>("/notifications/settings"),
    // While a Telegram code is out, look for the new chat every few seconds.
    refetchInterval: (query) =>
      linking && !query.state.data?.channels.some((c) => c.type === "telegram") ? 3000 : false,
  });
  const data = settings.data;
  const hasTelegram = data?.channels.some((c) => c.type === "telegram") ?? false;

  useEffect(() => {
    void pushState().then(setPush);
  }, []);

  const refresh = () => void client.invalidateQueries({ queryKey: KEY });
  const failed = () => setError(dict.common.errorGeneric);

  const consent = useMutation({
    mutationFn: () => api("/consents", { method: "PUT", body: { consents: { notifications: true } } }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: CONSENTS_KEY });
      refresh();
    },
    onError: failed,
  });
  const reminder = useMutation({
    mutationFn: ({ type, body }: { type: ReminderType; body: Record<string, unknown> }) =>
      api<ReminderSetting>(`/notifications/reminders/${type}`, { method: "PUT", body }),
    onSuccess: (row) => {
      client.setQueryData<NotificationSettings>(KEY, (old) =>
        old ? { ...old, reminders: old.reminders.map((r) => (r.type === row.type ? row : r)) } : old,
      );
    },
    onError: failed,
  });
  const prefs = useMutation({
    mutationFn: (body: NotificationPrefs) => api<NotificationPrefs>("/notifications/prefs", { method: "PUT", body }),
    onSuccess: (saved) => client.setQueryData<NotificationSettings>(KEY, (old) => (old ? { ...old, prefs: saved } : old)),
    onError: failed,
  });
  const removeDevice = useMutation({
    mutationFn: (id: string) => api(`/notifications/channels/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      refresh();
      void pushState().then(setPush);
    },
    onError: failed,
  });
  const link = useMutation({
    mutationFn: () => api<{ code: string; link: string | null }>("/notifications/telegram/link-code", { method: "POST" }),
    onSuccess: setLinking,
    onError: (err) => setError(err instanceof ApiError && err.code === "telegram_not_configured" ? t.telegramOff : dict.common.errorGeneric),
  });
  const test = useMutation({
    mutationFn: () => api<{ devices: number }>("/notifications/test", { method: "POST" }),
    onSuccess: ({ devices }) => (devices ? toast.success(fill(t.testSent, { count: devices })) : toast(t.testNone)),
    onError: failed,
  });
  const subscribe = useMutation({
    mutationFn: async () => {
      if (!data?.webpush_public_key) throw new Error("no key");
      const permission = await Notification.requestPermission();
      if (permission !== "granted") {
        setPush("blocked");
        return;
      }
      const reg = await navigator.serviceWorker.ready;
      const sub = await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: keyBytes(data.webpush_public_key),
      });
      const json = sub.toJSON();
      await api("/notifications/webpush/subscribe", {
        method: "POST",
        body: { endpoint: json.endpoint, keys: json.keys, label: deviceName() },
      });
      setPush("on");
      refresh();
    },
    onError: failed,
  });

  if (!data) {
    return <FormError message={settings.isError ? dict.common.errorGeneric : null} />;
  }

  const weekday = (i: number) =>
    new Date(Date.UTC(2026, 10, 9 + i, 12)).toLocaleDateString(locale, { weekday: "short", timeZone: "UTC" });
  const save = (type: ReminderType, body: Record<string, unknown>) => reminder.mutate({ type, body });
  const quietOn = data.prefs.quiet_start !== null;

  return (
    <section className="grid max-w-xl gap-8">
      <Button asChild variant="ghost" className="w-fit">
        <Link href={to("/settings")}>
          <ArrowLeft className="rtl:rotate-180" aria-hidden />
          {t.back}
        </Link>
      </Button>
      <div className="grid gap-1">
        <h1 className="text-4xl font-bold">{t.title}</h1>
        <p className="text-muted-foreground">{t.lead}</p>
      </div>
      <FormError message={error} />

      {!data.consent ? (
        <div className="grid gap-3 rounded-2xl border border-border bg-card p-5">
          <p>{t.consentOff}</p>
          <Button className="w-fit" onClick={() => consent.mutate()} disabled={consent.isPending}>
            <BellRing aria-hidden />
            {t.turnOn}
          </Button>
        </div>
      ) : (
        <>
          <Section title={t.devicesTitle}>
            <div className="grid gap-3 rounded-2xl border border-border bg-card p-4">
              {push === "on" && <p className="text-sm text-success">{t.pushOn}</p>}
              {push === "off" && data.webpush_public_key && (
                <Button className="w-fit" onClick={() => subscribe.mutate()} disabled={subscribe.isPending}>
                  <BellRing aria-hidden />
                  {t.pushThis}
                </Button>
              )}
              {push === "blocked" && <p className="text-sm text-muted-foreground">{t.pushBlocked}</p>}
              {push === "unsupported" && <p className="text-sm text-muted-foreground">{t.pushUnsupported}</p>}
              {push === "installed-only" && <p className="text-sm text-muted-foreground">{t.pushInstalled}</p>}
              <p className="text-xs text-muted-foreground">{t.iphoneHint}</p>
            </div>

            <div className="grid gap-3 rounded-2xl border border-border bg-card p-4">
              <h3 className="font-semibold">{t.telegramTitle}</h3>
              {!data.telegram_available ? (
                <p className="text-sm text-muted-foreground">{t.telegramOff}</p>
              ) : linking && !hasTelegram ? (
                <div className="grid gap-2" role="status">
                  <p className="text-sm">{t.telegramSteps}</p>
                  <code className="w-fit rounded-lg bg-muted px-3 py-2 text-base" dir="ltr">
                    /start {linking.code}
                  </code>
                  {linking.link && (
                    <Button asChild className="w-fit">
                      <a href={linking.link} target="_blank" rel="noreferrer">
                        <Send className="rtl:-scale-x-100" aria-hidden />
                        {t.telegramOpen}
                      </a>
                    </Button>
                  )}
                  <p className="text-xs text-muted-foreground">{t.telegramWaiting}</p>
                </div>
              ) : (
                !hasTelegram && (
                  <Button variant="outline" className="w-fit" onClick={() => link.mutate()} disabled={link.isPending}>
                    <Send className="rtl:-scale-x-100" aria-hidden />
                    {t.telegramLink}
                  </Button>
                )
              )}
            </div>

            {data.channels.length === 0 ? (
              <p className="text-sm text-muted-foreground">{t.devicesEmpty}</p>
            ) : (
              <ul className="grid gap-2">
                {data.channels.map((device) => {
                  const name = device.type === "telegram" ? t.telegramTitle : (device.label ?? t.webpushDevice);
                  return (
                    <li key={device.id} className="flex items-center justify-between gap-3 rounded-xl border border-border px-4 py-2">
                      <span dir="auto">{name}</span>
                      <Button
                        variant="ghost"
                        size="icon"
                        aria-label={fill(t.removeNamed, { name })}
                        onClick={() => removeDevice.mutate(device.id)}
                        disabled={removeDevice.isPending}
                      >
                        <Trash2 aria-hidden />
                      </Button>
                    </li>
                  );
                })}
              </ul>
            )}
            <Button variant="outline" className="w-fit" onClick={() => test.mutate()} disabled={test.isPending}>
              {t.test}
            </Button>
          </Section>

          <Section title={t.remindersTitle}>
            <ul className="grid gap-3">
              {data.reminders.map((r) => (
                <ReminderRow
                  key={r.type}
                  reminder={r}
                  channels={[...new Set(data.channels.map((c) => c.type))]}
                  weekday={weekday}
                  onSave={(body) => save(r.type, body)}
                  busy={reminder.isPending}
                />
              ))}
            </ul>
          </Section>

          <Section title={t.generalTitle}>
            <div className="grid gap-4 rounded-2xl border border-border bg-card p-4">
              <div className="flex min-h-11 items-center justify-between gap-4">
                <Label htmlFor="quiet-on">{t.quiet}</Label>
                <Switch
                  id="quiet-on"
                  checked={quietOn}
                  onCheckedChange={(on) =>
                    prefs.mutate({
                      ...data.prefs,
                      quiet_start: on ? "22:00" : null,
                      quiet_end: on ? "07:00" : null,
                    })
                  }
                />
              </div>
              {quietOn && (
                <div className="grid grid-cols-2 gap-3">
                  <TimeField
                    label={t.quietFrom}
                    value={data.prefs.quiet_start ?? "22:00"}
                    onSave={(v) => prefs.mutate({ ...data.prefs, quiet_start: v })}
                  />
                  <TimeField
                    label={t.quietTo}
                    value={data.prefs.quiet_end ?? "07:00"}
                    onSave={(v) => prefs.mutate({ ...data.prefs, quiet_end: v })}
                  />
                </div>
              )}
              <div className="flex min-h-11 items-center justify-between gap-4">
                <Label htmlFor="max-per-day">{t.maxPerDay}</Label>
                <select
                  id="max-per-day"
                  className="h-11 rounded-lg border border-input bg-background px-3"
                  value={data.prefs.max_per_day}
                  onChange={(e) => prefs.mutate({ ...data.prefs, max_per_day: Number(e.target.value) })}
                >
                  {[2, 3, 4, 6, 8, 10].map((n) => (
                    <option key={n} value={n}>
                      {n}
                    </option>
                  ))}
                </select>
              </div>
              <div className="flex items-start justify-between gap-4">
                <span className="grid gap-1">
                  <Label htmlFor="show-details">{t.details}</Label>
                  <span className="text-sm text-muted-foreground">{t.detailsBody}</span>
                </span>
                <Switch
                  id="show-details"
                  className="mt-1 shrink-0"
                  checked={data.prefs.show_details}
                  onCheckedChange={(on) => prefs.mutate({ ...data.prefs, show_details: on })}
                />
              </div>
            </div>
          </Section>
        </>
      )}
    </section>
  );
}

function TimeField({ label, value, onSave }: { label: string; value: string; onSave: (v: string) => void }) {
  const id = useId();
  return (
    <div className="grid gap-1">
      <Label htmlFor={id}>{label}</Label>
      <Input
        id={id}
        type="time"
        dir="ltr"
        className="h-11"
        defaultValue={value}
        onBlur={(e) => e.target.value && e.target.value !== value && onSave(e.target.value)}
      />
    </div>
  );
}

function ReminderRow({
  reminder,
  channels,
  weekday,
  onSave,
  busy,
}: {
  reminder: ReminderSetting;
  channels: ChannelType[];
  weekday: (i: number) => string;
  onSave: (body: Record<string, unknown>) => void;
  busy: boolean;
}) {
  const { dict, locale } = useI18n();
  const t = dict.notifications;
  const id = useId();
  const type = reminder.type;
  const text = t.types[type];
  const s = reminder.schedule;
  const snoozed = reminder.snooze_until && new Date(reminder.snooze_until) > new Date() ? reminder.snooze_until : null;

  return (
    <li className="grid gap-3 rounded-2xl border border-border bg-card p-4">
      <div className="flex items-start justify-between gap-4">
        <span className="grid gap-1">
          <Label htmlFor={`${id}-on`} className="font-medium">
            {text.title}
          </Label>
          <span className="text-sm text-muted-foreground">{text.body}</span>
        </span>
        <Switch
          id={`${id}-on`}
          className="mt-1 shrink-0"
          checked={reminder.enabled}
          disabled={busy}
          onCheckedChange={(enabled) => onSave({ enabled })}
        />
      </div>

      {reminder.enabled && (
        <div className="grid gap-3">
          {!NO_TIME.includes(type) && s.time && <TimeField label={t.time} value={s.time} onSave={(time) => onSave({ time })} />}

          {type === "workout" && (
            <fieldset className="grid gap-2">
              <legend className="mb-1 text-sm font-medium">{t.days}</legend>
              <div className="flex flex-wrap gap-2">
                {[0, 1, 2, 3, 4, 5, 6].map((d) => {
                  const on = s.days?.includes(d) ?? false;
                  return (
                    <Button
                      key={d}
                      type="button"
                      variant={on ? "default" : "outline"}
                      className="min-w-11 px-2"
                      aria-pressed={on}
                      disabled={busy || (on && (s.days?.length ?? 0) <= 1)}
                      onClick={() => onSave({ days: on ? s.days?.filter((x) => x !== d) : [...(s.days ?? []), d] })}
                    >
                      {weekday(d)}
                    </Button>
                  );
                })}
              </div>
            </fieldset>
          )}

          {WEEKLY.includes(type) && (
            <div className="grid gap-1">
              <Label htmlFor={`${id}-day`}>{t.day}</Label>
              <select
                id={`${id}-day`}
                className="h-11 rounded-lg border border-input bg-background px-3"
                value={s.weekday ?? 4}
                onChange={(e) => onSave({ weekday: Number(e.target.value) })}
              >
                {[0, 1, 2, 3, 4, 5, 6].map((d) => (
                  <option key={d} value={d}>
                    {weekday(d)}
                  </option>
                ))}
              </select>
            </div>
          )}

          {type === "meals" && s.times && !Array.isArray(s.times) && (
            <div className="grid gap-2 sm:grid-cols-3">
              {(["breakfast", "lunch", "dinner"] as const).map((meal) => (
                <TimeField
                  key={meal}
                  label={t.meals[meal]}
                  value={(s.times as Record<string, string>)[meal] ?? "13:00"}
                  onSave={(v) => onSave({ meal_times: { ...(s.times as Record<string, string>), [meal]: v } })}
                />
              ))}
            </div>
          )}

          {type === "water" && Array.isArray(s.times) && (
            <div className="grid gap-2 sm:grid-cols-3">
              {s.times.map((time, i) => (
                <TimeField
                  key={`${i}-${time}`}
                  label={fill(t.waterTime, { n: i + 1 })}
                  value={time}
                  onSave={(v) => onSave({ water_times: (s.times as string[]).map((x, j) => (j === i ? v : x)) })}
                />
              ))}
            </div>
          )}

          {channels.length > 1 && (
            <div className="grid gap-1">
              <Label htmlFor={`${id}-channel`}>{t.channel}</Label>
              <select
                id={`${id}-channel`}
                className="h-11 rounded-lg border border-input bg-background px-3"
                value={reminder.channel}
                onChange={(e) => onSave({ channel: e.target.value })}
              >
                {channels.map((c) => (
                  <option key={c} value={c}>
                    {t.channelNames[c]}
                  </option>
                ))}
              </select>
            </div>
          )}

          {type !== "review_ready" && (
            <div className={cn("flex flex-wrap items-center gap-3", snoozed && "text-muted-foreground")}>
              {snoozed ? (
                <>
                  <span className="text-sm">
                    {fill(t.snoozed, {
                      date: new Date(snoozed).toLocaleString(locale, { weekday: "short", hour: "numeric", minute: "2-digit" }),
                    })}
                  </span>
                  <Button variant="ghost" onClick={() => onSave({ snooze_hours: 0 })} disabled={busy}>
                    {t.resume}
                  </Button>
                </>
              ) : (
                <Button variant="ghost" className="px-0" onClick={() => onSave({ snooze_hours: 24 })} disabled={busy}>
                  {t.snooze}
                </Button>
              )}
            </div>
          )}
        </div>
      )}
    </li>
  );
}
