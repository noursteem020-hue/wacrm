import { describe, expect, it } from 'vitest';
import { TENANT_HEADER, withTenantHeader } from './header';

describe('withTenantHeader', () => {
  it('names the header x-tenant-slug', () => {
    expect(TENANT_HEADER).toBe('x-tenant-slug');
  });

  it('overwrites a client-supplied slug', () => {
    const headers = new Headers({ 'x-tenant-slug': 'attacker-tenant' });
    withTenantHeader(headers, 'acme');
    expect(headers.get('x-tenant-slug')).toBe('acme');
  });

  it('sets the slug when the client sent none', () => {
    const headers = new Headers();
    withTenantHeader(headers, 'acme');
    expect(headers.get('x-tenant-slug')).toBe('acme');
  });

  it('removes the header when the host resolves to no tenant', () => {
    const headers = new Headers({ 'x-tenant-slug': 'attacker-tenant' });
    withTenantHeader(headers, null);
    expect(headers.get('x-tenant-slug')).toBeNull();
  });
});