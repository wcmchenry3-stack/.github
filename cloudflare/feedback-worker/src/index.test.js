import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import worker, { parseRepoMap, checkRateLimit, classifyAndEnrich, buildIssueBody } from './index.js';

// ---------------------------------------------------------------------------
// Helpers

function makeKVMock() {
  const store = new Map();
  return {
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

function makeRequest(body, { origin = 'http://localhost:3000', ip = '1.2.3.4' } = {}) {
  return new Request('https://feedback.example.com/feedback', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
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
      // Label already exists
      return { ok: true, status: 200 };
    }

    if (urlStr.includes('/issues') && !urlStr.match(/\/issues\/\d+/)) {
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
// Health endpoint

describe('worker.fetch — /health', () => {
  it('GET /health returns 200 with status ok', async () => {
    const req = new Request('https://feedback.example.com/health', { method: 'GET' });
    const resp = await worker.fetch(req, makeEnv());
    expect(resp.status).toBe(200);
    const body = await resp.json();
    expect(body.status).toBe('ok');
    expect(body.worker).toBe('feedback-worker');
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
// checkRateLimit

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
    for (let i = 0; i < 5; i++) {
      await checkRateLimit(kv, '10.0.0.1');
    }
    const result = await checkRateLimit(kv, '10.0.0.1');
    expect(result.allowed).toBe(false);
    expect(result.retryAfterMs).toBeGreaterThan(0);
  });

  it('allows a different IP regardless of rate on the first IP', async () => {
    const kv = makeKVMock();
    for (let i = 0; i < 5; i++) {
      await checkRateLimit(kv, '10.0.0.1');
    }
    const result = await checkRateLimit(kv, '10.0.0.2');
    expect(result.allowed).toBe(true);
  });

  it('resets after the window expires', async () => {
    const kv = makeKVMock();
    // Pre-populate an expired window (windowStart = 11 minutes ago)
    const expiredWindow = JSON.stringify({ count: 5, windowStart: Date.now() - 11 * 60 * 1000 });
    await kv.put('ratelimit:10.0.0.3', expiredWindow);

    const result = await checkRateLimit(kv, '10.0.0.3');
    expect(result.allowed).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// classifyAndEnrich

describe('classifyAndEnrich', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('returns isValid=true and formattedBody for clean input', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: true,
        json: async () => ({
          content: [
            {
              text: JSON.stringify({
                isValid: true,
                rejectionReason: null,
                formattedBody: '## Steps to Reproduce\n\n## Original Submission (Verbatim)\n> desc',
              }),
            },
          ],
        }),
      })),
    );

    const result = await classifyAndEnrich('key', {
      title: 'App crashes on login',
      description: 'Opens and immediately closes.',
      type: 'bug',
    });

    expect(result.isValid).toBe(true);
    expect(result.formattedBody).toContain('Original Submission');
  });

  it('returns isValid=false with rejectionReason for spam', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: true,
        json: async () => ({
          content: [
            {
              text: JSON.stringify({
                isValid: false,
                rejectionReason: 'Contains promotional spam',
                formattedBody: null,
              }),
            },
          ],
        }),
      })),
    );

    const result = await classifyAndEnrich('key', {
      title: 'BUY CRYPTO NOW',
      description: 'Click here for profits!!!',
      type: 'feature',
    });

    expect(result.isValid).toBe(false);
    expect(result.rejectionReason).toMatch(/spam/i);
  });

  it('strips markdown code fences from Claude response if present', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: true,
        json: async () => ({
          content: [
            {
              text: '```json\n{"isValid":true,"rejectionReason":null,"formattedBody":"body"}\n```',
            },
          ],
        }),
      })),
    );

    const result = await classifyAndEnrich('key', {
      title: 'Test',
      description: 'Test',
      type: 'bug',
    });

    expect(result.isValid).toBe(true);
    expect(result.formattedBody).toBe('body');
  });

  it('throws when Anthropic API returns non-OK status', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: false,
        status: 429,
        text: async () => 'rate limited',
      })),
    );

    await expect(
      classifyAndEnrich('key', { title: 'x', description: 'y', type: 'bug' }),
    ).rejects.toThrow('Anthropic API 429');
  });
});

// ---------------------------------------------------------------------------
// buildIssueBody

describe('buildIssueBody', () => {
  it('includes enriched body', () => {
    const body = buildIssueBody({
      enrichedBody: '## Steps to Reproduce\n- step 1\n\n## Original Submission (Verbatim)\n> **Title:** T\n>\n> D',
      sessionLogs: null,
      screenshotLink: null,
      appId: 'gaming_app',
    });
    expect(body).toContain('Steps to Reproduce');
    expect(body).toContain('Original Submission (Verbatim)');
  });

  it('preserves verbatim original text in blockquote', () => {
    const originalDesc = 'The exact user words here.';
    const body = buildIssueBody({
      enrichedBody: `## Steps to Reproduce\n\n## Original Submission (Verbatim)\n> **Title:** My bug\n>\n> ${originalDesc}`,
      sessionLogs: null,
      screenshotLink: null,
      appId: 'gaming_app',
    });
    expect(body).toContain(originalDesc);
    expect(body).toContain('> **Title:** My bug');
  });

  it('appends collapsible session logs block', () => {
    const body = buildIssueBody({
      enrichedBody: '## Body',
      sessionLogs: 'INFO app started\nERROR crash at line 42',
      screenshotLink: null,
      appId: 'gaming_app',
    });
    expect(body).toContain('<details>');
    expect(body).toContain('<summary>Session logs</summary>');
    expect(body).toContain('INFO app started');
    expect(body).toContain('ERROR crash at line 42');
  });

  it('truncates session logs exceeding 20 000 chars', () => {
    const longLogs = 'x'.repeat(25_000);
    const body = buildIssueBody({
      enrichedBody: '## Body',
      sessionLogs: longLogs,
      screenshotLink: null,
      appId: 'gaming_app',
    });
    expect(body).toContain('*(truncated)*');
  });

  it('includes screenshot link when provided', () => {
    const body = buildIssueBody({
      enrichedBody: '## Body',
      sessionLogs: null,
      screenshotLink: 'https://gist.github.com/abc123',
      appId: 'gaming_app',
    });
    expect(body).toContain('## Screenshot');
    expect(body).toContain('https://gist.github.com/abc123');
  });

  it('omits screenshot and logs sections when not provided', () => {
    const body = buildIssueBody({
      enrichedBody: '## Body',
      sessionLogs: null,
      screenshotLink: null,
      appId: 'book_app',
    });
    expect(body).not.toContain('## Screenshot');
    expect(body).not.toContain('<details>');
  });

  it('appends app attribution footer', () => {
    const body = buildIssueBody({
      enrichedBody: '## Body',
      sessionLogs: null,
      screenshotLink: null,
      appId: 'book_app',
    });
    expect(body).toContain('app: `book_app`');
  });
});

// ---------------------------------------------------------------------------
// Full request handler — integration-style unit tests

describe('worker.fetch — CORS', () => {
  afterEach(() => vi.unstubAllGlobals());

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
    const req = makeRequest({ appId: 'gaming_app' }, { origin: 'https://evil.com' });
    const resp = await worker.fetch(req, makeEnv());
    expect(resp.status).toBe(403);
  });
});

describe('worker.fetch — input validation', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('missing appId → 400', async () => {
    vi.stubGlobal('fetch', mockFetch());
    const resp = await worker.fetch(
      makeRequest({ title: 't', description: 'd', type: 'bug' }),
      makeEnv(),
    );
    expect(resp.status).toBe(400);
    const body = await resp.json();
    expect(body.error).toMatch(/appId/);
  });

  it('unknown appId → 400', async () => {
    vi.stubGlobal('fetch', mockFetch());
    const resp = await worker.fetch(
      makeRequest({ appId: 'unknown_app', title: 't', description: 'd', type: 'bug' }),
      makeEnv(),
    );
    expect(resp.status).toBe(400);
    const body = await resp.json();
    expect(body.error).toMatch(/Unknown appId/);
  });

  it('title exceeding 255 chars → 400', async () => {
    vi.stubGlobal('fetch', mockFetch());
    const resp = await worker.fetch(
      makeRequest({ appId: 'gaming_app', title: 'x'.repeat(256), description: 'd', type: 'bug' }),
      makeEnv(),
    );
    expect(resp.status).toBe(400);
  });

  it('invalid type → 400', async () => {
    vi.stubGlobal('fetch', mockFetch());
    const resp = await worker.fetch(
      makeRequest({ appId: 'gaming_app', title: 't', description: 'd', type: 'compliment' }),
      makeEnv(),
    );
    expect(resp.status).toBe(400);
  });
});

describe('worker.fetch — rate limiting', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('6th submission from same IP within window → 429 with Retry-After', async () => {
    const env = makeEnv();
    vi.stubGlobal('fetch', mockFetch());

    const validPayload = { appId: 'gaming_app', title: 't', description: 'd', type: 'bug' };

    for (let i = 0; i < 5; i++) {
      await worker.fetch(makeRequest(validPayload, { ip: '5.5.5.5' }), env);
    }

    const resp = await worker.fetch(makeRequest(validPayload, { ip: '5.5.5.5' }), env);
    expect(resp.status).toBe(429);
    expect(resp.headers.get('Retry-After')).toBeTruthy();
  });
});

describe('worker.fetch — Claude classification', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('spam submission → 422 with rejection reason', async () => {
    vi.stubGlobal('fetch', mockFetch({ claudeIsValid: false, claudeRejectionReason: 'Spam detected' }));
    const resp = await worker.fetch(
      makeRequest({ appId: 'gaming_app', title: 'BUY NOW', description: 'Click here!!!', type: 'feature' }),
      makeEnv(),
    );
    expect(resp.status).toBe(422);
    const body = await resp.json();
    expect(body.error).toMatch(/Spam detected/);
  });

  it('valid submission → 201 with issueNumber and issueUrl', async () => {
    vi.stubGlobal('fetch', mockFetch({ claudeIsValid: true, issueNumber: 77 }));
    const resp = await worker.fetch(
      makeRequest({ appId: 'gaming_app', title: 'App crashes', description: 'It crashes on open.', type: 'bug' }),
      makeEnv(),
    );
    expect(resp.status).toBe(201);
    const body = await resp.json();
    expect(body.issueNumber).toBe(77);
    expect(body.issueUrl).toContain('/issues/77');
  });
});

describe('worker.fetch — resubmission returns 429 within window', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('same IP same content → 429 on 6th attempt, not duplicate issue', async () => {
    const env = makeEnv();
    vi.stubGlobal('fetch', mockFetch());

    const payload = { appId: 'gaming_app', title: 'Same bug', description: 'Same description', type: 'bug' };

    for (let i = 0; i < 5; i++) {
      await worker.fetch(makeRequest(payload, { ip: '9.9.9.9' }), env);
    }

    const resp = await worker.fetch(makeRequest(payload, { ip: '9.9.9.9' }), env);
    expect(resp.status).toBe(429);
  });
});
