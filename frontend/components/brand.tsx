import { cn } from "@/lib/utils";

/** The loaded-bar mark and the wordmark. The wordmark is never translated. */
export function Brand({ className }: { className?: string }) {
  return (
    <span className={cn("inline-flex items-center gap-2", className)} dir="ltr">
      <svg viewBox="0 0 512 512" aria-hidden className="size-7">
        <rect width="512" height="512" rx="112" fill="var(--card)" />
        <rect x="56" y="232" width="400" height="48" rx="12" fill="var(--gold)" />
        <rect x="150" y="96" width="72" height="320" rx="20" fill="var(--primary)" />
        <rect x="290" y="96" width="72" height="320" rx="20" fill="var(--primary)" />
      </svg>
      <span className="font-heading text-xl font-bold tracking-wide">
        FitCoach <span className="text-primary">PRO</span>
      </span>
    </span>
  );
}
