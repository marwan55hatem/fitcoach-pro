"use client";

import { useQuery } from "@tanstack/react-query";
import { Brain, ImagePlus, Loader2, Plus, SendHorizontal, ShieldAlert, X } from "lucide-react";
import Link from "next/link";
import { useEffect, useId, useRef, useState, type FormEvent } from "react";

import { ProposalCard } from "@/components/coach/proposal-card";
import { FormError } from "@/components/field";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { api, ApiError, uploadFile } from "@/lib/api/client";
import { type ChatEvent, type Proposal, streamChat } from "@/lib/coach/stream";
import { useConsents } from "@/lib/consents";
import { useI18n } from "@/lib/i18n/provider";
import { useLocalePath } from "@/lib/i18n/use-locale-path";
import { cn } from "@/lib/utils";

type Turn = {
  key: string;
  role: "user" | "assistant";
  content: string;
  redFlag?: string | null;
  partial?: boolean;
  proposals: Proposal[];
  photo?: string | null; // a local preview of an attached photo
};

type Detail = {
  id: string;
  messages: { id: string; role: string; content: string; red_flag: string | null; partial: boolean }[];
};

const CHIPS = ["week", "stall", "dinner", "inbody"] as const;

/** The coach chat: answers stream in; suggested changes come as cards to approve. */
export function CoachView() {
  const { dict } = useI18n();
  const t = dict.coach;
  const to = useLocalePath();
  const id = useId();
  const consents = useConsents();
  const [conversation, setConversation] = useState<string | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [text, setText] = useState("");
  const [photo, setPhoto] = useState<File | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const end = useRef<HTMLDivElement>(null);
  const counter = useRef(0); // keys for messages added in this visit

  // Pick up the latest conversation once, so a reload keeps the chat.
  const latest = useQuery({
    queryKey: ["coach", "latest"],
    queryFn: async () => {
      const list = await api<{ id: string }[]>("/coach/conversations");
      return list.length ? api<Detail>(`/coach/conversations/${list[0].id}`) : null;
    },
    staleTime: Infinity,
    enabled: consents.data?.ai_processing === true,
  });
  const [loaded, setLoaded] = useState(false);
  if (!loaded && latest.data !== undefined) {
    setLoaded(true);
    if (latest.data) {
      setConversation(latest.data.id);
      setTurns(
        latest.data.messages.map((m) => ({
          key: m.id,
          role: m.role === "user" ? "user" : "assistant",
          content: m.content,
          redFlag: m.red_flag,
          partial: m.partial,
          proposals: [],
        })),
      );
    }
  }

  useEffect(() => {
    end.current?.scrollIntoView({ block: "end", behavior: "smooth" });
  }, [turns, status]);

  const update = (key: string, change: (turn: Turn) => Turn) =>
    setTurns((all) => all.map((turn) => (turn.key === key ? change(turn) : turn)));

  async function send(message: string) {
    const body = message.trim();
    if (!body || busy) return;
    setBusy(true);
    setError(null);
    setStatus(t.thinking);
    const attached = photo;
    const preview = attached ? URL.createObjectURL(attached) : null;
    counter.current += 2;
    const reply = `a-${counter.current}`;
    setTurns((all) => [
      ...all,
      { key: `u-${counter.current}`, role: "user", content: body, proposals: [], photo: preview },
      { key: reply, role: "assistant", content: "", proposals: [] },
    ]);
    setText("");
    setPhoto(null);
    try {
      const file = attached ? await uploadFile("chat", attached) : null;
      await streamChat(
        { text: body, conversation_id: conversation ?? undefined, file_id: file?.id },
        (event: ChatEvent) => {
          if (event.type === "meta") {
            setConversation(event.conversation_id);
            if (event.red_flag) update(reply, (turn) => ({ ...turn, redFlag: event.red_flag }));
          } else if (event.type === "delta") {
            setStatus(null);
            update(reply, (turn) => ({ ...turn, content: turn.content + event.text }));
          } else if (event.type === "tool") {
            setStatus(t.tools[event.name] ?? t.working);
          } else if (event.type === "proposal") {
            update(reply, (turn) => ({ ...turn, proposals: [...turn.proposals, event.proposal] }));
          } else if (event.type === "error") {
            update(reply, (turn) => ({ ...turn, partial: turn.content.length > 0 }));
            setError(event.code === "ai_budget_exceeded" ? t.budget : t.unavailable);
          }
        },
      );
    } catch (err) {
      setTurns((all) => all.filter((turn) => turn.key !== reply || turn.content));
      if (err instanceof ApiError && err.code === "ai_budget_exceeded") setError(t.budget);
      else if (err instanceof ApiError && err.code === "attachment_not_allowed") setError(t.photoNotAllowed);
      else if (err instanceof ApiError && err.code === "ai_not_configured") setError(t.unavailable);
      else setError(dict.common.errorGeneric);
    } finally {
      setBusy(false);
      setStatus(null);
    }
  }

  const submit = (event: FormEvent) => {
    event.preventDefault();
    void send(text);
  };

  if (consents.data && !consents.data.ai_processing) {
    return (
      <section className="grid gap-4">
        <h1 className="text-4xl font-bold">{t.title}</h1>
        <p className="rounded-2xl border border-border bg-card p-4 text-muted-foreground">{t.aiOff}</p>
        <Button asChild className="w-fit">
          <Link href={to("/settings")}>{t.toSettings}</Link>
        </Button>
      </section>
    );
  }

  return (
    <section className="flex min-h-[calc(100dvh-12rem)] flex-col gap-4">
      <div className="flex items-start justify-between gap-3">
        <div className="grid gap-1">
          <h1 className="text-4xl font-bold">{t.title}</h1>
          <p className="text-sm text-muted-foreground">{t.lead}</p>
        </div>
        <div className="flex shrink-0 gap-1">
          <Button asChild variant="ghost" size="icon" aria-label={t.memory}>
            <Link href={to("/coach/memory")}>
              <Brain aria-hidden />
            </Link>
          </Button>
          <Button
            variant="ghost"
            size="icon"
            aria-label={t.newChat}
            disabled={busy}
            onClick={() => {
              setConversation(null);
              setTurns([]);
              setError(null);
            }}
          >
            <Plus aria-hidden />
          </Button>
        </div>
      </div>

      <ol className="grid flex-1 content-start gap-3" aria-live="polite" aria-busy={busy}>
        {turns.length === 0 && <li className="text-muted-foreground">{t.empty}</li>}
        {turns.map((turn) => (
          <li
            key={turn.key}
            className={cn("grid max-w-[88%] gap-2", turn.role === "user" ? "justify-self-end" : "justify-self-start")}
          >
            {turn.photo && (
              // eslint-disable-next-line @next/next/no-img-element -- a local preview of the chosen photo
              <img src={turn.photo} alt="" className="max-h-40 justify-self-end rounded-xl object-cover" />
            )}
            {(turn.content || turn.role === "user") && (
              <div
                className={cn(
                  "whitespace-pre-wrap rounded-2xl px-4 py-2.5 leading-relaxed",
                  turn.role === "user" ? "bg-primary text-primary-foreground" : "bg-card",
                  turn.redFlag && turn.role === "assistant" && "border border-destructive/50 bg-destructive/10",
                )}
              >
                {turn.redFlag && turn.role === "assistant" && (
                  <span className="mb-1 flex items-center gap-1.5 text-xs font-semibold text-destructive">
                    <ShieldAlert className="size-4" aria-hidden />
                    {t.safety}
                  </span>
                )}
                {/* Its own direction, from the message itself (an English reply on an Arabic page). */}
                <span className="block" dir="auto">
                  {turn.content}
                </span>
              </div>
            )}
            {turn.partial && <p className="text-xs text-muted-foreground">{t.cutOff}</p>}
            {turn.proposals.map((proposal) => (
              <ProposalCard key={proposal.id} proposal={proposal} />
            ))}
          </li>
        ))}
        {status && (
          <li className="flex items-center gap-2 text-sm text-muted-foreground" role="status">
            <Loader2 className="size-4 animate-spin" aria-hidden />
            {status}
          </li>
        )}
      </ol>

      <FormError message={error} />
      {turns.length === 0 && (
        <div className="flex flex-wrap gap-2">
          {CHIPS.map((chip) => (
            <Button key={chip} variant="outline" className="rounded-full" disabled={busy} onClick={() => void send(t.chips[chip])}>
              {t.chips[chip]}
            </Button>
          ))}
        </div>
      )}

      <form onSubmit={submit} className="sticky bottom-20 grid gap-2 rounded-2xl border border-border bg-background p-2 md:bottom-4">
        {photo && (
          <div className="flex items-center gap-2 px-2 text-sm text-muted-foreground">
            <ImagePlus className="size-4" aria-hidden />
            <span className="truncate" dir="auto">
              {photo.name}
            </span>
            <Button type="button" variant="ghost" size="icon" aria-label={t.removePhoto} onClick={() => setPhoto(null)}>
              <X aria-hidden />
            </Button>
          </div>
        )}
        <div className="flex items-end gap-2">
          <label
            htmlFor={`${id}-photo`}
            className="grid size-11 shrink-0 cursor-pointer place-items-center rounded-xl text-muted-foreground hover:bg-accent focus-within:outline-2 focus-within:outline-ring"
          >
            <ImagePlus aria-hidden />
          </label>
          <input
            id={`${id}-photo`}
            type="file"
            accept="image/*"
            className="sr-only"
            aria-label={t.attach}
            onChange={(e) => {
              setPhoto(e.target.files?.[0] ?? null);
              e.target.value = "";
            }}
          />
          <Textarea
            aria-label={t.placeholder}
            placeholder={t.placeholder}
            dir="auto"
            rows={1}
            maxLength={2000}
            value={text}
            className="max-h-40 min-h-11 flex-1 resize-none"
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void send(text);
              }
            }}
          />
          <Button type="submit" size="icon" aria-label={t.send} disabled={busy || !text.trim()}>
            {busy ? <Loader2 className="animate-spin" aria-hidden /> : <SendHorizontal className="rtl:rotate-180" aria-hidden />}
          </Button>
        </div>
      </form>
      {/* Scrolled into view as the reply grows, so the newest words sit above the box. */}
      <div ref={end} />
    </section>
  );
}
