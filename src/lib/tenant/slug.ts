export const RESERVED_SLUGS = [
  "www", "api", "admin", "mail", "app", "crm",
  "smtp", "ftp", "dev", "staging", "test",
] as const;

export function isReservedSlug(slug: string): boolean {
  return (RESERVED_SLUGS as readonly string[]).includes(slug.toLowerCase());
}

export function validateSlug(
  input: string,
): { ok: true; slug: string } | { ok: false; error: string } {
  const slug = input.trim().toLowerCase();
  if (!slug) return { ok: false, error: "Slug is required" };
  if (slug.length < 3 || slug.length > 63)
    return { ok: false, error: "Slug must be 3-63 characters" };
  if (!/^[a-z0-9][a-z0-9-]*[a-z0-9]$/.test(slug))
    return { ok: false, error: "Slug must use lowercase letters, digits and hyphens, and start and end with a letter or digit" };
  if (slug.includes("--"))
    return { ok: false, error: "Slug must not contain consecutive hyphens" };
  if (isReservedSlug(slug))
    return { ok: false, error: `"${slug}" is a reserved slug` };
  return { ok: true, slug };
}