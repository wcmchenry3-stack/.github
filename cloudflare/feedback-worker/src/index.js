const GITHUB_API = 'https://api.github.com';
const ANTHROPIC_API = 'https://api.anthropic.com';

const RATE_LIMIT_MAX = 5;
const RATE_LIMIT_WINDOW_MS = 10 * 60 * 1000; // 10 minutes

export default {
  async fetch(request, env) {
    if (request.method === 'OPTIONS') {
      return handlePreflight(request, env);
    }

    const url = new URL(request.url);
    if (request.method === 'POST' && url.pathname === '/feedback') {
      return handleFeedback(request, env);
    }

    return new Response('Not found', { status: 404 });
  },
};

// ---------------------------------------------------------------------------
// CORS

function getAllowedOrigins(env) {
  return (env.ALLOWED_ORIGINS || '').split(',').map((s) => s.trim()).filter(Boolean);
}

function corsHeaders(origin) {
  return {
    'Access-Control-Allow-Origin': origin,
    'Access-Control-Allow-Methods': 'POST, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type',
    'Access-Control-Max-Age': '86400',
  };
}

function checkOrigin(request, env) {
  const origin = request.headers.get('Origin') || '';
  return getAllowedOrigins(env).includes(origin) ? origin : null;
}

function handlePreflight(request, env) {
  const origin = checkOrigin(request, env);
  if (!origin) return new Response('Forbidden', { status: 403 });
  return new Response(null, { status: 204, headers: corsHeaders(origin) });
}

// ---------------------------------------------------------------------------
// Main handler

async function handleFeedback(request, env) {
  const origin = checkOrigin(request, env);
  if (!origin) {
    return jsonResponse({ error: 'Origin not allowed' }, 403);
  }

  const cors = corsHeaders(origin);

  // Parse body
  let payload;
  try {
    payload = await request.json();
  } catch {
    return jsonResponse({ error: 'Invalid JSON body' }, 400, cors);
  }

  // Validate
  const validationError = validatePayload(payload);
  if (validationError) {
    return jsonResponse({ error: validationError }, 400, cors);
  }

  const { appId, title, description, type, screenshotBase64, sessionLogs } = payload;

  // Resolve target repo
  const repoMap = parseRepoMap(env.APP_REPO_MAP || '');
  const targetRepo = repoMap[appId];
  if (!targetRepo) {
    return jsonResponse({ error: `Unknown appId: ${appId}` }, 400, cors);
  }

  // Rate limit
  const ip = request.headers.get('CF-Connecting-IP') || 'unknown';
  const rateLimit = await checkRateLimit(env.RATE_LIMIT_KV, ip);
  if (!rateLimit.allowed) {
    const retryAfter = String(Math.ceil(rateLimit.retryAfterMs / 1000));
    return jsonResponse(
      { error: 'Rate limit exceeded. Please try again later.' },
      429,
      { ...cors, 'Retry-After': retryAfter },
    );
  }

  // Claude: classify + enrich
  let enrichment;
  try {
    enrichment = await classifyAndEnrich(env.ANTHROPIC_API_KEY, { title, description, type });
  } catch (err) {
    console.error('Claude API error:', err.message);
    return jsonResponse({ error: 'Failed to process feedback. Please try again.' }, 502, cors);
  }

  if (!enrichment.isValid) {
    return jsonResponse(
      { error: `Submission rejected: ${enrichment.rejectionReason}` },
      422,
      cors,
    );
  }

  // Screenshot → private Gist
  let screenshotLink = null;
  if (screenshotBase64) {
    try {
      screenshotLink = await uploadScreenshotGist(env.GITHUB_TOKEN, screenshotBase64, title);
    } catch (err) {
      console.warn('Screenshot upload failed (non-fatal):', err.message);
    }
  }

  // Build issue body
  const issueBody = buildIssueBody({
    enrichedBody: enrichment.formattedBody,
    sessionLogs,
    screenshotLink,
    appId,
  });

  // Ensure user-feedback label exists in target repo
  await ensureLabel(env.GITHUB_TOKEN, targetRepo, {
    name: 'user-feedback',
    color: 'F9E4B7',
    description: 'Submitted via in-app feedback form',
  });

  const typeLabel = type === 'bug' ? 'bug' : 'enhancement';

  // Create GitHub issue
  let issue;
  try {
    issue = await createGitHubIssue(env.GITHUB_TOKEN, targetRepo, {
      title,
      body: issueBody,
      labels: [typeLabel, 'user-feedback'],
    });
  } catch (err) {
    console.error('GitHub API error:', err.message);
    return jsonResponse({ error: 'Failed to create issue. Please try again.' }, 502, cors);
  }

  return jsonResponse({ issueNumber: issue.number, issueUrl: issue.html_url }, 201, cors);
}

// ---------------------------------------------------------------------------
// Input validation

function validatePayload(payload) {
  if (!payload || typeof payload !== 'object') return 'Payload must be a JSON object';

  const { appId, title, description, type, screenshotBase64, sessionLogs } = payload;

  if (!appId || typeof appId !== 'string') return 'appId is required';
  if (!title || typeof title !== 'string') return 'title is required';
  if (title.length > 255) return 'title must be 255 characters or fewer';
  if (!description || typeof description !== 'string') return 'description is required';
  if (description.length > 10_000) return 'description must be 10,000 characters or fewer';
  if (!type || !['bug', 'feature'].includes(type)) return 'type must be "bug" or "feature"';

  if (screenshotBase64 !== undefined) {
    if (typeof screenshotBase64 !== 'string') return 'screenshotBase64 must be a string';
    // ~2 MB base64 ≈ 1.5 MB decoded
    if (screenshotBase64.length > 2_800_000) return 'screenshot exceeds 2 MB limit';
  }

  if (sessionLogs !== undefined) {
    if (typeof sessionLogs !== 'string') return 'sessionLogs must be a string';
    if (sessionLogs.length > 50_000) return 'sessionLogs must be 50,000 characters or fewer';
  }

  return null;
}

// ---------------------------------------------------------------------------
// Repo map

export function parseRepoMap(raw) {
  // Format: "gaming_app:wcmchenry3-stack/gaming_app,book_app:wcmchenry3-stack/book_app"
  const map = {};
  for (const entry of raw.split(',')) {
    const colonIdx = entry.indexOf(':');
    if (colonIdx === -1) continue;
    const key = entry.slice(0, colonIdx).trim();
    const value = entry.slice(colonIdx + 1).trim();
    if (key && value) map[key] = value;
  }
  return map;
}

// ---------------------------------------------------------------------------
// Rate limiting (Cloudflare KV)

export async function checkRateLimit(kv, ip) {
  const key = `ratelimit:${ip}`;
  const now = Date.now();

  let record = { count: 0, windowStart: now };

  const existing = await kv.get(key, { type: 'json' });
  if (existing) {
    const elapsed = now - existing.windowStart;
    if (elapsed < RATE_LIMIT_WINDOW_MS) {
      record = existing;
    }
    // else: window expired, reset with fresh record
  }

  if (record.count >= RATE_LIMIT_MAX) {
    const retryAfterMs = RATE_LIMIT_WINDOW_MS - (now - record.windowStart);
    return { allowed: false, retryAfterMs };
  }

  record.count += 1;
  const ttlSeconds = Math.ceil(RATE_LIMIT_WINDOW_MS / 1000);
  await kv.put(key, JSON.stringify(record), { expirationTtl: ttlSeconds });

  return { allowed: true };
}

// ---------------------------------------------------------------------------
// Claude: classify + enrich

export async function classifyAndEnrich(apiKey, { title, description, type }) {
  const typeLabel = type === 'bug' ? 'Bug Report' : 'Feature Request';

  const structureInstructions =
    type === 'bug'
      ? `## Steps to Reproduce\n- [ ] (fill in)\n\n## Expected Behavior\n\n## Actual Behavior`
      : `## Use Case\n\n## Proposed Solution\n\n## Acceptance Criteria\n- [ ] (fill in)`;

  const prompt = `You are a feedback classifier and formatter for a mobile/web application.

Analyze the user feedback below and respond with ONLY valid JSON — no markdown fences, no explanation.

Response schema:
{
  "isValid": boolean,
  "rejectionReason": "string (only if isValid is false, otherwise null)",
  "formattedBody": "string (only if isValid is true, otherwise null)"
}

isValid must be FALSE only for: spam, abuse, hate speech, threats, or content clearly unrelated to app usage.
ALL legitimate feedback — including harsh criticism, complaints, and detailed bug reports — must be isValid = TRUE.

If isValid is true, formattedBody must be a GitHub Markdown issue body structured as:
${structureInstructions}

## Original Submission (Verbatim)
> **Title:** {user title here, unmodified}
>
> {user description here, unmodified, each line prefixed with ">"}

Do NOT modify the original text. Preserve it exactly inside the blockquote.

<feedback_content>
Type: ${typeLabel}
Title: ${title}
Description: ${description}
</feedback_content>`;

  const resp = await fetch(`${ANTHROPIC_API}/v1/messages`, {
    method: 'POST',
    headers: {
      'x-api-key': apiKey,
      'anthropic-version': '2023-06-01',
      'content-type': 'application/json',
    },
    body: JSON.stringify({
      model: 'claude-haiku-4-5-20251001',
      max_tokens: 2048,
      messages: [{ role: 'user', content: prompt }],
    }),
  });

  if (!resp.ok) {
    const err = await resp.text();
    throw new Error(`Anthropic API ${resp.status}: ${err}`);
  }

  const data = await resp.json();
  const rawText = data.content?.[0]?.text || '';

  // Strip markdown code fences if Claude wrapped the JSON anyway
  const jsonText = rawText.replace(/^```(?:json)?\n?/m, '').replace(/\n?```$/m, '').trim();

  const result = JSON.parse(jsonText);

  if (typeof result.isValid !== 'boolean') {
    throw new Error('Unexpected Claude response structure');
  }

  return result;
}

// ---------------------------------------------------------------------------
// Screenshot → private GitHub Gist

async function uploadScreenshotGist(token, base64Data, feedbackTitle) {
  const resp = await fetch(`${GITHUB_API}/gists`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28',
      'Content-Type': 'application/json',
      'User-Agent': 'wcmchenry3-stack-feedback-worker/1.0',
    },
    body: JSON.stringify({
      description: `Feedback screenshot: ${feedbackTitle}`,
      public: false,
      files: {
        'screenshot.b64': { content: base64Data },
        'README.md': {
          content:
            '## Feedback Screenshot\n\nThis file contains a base64-encoded PNG screenshot.\n' +
            'Decode `screenshot.b64` with: `base64 -d screenshot.b64 > screenshot.png`',
        },
      },
    }),
  });

  if (!resp.ok) {
    throw new Error(`GitHub Gist API ${resp.status}`);
  }

  const gist = await resp.json();
  return gist.html_url;
}

// ---------------------------------------------------------------------------
// Issue body builder

export function buildIssueBody({ enrichedBody, sessionLogs, screenshotLink, appId }) {
  const parts = [enrichedBody];

  if (screenshotLink) {
    parts.push(
      '',
      '## Screenshot',
      `[View screenshot](${screenshotLink}) *(base64-encoded — see the Gist README for decode instructions)*`,
    );
  }

  if (sessionLogs) {
    const truncated =
      sessionLogs.length > 20_000
        ? sessionLogs.slice(0, 20_000) + '\n\n*(truncated)*'
        : sessionLogs;
    parts.push(
      '',
      '<details>',
      '<summary>Session logs</summary>',
      '',
      '```',
      truncated,
      '```',
      '',
      '</details>',
    );
  }

  parts.push('', '---', `*Submitted via in-app feedback form · app: \`${appId}\`*`);

  return parts.join('\n');
}

// ---------------------------------------------------------------------------
// GitHub helpers

async function ensureLabel(token, repo, { name, color, description }) {
  const headers = {
    Authorization: `Bearer ${token}`,
    Accept: 'application/vnd.github+json',
    'X-GitHub-Api-Version': '2022-11-28',
    'User-Agent': 'wcmchenry3-stack-feedback-worker/1.0',
  };

  const check = await fetch(`${GITHUB_API}/repos/${repo}/labels/${encodeURIComponent(name)}`, {
    headers,
  });

  if (check.status === 404) {
    await fetch(`${GITHUB_API}/repos/${repo}/labels`, {
      method: 'POST',
      headers: { ...headers, 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, color, description }),
    });
  }
}

async function createGitHubIssue(token, repo, { title, body, labels }) {
  const resp = await fetch(`${GITHUB_API}/repos/${repo}/issues`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28',
      'Content-Type': 'application/json',
      'User-Agent': 'wcmchenry3-stack-feedback-worker/1.0',
    },
    body: JSON.stringify({ title, body, labels }),
  });

  if (!resp.ok) {
    const err = await resp.text();
    throw new Error(`GitHub API ${resp.status}: ${err}`);
  }

  return resp.json();
}

// ---------------------------------------------------------------------------
// Utility

function jsonResponse(data, status, extraHeaders = {}) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json', ...extraHeaders },
  });
}
