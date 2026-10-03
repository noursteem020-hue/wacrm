import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { readdirSync } from "node:fs";
import { join } from "node:path";
import { NextRequest, NextResponse } from "next/server";

/**
 * Captures the options object of every `NextResponse.next(...)` call the proxy
 * makes, so a test can assert on the headers actually forwarded to the app.
 * Each call returns a real NextResponse, because `src/proxy.ts` goes on to set
 * cookies on it and the existing 15 tests read those cookies back.
 */
type NextInit = { request?: { headers?: Headers } };
const capturedNextCalls: NextInit[] = [];
const realNext = NextResponse.next.bind(NextResponse);
NextResponse.next = ((init?: NextInit) => {
  capturedNextCalls.push(init ?? {});
  return realNext(init as never);
}) as typeof NextResponse.next;

/** The headers object actually handed to `NextResponse.next({ request: … })`. */
const forwardedHeaders = (call: NextInit | undefined): Headers => {
  expect(call?.request?.headers).toBeInstanceOf(Headers);
  return call!.request!.headers!;
};

// --- Scenario knobs the mock reads -----------------------------------------
// `mockUser`         — what getUser() resolves to (a refreshed session ⇒ user,
//                      or null for the logged-out path).
// `refreshedCookies` — cookies Supabase writes via setAll() during getUser(),
//                      i.e. the freshly *rotated* auth token. The whole point
//                      of the test is that these must survive onto whatever
//                      response the middleware returns — including redirects.
let mockUser: { id: string } | null = null;
let refreshedCookies: Array<{
  name: string;
  value: string;
  options: Record<string, unknown>;
}> = [];

vi.mock("@supabase/ssr", () => ({
  createServerClient: (
    _url: string,
    _key: string,
    opts: {
      cookies: { setAll: (c: typeof refreshedCookies) => void };
    },
  ) => ({
    auth: {
      // Mirrors real auth-js: an expired access token is transparently
      // refreshed inside getUser(), which rotates the refresh token and
      // pushes the new cookies through setAll() before resolving.
      getUser: async () => {
        if (refreshedCookies.length) opts.cookies.setAll(refreshedCookies);
        return { data: { user: mockUser } };
      },
    },
  }),
}));

// Imported after the mock is registered.
const { proxy } = await import("./proxy");

beforeEach(() => {
  process.env.NEXT_PUBLIC_SUPABASE_URL = "https://test.supabase.co";
  process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY = "anon-key";
  mockUser = null;
  refreshedCookies = [];
  capturedNextCalls.length = 0;
});

afterEach(() => vi.clearAllMocks());

const ROTATED = {
  name: "sb-test-auth-token",
  value: "rotated-refresh-token",
  options: { path: "/", httpOnly: true },
};

describe("proxy — refreshed auth cookies survive redirects", () => {
  it("carries the rotated token when redirecting a signed-in user off /login", async () => {
    mockUser = { id: "user-1" };
    refreshedCookies = [ROTATED];

    const res = await proxy(
      new NextRequest("https://app.test/login"),
    );

    // Redirect to /dashboard…
    expect(res.status).toBe(307);
    expect(res.headers.get("location")).toContain("/dashboard");
    // …and the rotated cookie MUST ride along, otherwise the browser keeps
    // replaying the now-consumed refresh token and the session wedges until
    // the user manually clears cookies.
    expect(res.cookies.get(ROTATED.name)?.value).toBe(ROTATED.value);
  });

  it("carries the rotated token when redirecting an unauth user to /login", async () => {
    mockUser = null;
    // Even on the logged-out path getUser() may emit cookie writes (e.g.
    // clearing a dead session); those must not be dropped on the redirect.
    refreshedCookies = [{ ...ROTATED, value: "cleared" }];

    const res = await proxy(
      new NextRequest("https://app.test/dashboard"),
    );

    expect(res.status).toBe(307);
    expect(res.headers.get("location")).toContain("/login");
    expect(res.cookies.get(ROTATED.name)?.value).toBe("cleared");
  });

  it("redirects a signed-in user with an invite token to /join/<token>", async () => {
    mockUser = { id: "user-1" };
    refreshedCookies = [ROTATED];

    const res = await proxy(
      new NextRequest("https://app.test/login?invite=abc123"),
    );

    expect(res.headers.get("location")).toContain("/join/abc123");
    expect(res.cookies.get(ROTATED.name)?.value).toBe(ROTATED.value);
  });

  it("passes through (no redirect) for a signed-in user on a protected page", async () => {
    mockUser = { id: "user-1" };
    refreshedCookies = [ROTATED];

    const res = await proxy(
      new NextRequest("https://app.test/dashboard"),
    );

    // No redirect — the normal NextResponse.next() already carries cookies.
    expect(res.headers.get("location")).toBeNull();
    expect(res.cookies.get(ROTATED.name)?.value).toBe(ROTATED.value);
  });
});

describe("proxy — every dashboard route requires a session", () => {
  // Read the route group rather than hard-coding a list, so a new page added
  // under src/app/(dashboard)/ fails here until it is added to
  // protectedPaths. /flows, /agents and /notifications were missed that way
  // and rendered a broken page to signed-out visitors instead of redirecting.
  const dashboardRoutes = readdirSync(join(__dirname, "app", "(dashboard)"), {
    withFileTypes: true,
  })
    .filter((entry) => entry.isDirectory())
    .map((entry) => `/${entry.name}`);

  it("finds the dashboard route group", () => {
    expect(dashboardRoutes).toContain("/dashboard");
  });

  it.each(dashboardRoutes)("redirects a signed-out visitor from %s to /login", async (route) => {
    mockUser = null;

    const res = await proxy(new NextRequest(`https://app.test${route}`));

    expect(res.status).toBe(307);
    expect(new URL(res.headers.get("location")!).pathname).toBe("/login");
  });
});

describe("proxy — tenant header on the forwarded request", () => {
  // The attacker seed is what makes the fail-closed assertion falsifiable:
  // without it nothing sent the header and nothing deleted it, so "no header"
  // holds trivially and passes even when the proxy never removes one.
  const ATTACKER = "attacker-tenant";

  it("never forwards a client-supplied tenant slug", async () => {
    mockUser = { id: "user-1" };
    refreshedCookies = [];

    const request = new NextRequest("https://crm.acme.com/dashboard", {
      headers: { host: "crm.acme.com", "x-tenant-slug": ATTACKER },
    });
    await proxy(request);

    // This scenario deliberately does NOT rotate, so it asserts the one call it
    // arranges (1) and makes no claim about covering the `setAll` rebuild.
    expect(capturedNextCalls.length).toBe(1);
    const forwarded = forwardedHeaders(capturedNextCalls[0]);
    expect(forwarded.get("x-tenant-slug")).toBe("acme");
  });

  it("sets the slug when the client sent none", async () => {
    mockUser = { id: "user-1" };
    refreshedCookies = [];

    const request = new NextRequest("https://crm.acme.com/dashboard", {
      headers: { host: "crm.acme.com" },
    });
    await proxy(request);

    expect(capturedNextCalls.length).toBe(1);
    expect(forwardedHeaders(capturedNextCalls[0]).get("x-tenant-slug")).toBe("acme");
  });

  it("forwards the rotated cookie AND the tenant header after a token refresh", async () => {
    mockUser = { id: "user-1" };
    refreshedCookies = [ROTATED];

    // Seeded with a pre-existing cookie so a STALE forwarded cookie is
    // distinguishable from an absent one — and a stale cookie is exactly the
    // issue #288 defect.
    const request = new NextRequest("https://crm.acme.com/dashboard", {
      headers: {
        host: "crm.acme.com",
        cookie: "sb-test-auth-token=PRE-ROTATION",
      },
    });
    await proxy(request);

    // The count guard comes FIRST: the mock only calls setAll when
    // `refreshedCookies` is non-empty, so a non-rotating arrange captures one
    // call and "assert on every captured call" would pass over a single clean
    // call with the defect present. Asserting 2 makes that impossible.
    expect(capturedNextCalls.length).toBe(2);

    const [initial, rebuilt] = capturedNextCalls.map(forwardedHeaders);

    // Call #1 is built before getUser() runs, so it can only carry the
    // pre-rotation cookie. It must NOT contain the rotated value.
    expect(initial.get("cookie")).toContain("PRE-ROTATION");
    expect(initial.get("cookie")).not.toContain(ROTATED.value);

    // Call #2 is rebuilt inside setAll and must carry the ROTATED cookie, not
    // the top-of-function snapshot. Asserted per call — never with .some(),
    // which is satisfied by the clean first call while the post-rotation call
    // carries the attacker header.
    expect(rebuilt.get("cookie")).toContain(ROTATED.value);

    // The tenant header is correct on BOTH calls.
    expect(initial.get("x-tenant-slug")).toBe("acme");
    expect(rebuilt.get("x-tenant-slug")).toBe("acme");
  });

  it("removes a client-supplied tenant slug when the host resolves to no tenant", async () => {
    mockUser = { id: "user-1" };
    refreshedCookies = [ROTATED];

    // 127.0.0.1 is a raw IP, which the resolver refuses to map.
    const request = new NextRequest("https://127.0.0.1/dashboard", {
      headers: { host: "127.0.0.1", "x-tenant-slug": ATTACKER },
    });
    await proxy(request);

    expect(capturedNextCalls.length).toBe(2);
    for (const call of capturedNextCalls) {
      // Per call, not .some(): the initial call is clean and the post-rotation
      // one is the broken case.
      expect(forwardedHeaders(call).has("x-tenant-slug")).toBe(false);
    }
  });
});
