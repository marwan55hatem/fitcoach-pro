"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Camera, Loader2, Trash2 } from "lucide-react";
import { useId, useState } from "react";
import { toast } from "sonner";

import { FormError } from "@/components/field";
import { Button } from "@/components/ui/button";
import { api, ApiError, uploadFile } from "@/lib/api/client";
import { apiErrorMessage } from "@/lib/api/errors";
import { PHOTO_VIEWS, type PhotoView, type ProgressPhoto } from "@/lib/api/types";
import { useConsents } from "@/lib/consents";
import { fill, useI18n } from "@/lib/i18n/provider";
import { cn } from "@/lib/utils";

const KEY = ["progress-photos"] as const;
const LINK_FRESH_MS = 4 * 60_000; // signed links last five minutes

/** Take or choose a body photo for one view. It never goes to an AI. */
export function PhotoCapture({ view, onAdded }: { view: PhotoView; onAdded?: () => void }) {
  const { dict } = useI18n();
  const t = dict.progress;
  const id = useId();
  const client = useQueryClient();
  const add = useMutation({
    mutationFn: async (file: File) => {
      const stored = await uploadFile("body_photo", file);
      return api<ProgressPhoto>("/progress-photos", { method: "POST", body: { file_id: stored.id, view } });
    },
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: KEY });
      onAdded?.();
    },
  });
  const label = fill(t.addPhoto, { view: t.views[view] });
  return (
    <div className="grid gap-1">
      <label
        htmlFor={`${id}-${view}`}
        className="flex min-h-12 cursor-pointer items-center justify-center gap-2 rounded-xl border border-dashed border-input bg-card px-3 text-sm font-medium hover:bg-accent focus-within:outline-2 focus-within:outline-ring"
      >
        {add.isPending ? <Loader2 className="size-4 animate-spin" aria-hidden /> : <Camera className="size-4" aria-hidden />}
        {add.isSuccess ? `${t.views[view]} ✓` : label}
      </label>
      <input
        id={`${id}-${view}`}
        type="file"
        accept="image/*"
        capture="user"
        className="sr-only"
        aria-label={label}
        disabled={add.isPending}
        onChange={(e) => {
          const file = e.target.files?.[0];
          e.target.value = "";
          if (file) add.mutate(file);
        }}
      />
      <FormError
        message={
          add.error instanceof ApiError && add.error.code === "too_large"
            ? dict.food.tooLarge
            : add.isError
              ? apiErrorMessage(add.error, dict)
              : null
        }
      />
    </div>
  );
}

/** Drag the handle to wipe between an older and a newer photo of the same view. */
function BeforeAfter({ before, after }: { before: ProgressPhoto; after: ProgressPhoto }) {
  const { dict, locale } = useI18n();
  const t = dict.progress;
  const [split, setSplit] = useState(50);
  const date = (iso: string) =>
    new Date(`${iso}T12:00:00Z`).toLocaleDateString(locale, { day: "numeric", month: "short", year: "numeric" });
  return (
    <figure className="grid gap-2">
      <div className="relative aspect-[3/4] w-full overflow-hidden rounded-xl bg-muted" dir="ltr">
        {/* Signed links to private storage: next/image would fetch and cache them on the server. */}
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={after.url} alt="" className="absolute inset-0 size-full object-cover" />
        <div className="absolute inset-0 overflow-hidden" style={{ clipPath: `inset(0 ${100 - split}% 0 0)` }}>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={before.url} alt="" className="absolute inset-0 size-full object-cover" />
        </div>
        <div className="pointer-events-none absolute inset-y-0 w-0.5 bg-primary" style={{ left: `${split}%` }} aria-hidden />
        <span className="absolute start-2 top-2 rounded-md bg-black/60 px-2 py-0.5 text-xs text-white">{date(before.taken_on)}</span>
        <span className="absolute end-2 top-2 rounded-md bg-black/60 px-2 py-0.5 text-xs text-white">{date(after.taken_on)}</span>
      </div>
      <input
        type="range"
        min={0}
        max={100}
        value={split}
        dir="ltr"
        onChange={(e) => setSplit(Number(e.target.value))}
        aria-label={fill(t.compareHint, { before: date(before.taken_on), after: date(after.taken_on) })}
        className="h-11 w-full accent-[var(--primary)]"
      />
    </figure>
  );
}

export function PhotosSection() {
  const { dict } = useI18n();
  const t = dict.progress;
  const client = useQueryClient();
  const consents = useConsents();
  const allowed = consents.data?.body_photos === true;
  const [view, setView] = useState<PhotoView>("front");
  const photos = useQuery({
    queryKey: KEY,
    queryFn: () => api<ProgressPhoto[]>("/progress-photos"),
    enabled: allowed,
    staleTime: LINK_FRESH_MS,
    refetchInterval: LINK_FRESH_MS,
  });
  const remove = useMutation({
    mutationFn: (photo: ProgressPhoto) => api(`/progress-photos/${photo.id}`, { method: "DELETE" }),
    onSuccess: () => {
      toast.success(t.photoDeleted);
      void client.invalidateQueries({ queryKey: KEY });
    },
  });

  const ofView = (photos.data ?? []).filter((p) => p.view === view); // newest first
  return (
    <section className="grid gap-4 rounded-2xl border border-border bg-card p-4" aria-labelledby="photos-title">
      <div className="grid gap-1">
        <h2 id="photos-title" className="text-xl font-semibold">
          {t.photosTitle}
        </h2>
        <p className="text-sm text-muted-foreground">{t.photosLead}</p>
      </div>
      {consents.data && !allowed ? (
        <p className="rounded-xl bg-muted p-3 text-sm text-muted-foreground">{t.photosOff}</p>
      ) : (
        <>
          <div role="tablist" aria-label={t.photosTitle} className="grid grid-cols-3 gap-1 rounded-xl bg-muted p-1">
            {PHOTO_VIEWS.map((v) => (
              <button
                key={v}
                type="button"
                role="tab"
                aria-selected={view === v}
                onClick={() => setView(v)}
                className={cn(
                  "min-h-11 rounded-lg text-sm font-medium focus-visible:outline-2 focus-visible:outline-ring",
                  view === v ? "bg-card text-foreground shadow-sm" : "text-muted-foreground",
                )}
              >
                {t.views[v]}
              </button>
            ))}
          </div>
          <PhotoCapture view={view} />
          {photos.isPending && <p className="text-sm text-muted-foreground">{dict.common.loading}</p>}
          {photos.isError && <FormError message={dict.common.errorGeneric} />}
          {photos.isSuccess && ofView.length === 0 && (
            <p className="text-sm text-muted-foreground">{fill(t.noPhotos, { view: t.views[view] })}</p>
          )}
          {ofView.length >= 2 && (
            <div className="grid gap-2">
              <h3 className="font-semibold">{t.compare}</h3>
              <BeforeAfter before={ofView[ofView.length - 1]} after={ofView[0]} />
            </div>
          )}
          {ofView.length > 0 && (
            <ul className="grid grid-cols-3 gap-2">
              {ofView.map((photo) => (
                <li key={photo.id} className="relative">
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    src={photo.thumbnail_url ?? photo.url}
                    alt={`${t.views[photo.view]} ${photo.taken_on}`}
                    className="aspect-[3/4] w-full rounded-lg object-cover"
                  />
                  <Button
                    variant="secondary"
                    size="icon"
                    className="absolute end-1 top-1 bg-black/60"
                    aria-label={t.deletePhoto}
                    disabled={remove.isPending}
                    onClick={() => remove.mutate(photo)}
                  >
                    <Trash2 aria-hidden />
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </section>
  );
}
