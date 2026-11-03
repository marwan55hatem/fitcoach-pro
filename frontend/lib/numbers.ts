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

/** Up to one decimal, without a trailing ".0". */
export function formatKg(value: number): string {
  return String(Math.round(value * 10) / 10);
}
