import { describe, expect, it } from "vitest";
import {
  CURRENCIES,
  DEFAULT_CURRENCY,
  formatCurrency,
  formatCurrencyShort,
} from "./currency";

/**
 * `formatCurrency` passes `undefined` as the locale on purpose, so the
 * rendering follows the ambient ICU default (en-US in CI, ar-SA on
 * some machines). Digits, the grouping separator and the decimal
 * separator are all locale-specific, so the expectations below are
 * derived from Intl at that same locale instead of hardcoding ASCII
 * literals.
 */

/** Grouped whole number — mirrors the currency.ts fallback path. */
function groupedInteger(amount: number): string {
  return new Intl.NumberFormat(undefined, {
    maximumFractionDigits: 0,
  }).format(amount);
}

/**
 * Whether a rendering carries a fractional part. The decimal separator
 * is locale-dependent (".", ",", "٫", …), so read it from Intl rather
 * than assuming one. A whole-number currency render must contain none:
 * under ar-SA the old `.not.toContain(".00")` check passed for the
 * wrong reason (the separator is "٫", never "."), and under de-DE it
 * would miss a real regression like "1.234,00".
 */
function hasFraction(rendered: string): boolean {
  const decimal = new Intl.NumberFormat(undefined)
    .formatToParts(1.5)
    .find((part) => part.type === "decimal")?.value;
  return decimal !== undefined && rendered.includes(decimal);
}

describe("formatCurrency", () => {
  it("formats whole amounts with no minor units", () => {
    const out = formatCurrency(1234, "USD");
    // Grouped for 1234 in the ambient locale ("," / "٫" / "." / …).
    expect(out).toContain(groupedInteger(1234));
    expect(hasFraction(out)).toBe(false);
  });

  it("defaults to USD when no currency is given", () => {
    expect(formatCurrency(10)).toBe(formatCurrency(10, DEFAULT_CURRENCY));
  });

  it("treats an empty-string currency as the default", () => {
    expect(formatCurrency(10, "")).toBe(formatCurrency(10, DEFAULT_CURRENCY));
  });

  it("coerces non-finite values to 0", () => {
    expect(formatCurrency(Number.NaN, "USD")).toContain(groupedInteger(0));
  });

  it("renders a well-formed but unknown ISO code without throwing", () => {
    // Intl is lenient here — it uses the code as the symbol.
    const out = formatCurrency(1234, "ZZZ");
    expect(out).toContain("ZZZ");
    expect(out).toContain(groupedInteger(1234));
  });

  it("never throws on a structurally invalid code (no DB CHECK on deals.currency)", () => {
    for (const bad of ["United States", "US", "USDD", "12", "u$d"]) {
      expect(() => formatCurrency(1234, bad)).not.toThrow();
      expect(formatCurrency(1234, bad)).toContain(groupedInteger(1234));
    }
  });

  it("formats every offered currency without throwing", () => {
    for (const c of CURRENCIES) {
      expect(() => formatCurrency(1000, c.code)).not.toThrow();
    }
  });
});

describe("formatCurrencyShort", () => {
  it("abbreviates millions and thousands with the currency symbol", () => {
    expect(formatCurrencyShort(2_500_000, "USD")).toBe("$2.5M");
    expect(formatCurrencyShort(3_400, "USD")).toBe("$3.4k");
    expect(formatCurrencyShort(900, "USD")).toBe("$900");
  });

  it("uses the matching symbol for non-USD currencies", () => {
    expect(formatCurrencyShort(1_000, "EUR")).toBe("€1.0k");
    expect(formatCurrencyShort(1_000, "INR")).toBe("₹1.0k");
  });

  it("falls back to the code prefix for unknown currencies (no throw)", () => {
    expect(formatCurrencyShort(1_000, "ZZZ")).toBe("ZZZ 1.0k");
  });
});
