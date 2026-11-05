"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Trash2 } from "lucide-react";
import Link from "next/link";
import { useState, type FormEvent } from "react";

import { FormError } from "@/components/field";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api, ApiError } from "@/lib/api/client";
import { fill, useI18n } from "@/lib/i18n/provider";
import { useLocalePath } from "@/lib/i18n/use-locale-path";

type Note = { id: string; key: string; value: string; source: "ai" | "user" };
const KEY = ["coach", "notes"] as const;

/** What the coach remembers: the user sees, edits and deletes every note. */
export function MemoryView() {
  const { dict } = useI18n();
  const t = dict.memory;
  const to = useLocalePath();
  const client = useQueryClient();
  const notes = useQuery({ queryKey: KEY, queryFn: () => api<Note[]>("/coach/notes") });
  const [key, setKey] = useState("");
  const [value, setValue] = useState("");

  const save = useMutation({
    mutationFn: (note: { key: string; value: string }) => api<Note>("/coach/notes", { method: "PUT", body: note }),
    onSuccess: () => {
      setKey("");
      setValue("");
      void client.invalidateQueries({ queryKey: KEY });
    },
  });
  const remove = useMutation({
    mutationFn: (note: Note) => api(`/coach/notes/${note.id}`, { method: "DELETE" }),
    onSuccess: () => void client.invalidateQueries({ queryKey: KEY }),
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (key.trim() && value.trim()) save.mutate({ key: key.trim(), value: value.trim() });
  };
  const refused = save.error instanceof ApiError && save.error.status === 422;

  return (
    <section className="grid max-w-xl gap-5">
      <Link href={to("/coach")} className="flex min-h-11 w-fit items-center gap-2 text-sm text-primary">
        <ArrowLeft className="size-4 rtl:rotate-180" aria-hidden />
        {t.back}
      </Link>
      <div className="grid gap-1">
        <h1 className="text-4xl font-bold">{t.title}</h1>
        <p className="text-muted-foreground">{t.lead}</p>
      </div>

      {notes.isPending && <p className="text-muted-foreground">{dict.common.loading}</p>}
      {notes.isError && <FormError message={dict.common.errorGeneric} />}
      {notes.data?.length === 0 && <p className="text-muted-foreground">{t.empty}</p>}
      <ul className="grid gap-2">
        {notes.data?.map((note) => (
          <li key={note.id} className="flex items-start gap-3 rounded-2xl border border-border bg-card p-4">
            <div className="grid flex-1 gap-1">
              <span className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                <span dir="auto">{note.key}</span>
                <span className="rounded-full bg-muted px-2 py-0.5">{note.source === "ai" ? t.byCoach : t.byYou}</span>
              </span>
              <span dir="auto">{note.value}</span>
            </div>
            <Button
              variant="ghost"
              size="icon"
              aria-label={fill(t.deleteNote, { key: note.key })}
              disabled={remove.isPending}
              onClick={() => remove.mutate(note)}
            >
              <Trash2 aria-hidden />
            </Button>
          </li>
        ))}
      </ul>

      <form onSubmit={submit} className="grid gap-3 rounded-2xl border border-border bg-card p-4">
        <div className="grid gap-2">
          <Label htmlFor="note-key">{t.key}</Label>
          <Input id="note-key" dir="auto" maxLength={100} value={key} onChange={(e) => setKey(e.target.value)} />
        </div>
        <div className="grid gap-2">
          <Label htmlFor="note-value">{t.value}</Label>
          <Input id="note-value" dir="auto" maxLength={200} value={value} onChange={(e) => setValue(e.target.value)} />
        </div>
        <FormError message={refused ? t.refused : save.isError ? dict.common.errorGeneric : null} />
        <Button type="submit" className="w-fit" disabled={save.isPending || !key.trim() || !value.trim()}>
          {t.add}
        </Button>
      </form>
    </section>
  );
}
