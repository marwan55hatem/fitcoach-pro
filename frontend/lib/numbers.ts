const ARABIC_DIGITS = /[٠-٩۰-۹]/g;

/**
 * Read a number typed on any keyboard: Arabic-Indic and Persian digits, and the Arabic
 * decimal separator or a comma, all count. Empty or invalid input gives null.
 */
export function parseNumber(text: string): number | null {
  const normalized = text
    .trim()
    .replace(ARABIC_DIGITS, (d) => String((d.charCodeAt(0) - 0x0660) % 0x90 % 10))
    .replace(/[٫,]/g, ".");
  if (normalized === "" || !/^\d+(\.\d+)?$/.test(normalized)) return null;
  return Number(normalized);
}

/**
 * Keep a number range such as "700–850" in reading order inside Arabic text, where the
 * dash between two numbers would otherwise flip it to "850–700".
 */
export function isolated(text: string): string {
  return `\u2066${text}\u2069`;
}

/** Up to one decimal, without a trailing ".0". */
export function formatKg(value: number): string {
  return String(Math.round(value * 10) / 10);
}
