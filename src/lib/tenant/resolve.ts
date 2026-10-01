/**
 * Hostname -> tenant slug resolution.
 *
 * Deliberately independent of `./slug`: resolution says *which* label a host
 * claims, validation decides whether that label is a legal tenant. Keeping them
 * apart means a bad hostname can never crash or be rejected by slug rules here.
 *
 * This function must never throw, whatever it is handed.
 */

function isIpLiteral(host: string): boolean {
  return /^\d{1,3}(\.\d{1,3}){3}$/.test(host) || host.includes(":");
}

export function resolveTenantFromHost(
  host: string | null | undefined,
): string | null {
  if (!host) return null;
  // Strip the port, lowercase, and drop a leading www.
  let name = host.trim().toLowerCase().split(":")[0];
  if (!name || name.includes("..")) return null;
  if (isIpLiteral(name)) return null;
  const parts = name.split(".").filter(Boolean);
  // Need at least 3 labels (crm.acme.com) for a subdomain to exist.
  if (parts.length < 3) {
    return process.env.LOCALHOST_TENANT || null;
  }
  if (parts[0] === "www") parts.shift();
  // The subdomain is the label immediately before the registrable domain,
  // so crm.acme.com and www.acme.com both resolve to "acme".
  return parts[parts.length - 2] || null;
}