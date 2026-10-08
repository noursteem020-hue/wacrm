export const TENANT_HEADER = 'x-tenant-slug';

/**
 * Writes the resolved tenant slug onto outgoing request headers,
 * overwriting anything the client supplied. A client-sent
 * `x-tenant-slug` is attacker-controlled input and must never be
 * treated as authoritative.
 */
export function withTenantHeader(
  headers: Headers,
  slug: string | null
): void {
  if (slug) headers.set(TENANT_HEADER, slug);
  else headers.delete(TENANT_HEADER);
}