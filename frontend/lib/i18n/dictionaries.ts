import "server-only";

import { ar } from "./ar";
import type { Locale } from "./config";
import { en, type Dictionary } from "./en";

const dictionaries: Record<Locale, Dictionary> = { en, ar };

export function getDictionary(locale: Locale): Dictionary {
  return dictionaries[locale];
}
