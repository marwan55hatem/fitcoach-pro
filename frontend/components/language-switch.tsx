"use client";

import { Languages } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { switchLocalePath } from "@/lib/i18n/config";
import { useI18n } from "@/lib/i18n/provider";
import { cn } from "@/lib/utils";

export function LanguageSwitch({ className }: { className?: string }) {
  const { locale, dict } = useI18n();
  const pathname = usePathname();
  const other = locale === "ar" ? "en" : "ar";
  return (
    <Link
      href={switchLocalePath(pathname, other)}
      hrefLang={other}
      aria-label={dict.common.switchLanguage}
      className={cn(
        "inline-flex min-h-11 items-center gap-2 rounded-lg px-3 text-sm text-muted-foreground hover:bg-accent hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring",
        className,
      )}
    >
      <Languages className="size-4" aria-hidden />
      <span lang={other}>{dict.common.languageName}</span>
    </Link>
  );
}
