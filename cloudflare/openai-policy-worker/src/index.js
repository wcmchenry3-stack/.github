import PostalMime from 'postal-mime';

const GITHUB_REPO = 'wcmchenry3-stack/.github';
const GITHUB_API  = 'https://api.github.com';

// Only process mail from these domains to avoid noise.
const ALLOWED_SENDERS = ['openai.com', 'sendgrid.net'];

export default {
  async email(message, env, _ctx) {
    const from = message.from.toLowerCase();
    const isAllowed = ALLOWED_SENDERS.some((domain) => from.includes(domain));

    if (!isAllowed) {
      console.log(`Ignored email from ${message.from} — not an allowed sender`);
      return;
    }

    // Parse raw email bytes into subject + plain-text body.
    const rawBytes = await new Response(message.raw).arrayBuffer();
    const parsed   = await new PostalMime().parse(rawBytes);

    const subject = parsed.subject ?? '(no subject)';
    const body    = parsed.text ?? stripHtml(parsed.html ?? '') ?? '(no body)';

    console.log(`Processing OpenAI email: "${subject}"`);

    await createGitHubIssue(env.GITHUB_TOKEN, {
      title: `OpenAI newsletter: ${subject}`,
      body: buildIssueBody(message.from, subject, body),
      labels: ['policy-update'],
    });
  },
};

// ---------------------------------------------------------------------------

function stripHtml(html) {
  return html
    .replace(/<style[^>]*>[\s\S]*?<\/style>/gi, '')
    .replace(/<script[^>]*>[\s\S]*?<\/script>/gi, '')
    .replace(/<[^>]+>/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

function buildIssueBody(from, subject, body) {
  const preview = body.length > 4000 ? body.slice(0, 4000) + '\n\n*(truncated — full email in Worker logs)*' : body;
  return [
    '## OpenAI Policy Email Received',
    '',
    `**From:** ${from}`,
    `**Subject:** ${subject}`,
    '',
    '---',
    '',
    preview,
    '',
    '---',
    '*Auto-opened by the [openai-policy-email-worker](https://dash.cloudflare.com) Cloudflare Email Worker*',
  ].join('\n');
}

async function createGitHubIssue(token, { title, body, labels }) {
  const resp = await fetch(`${GITHUB_API}/repos/${GITHUB_REPO}/issues`, {
    method: 'POST',
    headers: {
      Authorization:        `Bearer ${token}`,
      Accept:               'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28',
      'Content-Type':       'application/json',
      'User-Agent':         'wcmchenry3-stack-policy-worker/1.0',
    },
    body: JSON.stringify({ title, body, labels }),
  });

  if (!resp.ok) {
    const err = await resp.text();
    throw new Error(`GitHub API ${resp.status}: ${err}`);
  }

  const issue = await resp.json();
  console.log(`Opened issue #${issue.number}: ${issue.html_url}`);
}
