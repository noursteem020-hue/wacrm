import { describe, expect, it } from "vitest";
import { isReservedSlug, validateSlug } from "./slug";

describe("validateSlug", () => {
  it("accepts a plain lowercase slug", () => {
    expect(validateSlug("acme")).toEqual({ ok: true, slug: "acme" });
  });

  it("normalizes case and surrounding whitespace", () => {
    expect(validateSlug("  Acme  ")).toEqual({ ok: true, slug: "acme" });
  });

  it("rejects an empty or whitespace-only input", () => {
    expect(validateSlug("").ok).toBe(false);
    expect(validateSlug("   ").ok).toBe(false);
  });

  it("rejects slugs shorter than 3 characters", () => {
    expect(validateSlug("ab").ok).toBe(false);
  });

  it("accepts a slug of exactly 63 characters", () => {
    expect(validateSlug("a".repeat(63)).ok).toBe(true);
  });

  it("rejects a slug longer than 63 characters", () => {
    expect(validateSlug("a".repeat(64)).ok).toBe(false);
  });

  it("rejects a slug that does not start or end alphanumerically", () => {
    expect(validateSlug("-acme").ok).toBe(false);
    expect(validateSlug("acme-").ok).toBe(false);
  });

  it("rejects consecutive hyphens", () => {
    expect(validateSlug("ac--me").ok).toBe(false);
  });

  it("rejects underscores, dots and non-ascii characters", () => {
    expect(validateSlug("ac_me").ok).toBe(false);
    expect(validateSlug("ac.me").ok).toBe(false);
    expect(validateSlug("acmé").ok).toBe(false);
  });

  it("rejects a reserved slug", () => {
    expect(validateSlug("admin").ok).toBe(false);
    expect(validateSlug("www").ok).toBe(false);
  });

  it("carries an error message on failure", () => {
    const result = validateSlug("ac--me");
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.error.length).toBeGreaterThan(0);
  });
});

describe("isReservedSlug", () => {
  it("is true for every reserved word", () => {
    expect(isReservedSlug("admin")).toBe(true);
    expect(isReservedSlug("staging")).toBe(true);
  });

  it("is false for an ordinary slug", () => {
    expect(isReservedSlug("acme")).toBe(false);
  });
});