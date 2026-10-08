/**
 * Hostname -> tenant slug resolution.
 *
 * Deliberately independent of `./slug`: resolution says *which* label a host
 * claims, validation decides whether that label is a legal tenant. Keeping them
 * apart means a bad hostname can never crash or be rejected by slug rules here.
 *
 * This function must never throw, whatever it is handed.
 */

// Callers pass a host whose port has already been split off, so it can
// never contain ":". Only IPv4 needs matching here.
function isIpLiteral(host: string): boolean {
  return /^\d{1,3}(\.\d{1,3}){3}$/.test(host);
}

export function resolveTenantFromHost(
  host: string | null | undefined,
): string | null {
  if (!host) return null;
  // Strip the port and lowercase.
  const name = host.trim().toLowerCase().split(":")[0];
  if (!name || name.includes("..")) return null;
  if (isIpLiteral(name)) return null;
  const parts = name.split(".").filter(Boolean);
  // Need at least 3 labels (crm.acme.com) for a subdomain to exist.
  if (parts.length < 3) {
    return process.env.LOCALHOST_TENANT || null;
  }
  // Take the second-to-last label, i.e. the first label of the registrable
  // domain (acme in acme.com), so crm.acme.com and www.acme.com both resolve
  // to "acme". Taking the label *before* that would be the subdomain, crm.
  //
  // No `www` special-case is needed or wanted: with three-or-more labels the
  // second-to-last label is the same whether or not `www` leads, so stripping
  // it could not change the result. Adding one back would be dead code.
  //
  // Known limitation: correct for a two-label public suffix only. A multi-part
  // suffix yields a valid-looking but wrong slug — `crm.acme.com.au` -> "com",
  // which passes validateSlug and would route to the wrong tenant. RLS still
  // blocks the read, because profiles.account_id governs data, not this header.
  // Fix with a public-suffix list before wildcard DNS goes live.
  return parts[parts.length - 2] || null;
}