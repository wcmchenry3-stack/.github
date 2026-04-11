import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import worker, {
  parseRepoMap,
  checkRateLimit,
  checkBurstLimit,
  classifyAndEnrich,
  buildIssueBody,
  sanitizeString,
  validatePayload,
  hashIP,
} from './index.js';

// ---------------------------------------------------------------------------
// Helpers

function makeKVMock() {
  const store = new Map();
  return {
    store, // exposed for inspection in tests
    async get(key, options) {
      const value = store.get(key);
      if (value === undefined) return null;
      if (options?.type === 'json') return JSON.parse(value);
      return value;
    },
    async put(key, value) {
      store.set(key, typeof value === 'object' ? JSON.stringify(value) : value);
    },
  };
}

function makeEnv(overrides = {}) {
  return {
    RATE_LIMIT_KV: makeKVMock(),
    ANTHROPIC_API_KEY: 'test-anthropic-key',
    GITHUB_TOKEN: 'test-github-token',
    ALLOWED_ORIGINS: 'http://localhost:3000,https://gaming-app.example.com',
    APP_REPO_MAP: 'gaming_app:wcmchenry3-stack/gaming_app,book_app:wcmchenry3-stack/book_app',
    ...overrides,
  };
}

function makeRequest(body, { origin = 'http://localhost:3000', ip = '1.2.3.4', contentType = 'application/json' } = {}) {
  return new Request('https://feedback.example.com/feedback', {
    method: 'POST',
    headers: {
      'Content-Type': contentType,
      Origin: origin,
      'CF-Connecting-IP': ip,
    },
    body: JSON.stringify(body),
  });
}

function mockFetch({ claudeIsValid = true, claudeRejectionReason = null, issueNumber = 42 } = {}) {
  const formattedBody =
    '## Steps to Reproduce\n- [ ] (fill in)\n\n## Original Submission (Verbatim)\n> **Title:** Bug title\n>\n> Something is broken';

  return vi.fn(async (url) => {
    const urlStr = typeof url === 'string' ? url : url.toString();

    if (urlStr.includes('anthropic.com')) {
      return {
        ok: true,
        json: async () => ({
          content: [
            {
              text: JSON.stringify({
                isValid: claudeIsValid,
                rejectionReason: claudeIsValid ? null : (claudeRejectionReason ?? 'Spam'),
                formattedBody: claudeIsValid ? formattedBody : null,
              }),
            },
          ],
        }),
        text: async () => '',
      };
    }

    if (urlStr.includes('/labels/user-feedback')) {
      return { ok: true, status: 200 };
    }

    if (urlStr.includes('/labels') && !urlStr.includes('/labels/')) {
      return { ok: true, status: 201, json: async () => ({ name: 'user-feedback' }) };
    }

    if (urlStr.includes('/issues')) {
      return {
        ok: true,
        status: 201,
        json: async () => ({
          number: issueNumber,
          html_url: `https://github.com/wcmchenry3-stack/gaming_app/issues/${issueNumber}`,
        }),
        text: async () => '',
      };
    }

    if (urlStr.includes('/gists')) {
      return {
        ok: true,
        json: async () => ({ html_url: 'https://gist.github.com/abc123' }),
      };
    }

    throw new Error(`Unmocked fetch: ${urlStr}`);
  });
}

// ---------------------------------------------------------------------------
// sanitizeString

describe('sanitizeString', () => {
  it('strips HTML tags but keeps text content between them', () => {
    // Tags are removed; inner text is preserved (ends up in a GitHub issue, not a browser)
    expect(sanitizeString('<script>alert(1)</script>hello')).toBe('alert(1)hello');
    expect(sanitizeString('<b>bold</b> text')).toBe('bold text');
    expect(sanitizeString('<img src="x" onerror="evil()">')).toBe('');
  });

  it('strips null bytes', () => {
    expect(sanitizeString('hel\x00lo')).toBe('hello');
  });

  it('strips control characters but preserves tabs, newlines, carriage returns', () => {
    expect(sanitizeString('a\x01b\x1Fc')).toBe('abc');
    expect(sanitizeString('line1\nline2\ttab')).toBe('line1\nline2\ttab');
  });

  it('leaves clean text unchanged', () => {
    expect(sanitizeString('App crashes on login')).toBe('App crashes on login');
  });
});

// ---------------------------------------------------------------------------
// validatePayload

describe('validatePayload', () => {
  it('returns error for non-object payload', () => {
    expect(validatePayload(null).error).toBeTruthy();
    expect(validatePayload('string').error).toBeTruthy();
  });

  it('returns error when title missing', () => {
    expect(validatePayload({ appId: 'gaming_app', description: 'd', type: 'bug' }).error).toMatch(/title/);
  });

  it('returns error when title exceeds 200 chars after sanitization', () => {
    const result = validatePayload({
      appId: 'gaming_app',
      title: 'x'.repeat(201),
      description: 'd',
      type: 'bug',
    });
    expect(result.error).toMatch(/200/);
  });

  it('returns error when description exceeds 5000 chars', () => {
    const result = validatePayload({
      appId: 'gaming_app',
      title: 't',
      description: 'x'.repeat(5001),
      type: 'bug',
    });
    expect(result.error).toMatch(/5,000/);
  });

  it('returns error for unknown type', () => {
    const result = validatePayload({ appId: 'gaming_app', title: 't', description: 'd', type: 'compliment' });
    expect(result.error).toMatch(/type/);
  });

  it('accepts localization as a valid type', () => {
    const result = validatePayload({ appId: 'gaming_app', title: 't', description: 'd', type: 'localization' });
    expect(result.error).toBeNull();
  });

  it('returns error for invalid BCP 47 locale', () => {
    const result = validatePayload({
      appId: 'gaming_app', title: 't', description: 'd', type: 'localization', locale: 'not valid!',
    });
    expect(result.error).toMatch(/BCP 47/);
  });

  it('accepts a valid BCP 47 locale', () => {
    const result = validatePayload({
      appId: 'gaming_app', title: 't', description: 'd', type: 'localization', locale: 'es-MX',
    });
    expect(result.error).toBeNull();
  });

  it('sanitizes HTML out of title in returned payload', () => {
    const result = validatePayload({
      appId: 'gaming_app',
      title: '<b>My bug</b>',
      description: 'desc',
      type: 'bug',
    });
    expect(result.error).toBeNull();
    expect(result.payload.title).toBe('My bug');
  });

  it('returns error when title is empty after sanitization', () => {
    const result = validatePayload({
      appId: 'gaming_app',
      title: '<b></b>',
      description: 'desc',
      type: 'bug',
    });
    expect(result.error).toMatch(/title is required/);
  });
});

// ---------------------------------------------------------------------------
// hashIP

describe('hashIP', () => {
  it('returns a hex string', async () => {
    const hash = await hashIP('1.2.3.4');
    expect(hash).toMatch(/^[0-9a-f]+$/);
  });

  it('returns same hash for same IP', async () => {
    expect(await hashIP('10.0.0.1')).toBe(await hashIP('10.0.0.1'));
  });

  it('returns different hashes for different IPs', async () => {
    expect(await hashIP('10.0.0.1')).not.toBe(await hashIP('10.0.0.2'));
  });

  it('does not return the raw IP', async () => {
    const hash = await hashIP('192.168.1.1');
    expect(hash).not.toContain('192');
  });
});

// ---------------------------------------------------------------------------
// parseRepoMap

describe('parseRepoMap', () => {
  it('parses a valid map string', () => {
    const map = parseRepoMap('gaming_app:wcmchenry3-stack/gaming_app,book_app:wcmchenry3-stack/book_app');
    expect(map).toEqual({
      gaming_app: 'wcmchenry3-stack/gaming_app',
      book_app: 'wcmchenry3-stack/book_app',
    });
  });

  it('returns empty object for empty string', () => {
    expect(parseRepoMap('')).toEqual({});
  });

  it('skips malformed entries without colons', () => {
    const map = parseRepoMap('gaming_app:wcmchenry3-stack/gaming_app,badentry');
    expect(map).toEqual({ gaming_app: 'wcmchenry3-stack/gaming_app' });
  });
});

// ---------------------------------------------------------------------------
// checkRateLimit — raw IP not stored in KV

describe('checkRateLimit', () => {
  it('allows first 5 requests from same IP', async () => {
    const kv = makeKVMock();
    for (let i = 0; i < 5; i++) {
      const result = await checkRateLimit(kv, '10.0.0.1');
      expect(result.allowed).toBe(true);
    }
  });

  it('blocks the 6th request within the window', async () => {
    const kv = makeKVMock();
    for (let i = 0; i < 5; i++) await checkRateLimit(kv, '10.0.0.1');
    const result = await checkRateLimit(kv, '10.0.0.1');
    expect(result.allowed).toBe(false);
    expect(result.retryAfterMs).toBeGreaterThan(0);
  });

  it('does NOT store the raw IP as a KV key', async () => {
    const kv = makeKVMock();
    await checkRateLimit(kv, '1.2.3.4');
    for (const key of kv.store.keys()) {
      expect(key).not.toContain('1.2.3.4');
    }
  });

  it('stores a hashed key instead', async () => {
    const kv = makeKVMock();
    await checkRateLimit(kv, '1.2.3.4');
    const keys = Array.from(kv.store.keys());
    expect(keys.some((k) => k.startsWith('ratelimit:'))).toBe(true);
    const rateLimitKey = keys.find((k) => k.startsWith('ratelimit:'));
    expect(rateLimitKey).toMatch(/^ratelimit:[0-9a-f]+$/);
  });

  it('resets after the window expires', async () => {
    const kv = makeKVMock();
    const expiredWindow = JSON.stringify({ count: 5, windowStart: Date.now() - 11 * 60 * 1000 });
    const hashedKey = `ratelimit:${await hashIP('10.0.0.3')}`;
    await kv.put(hashedKey, expiredWindow);
    const result = await checkRateLimit(kv, '10.0.0.3');
    expect(result.allowed).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// checkBurstLimit

describe('checkBurstLimit', () => {
  it('allows up to 50 submissions per minute per appId', async () => {
    const kv = makeKVMock();
    for (let i = 0; i < 50; i++) {
      const result = await checkBurstLimit(kv, 'gaming_app');
      expect(result.allowed).toBe(true);
    }
  });

  it('blocks the 51st submission within the burst window', async () => {
    const kv = makeKVMock();
    for (let i = 0; i < 50; i++) await checkBurstLimit(kv, 'gaming_app');
    const result = await checkBurstLimit(kv, 'gaming_app');
    expect(result.allowed).toBe(false);
    expect(result.retryAfterMs).toBeGreaterThan(0);
  });

  it('does not affect a different appId', async () => {
    const kv = makeKVMock();
    for (let i = 0; i < 50; i++) await checkBurstLimit(kv, 'gaming_app');
    const result = await checkBurstLimit(kv, 'book_app');
    expect(result.allowed).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// classifyAndEnrich

describe('classifyAndEnrich', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('returns isValid=true and formattedBody for clean input', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({
      ok: true,
      json: async () => ({
        content: [{ text: JSON.stringify({ isValid: true, rejectionReason: null, formattedBody: '## Steps\n\n## Original Submission (Verbatim)\n> desc' }) }],
      }),
    })));
    const result = await classifyAndEnrich('key', { title: 'App crashes', description: 'Crash on open', type: 'bug' });
    expect(result.isValid).toBe(true);
    expect(result.formattedBody).toContain('Original Submission');
  });

  it('returns isValid=false for spam', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({
      ok: true,
      json: async () => ({
        content: [{ text: JSON.stringify({ isValid: false, rejectionReason: 'Promotional spam', formattedBody: null }) }],
      }),
    })));
    const result = await classifyAndEnrich('key', { title: 'BUY NOW', description: 'Click!!!', type: 'feature' });
    expect(result.isValid).toBe(false);
    expect(result.rejectionReason).toMatch(/spam/i);
  });

  it('strips markdown code fences from Claude response', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({
      ok: true,
      json: async () => ({
        content: [{ text: '```json\n{"isValid":true,"rejectionReason":null,"formattedBody":"body"}\n```' }],
      }),
    })));
    const result = await classifyAndEnrich('key', { title: 'T', description: 'D', type: 'bug' });
    expect(result.isValid).toBe(true);
    expect(result.formattedBody).toBe('body');
  });

  it('throws when Anthropic API returns non-OK status', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, status: 429, text: async () => 'rate limited' })));
    await expect(classifyAndEnrich('key', { title: 'x', description: 'y', type: 'bug' })).rejects.toThrow('429');
  });
});

// ---------------------------------------------------------------------------
// buildIssueBody

describe('buildIssueBody', () => {
  it('includes enriched body and verbatim original text', () => {
    const body = buildIssueBody({
      enrichedBody: '## Steps to Reproduce\n\n## Original Submission (Verbatim)\n> **Title:** T\n>\n> The exact user words.',
      sessionLogs: null, screenshotLink: null, appId: 'gaming_app',
    });
    expect(body).toContain('Steps to Reproduce');
    expect(body).toContain('The exact user words.');
    expect(body).toContain('> **Title:** T');
  });

  it('appends collapsible session logs', () => {
    const body = buildIssueBody({
      enrichedBody: '## Body',
      sessionLogs: 'INFO start\nERROR crash', screenshotLink: null, appId: 'gaming_app',
    });
    expect(body).toContain('<details>');
    expect(body).toContain('INFO start');
  });

  it('truncates session logs exceeding 20 000 chars', () => {
    const body = buildIssueBody({
      enrichedBody: '## Body',
      sessionLogs: 'x'.repeat(25_000), screenshotLink: null, appId: 'gaming_app',
    });
    expect(body).toContain('*(truncated)*');
  });

  it('includes screenshot link when provided', () => {
    const body = buildIssueBody({
      enrichedBody: '## Body',
      sessionLogs: null, screenshotLink: 'https://gist.github.com/abc', appId: 'gaming_app',
    });
    expect(body).toContain('## Screenshot');
    expect(body).toContain('https://gist.github.com/abc');
  });

  it('appends app attribution footer', () => {
    const body = buildIssueBody({ enrichedBody: '## Body', sessionLogs: null, screenshotLink: null, appId: 'book_app' });
    expect(body).toContain('app: `book_app`');
  });
});

// ---------------------------------------------------------------------------
// Full handler — /health

describe('worker.fetch — /health', () => {
  it('GET /health returns 200 with status ok', async () => {
    const req = new Request('https://feedback.example.com/health', { method: 'GET' });
    const resp = await worker.fetch(req, makeEnv());
    expect(resp.status).toBe(200);
    const body = await resp.json();
    expect(body.status).toBe('ok');
  });

  it('GET /health includes X-Content-Type-Options: nosniff', async () => {
    const req = new Request('https://feedback.example.com/health', { method: 'GET' });
    const resp = await worker.fetch(req, makeEnv());
    expect(resp.headers.get('X-Content-Type-Options')).toBe('nosniff');
  });
});

// ---------------------------------------------------------------------------
// Full handler — CORS

describe('worker.fetch — CORS', () => {
  it('OPTIONS from allowed origin returns 204 with CORS headers', async () => {
    const req = new Request('https://feedback.example.com/feedback', {
      method: 'OPTIONS',
      headers: { Origin: 'http://localhost:3000' },
    });
    const resp = await worker.fetch(req, makeEnv());
    expect(resp.status).toBe(204);
    expect(resp.headers.get('Access-Control-Allow-Origin')).toBe('http://localhost:3000');
  });

  it('OPTIONS from disallowed origin returns 403', async () => {
    const req = new Request('https://feedback.example.com/feedback', {
      method: 'OPTIONS',
      headers: { Origin: 'https://evil.com' },
    });
    const resp = await worker.fetch(req, makeEnv());
    expect(resp.status).toBe(403);
  });

  it('POST from disallowed origin returns 403', async () => {
    const resp = await worker.fetch(makeRequest({}, { origin: 'https://evil.com' }), makeEnv());
    expect(resp.status).toBe(403);
  });
});

// ---------------------------------------------------------------------------
// Full handler — Content-Type enforcement

describe('worker.fetch — Content-Type', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('non-application/json Content-Type returns 415', async () => {
    const resp = await worker.fetch(
      makeRequest({ appId: 'gaming_app', title: 't', description: 'd', type: 'bug' }, { contentType: 'text/plain' }),
      makeEnv(),
    );
    expect(resp.status).toBe(415);
  });

  it('application/json Content-Type proceeds normally', async () => {
    vi.stubGlobal('fetch', mockFetch());
    const resp = await worker.fetch(
      makeRequest({ appId: 'gaming_app', title: 't', description: 'd', type: 'bug' }, { contentType: 'application/json; charset=utf-8' }),
      makeEnv(),
    );
    expect(resp.status).toBe(201);
  });
});

// ---------------------------------------------------------------------------
// Full handler — security headers

describe('worker.fetch — security headers', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('all JSON responses include X-Content-Type-Options: nosniff', async () => {
    vi.stubGlobal('fetch', mockFetch());
    const resp = await worker.fetch(
      makeRequest({ appId: 'gaming_app', title: 't', description: 'd', type: 'bug' }),
      makeEnv(),
    );
    expect(resp.headers.get('X-Content-Type-Options')).toBe('nosniff');
  });

  it('error responses include X-Content-Type-Options: nosniff', async () => {
    const resp = await worker.fetch(
      makeRequest({ title: 'missing appId', description: 'd', type: 'bug' }),
      makeEnv(),
    );
    expect(resp.status).toBe(400);
    expect(resp.headers.get('X-Content-Type-Options')).toBe('nosniff');
  });
});

// ---------------------------------------------------------------------------
// Full handler — input validation

describe('worker.fetch — input validation', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('missing appId → 400', async () => {
    const resp = await worker.fetch(makeRequest({ title: 't', description: 'd', type: 'bug' }), makeEnv());
    expect(resp.status).toBe(400);
    expect((await resp.json()).error).toMatch(/appId/);
  });

  it('unknown appId → 400', async () => {
    vi.stubGlobal('fetch', mockFetch());
    const resp = await worker.fetch(
      makeRequest({ appId: 'unknown_app', title: 't', description: 'd', type: 'bug' }),
      makeEnv(),
    );
    expect(resp.status).toBe(400);
    expect((await resp.json()).error).toMatch(/Unknown appId/);
  });

  it('title exceeding 200 chars → 400', async () => {
    const resp = await worker.fetch(
      makeRequest({ appId: 'gaming_app', title: 'x'.repeat(201), description: 'd', type: 'bug' }),
      makeEnv(),
    );
    expect(resp.status).toBe(400);
  });

  it('description exceeding 5000 chars → 400', async () => {
    const resp = await worker.fetch(
      makeRequest({ appId: 'gaming_app', title: 't', description: 'x'.repeat(5001), type: 'bug' }),
      makeEnv(),
    );
    expect(resp.status).toBe(400);
  });

  it('invalid type → 400', async () => {
    const resp = await worker.fetch(
      makeRequest({ appId: 'gaming_app', title: 't', description: 'd', type: 'compliment' }),
      makeEnv(),
    );
    expect(resp.status).toBe(400);
  });

  it('HTML in title is stripped before processing — valid submission succeeds', async () => {
    vi.stubGlobal('fetch', mockFetch());
    const resp = await worker.fetch(
      makeRequest({ appId: 'gaming_app', title: '<b>My bug</b>', description: 'd', type: 'bug' }),
      makeEnv(),
    );
    expect(resp.status).toBe(201);
  });
});

// ---------------------------------------------------------------------------
// Full handler — rate limiting

describe('worker.fetch — rate limiting', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('6th submission from same IP within window → 429 with Retry-After', async () => {
    const env = makeEnv();
    vi.stubGlobal('fetch', mockFetch());
    const payload = { appId: 'gaming_app', title: 't', description: 'd', type: 'bug' };
    for (let i = 0; i < 5; i++) await worker.fetch(makeRequest(payload, { ip: '5.5.5.5' }), env);
    const resp = await worker.fetch(makeRequest(payload, { ip: '5.5.5.5' }), env);
    expect(resp.status).toBe(429);
    expect(resp.headers.get('Retry-After')).toBeTruthy();
  });

  it('IP not stored raw in KV after rate limit check', async () => {
    const env = makeEnv();
    vi.stubGlobal('fetch', mockFetch());
    await worker.fetch(
      makeRequest({ appId: 'gaming_app', title: 't', description: 'd', type: 'bug' }, { ip: '9.8.7.6' }),
      env,
    );
    for (const key of env.RATE_LIMIT_KV.store.keys()) {
      expect(key).not.toContain('9.8.7.6');
    }
  });
});

// ---------------------------------------------------------------------------
// Full handler — Claude classification

describe('worker.fetch — Claude classification', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('spam submission → 422 with generic message (no internal policy details)', async () => {
    vi.stubGlobal('fetch', mockFetch({ claudeIsValid: false, claudeRejectionReason: 'Spam: BUY NOW detected' }));
    const resp = await worker.fetch(
      makeRequest({ appId: 'gaming_app', title: 'BUY NOW', description: 'Click!!!', type: 'feature' }),
      makeEnv(),
    );
    expect(resp.status).toBe(422);
    const body = await resp.json();
    // Must NOT leak the internal rule that triggered
    expect(body.error).not.toContain('BUY NOW');
    expect(body.error).not.toContain('Spam');
    expect(body.error).toBe('Submission could not be accepted.');
  });

  it('valid submission → 201 with issueNumber and issueUrl', async () => {
    vi.stubGlobal('fetch', mockFetch({ claudeIsValid: true, issueNumber: 77 }));
    const resp = await worker.fetch(
      makeRequest({ appId: 'gaming_app', title: 'App crashes', description: 'Crash on open.', type: 'bug' }),
      makeEnv(),
    );
    expect(resp.status).toBe(201);
    const body = await resp.json();
    expect(body.issueNumber).toBe(77);
    expect(body.issueUrl).toContain('/issues/77');
  });
});

// ---------------------------------------------------------------------------
// Full handler — resubmission within rate-limit window → 429

describe('worker.fetch — resubmission returns 429 within window', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('same IP same content → 429 on 6th attempt, not duplicate issue', async () => {
    const env = makeEnv();
    vi.stubGlobal('fetch', mockFetch());
    const payload = { appId: 'gaming_app', title: 'Same bug', description: 'Same description', type: 'bug' };
    for (let i = 0; i < 5; i++) await worker.fetch(makeRequest(payload, { ip: '9.9.9.9' }), env);
    const resp = await worker.fetch(makeRequest(payload, { ip: '9.9.9.9' }), env);
    expect(resp.status).toBe(429);
  });
});
