# Feedback Widget Token Contract

Design-token interface consumed by the in-app feedback widget.

Any app can theme the widget by supplying a `feedback-theme.css` file that maps
the app's own tokens to the `--feedback-*` properties below.
**No widget source code changes are required.**

Machine-readable schema: [`schemas/feedback-widget-tokens.schema.json`](../../schemas/feedback-widget-tokens.schema.json)

---

## Naming convention

```
--feedback-[category]-[variant]
```

Categories: `color`, `font`, `spacing`, `border-radius`, `transition`.

---

## Token reference

Legend — **Required** column:
- ✅ must be set; widget will look broken without it
- ⚪ optional; fallback value is used if absent

**App DS** column — whether the token *must* match the app's primary design
system (brand colours, typeface, border-radius) or can be widget-specific.

---

### Colors

| Token | Purpose | Fallback | Required | App DS |
|-------|---------|----------|----------|--------|
| `--feedback-color-background` | Widget container background | `#ffffff` | ✅ | ✅ |
| `--feedback-color-background-input` | Text input / textarea background | `#ffffff` | ✅ | ✅ |
| `--feedback-color-text-primary` | Primary text — labels, body copy | `#111827` | ✅ | ✅ |
| `--feedback-color-text-secondary` | Secondary text — hints, placeholders, character counts | `#6b7280` | ✅ | ✅ |
| `--feedback-color-text-on-primary` | Text on top of the primary action button | `#ffffff` | ✅ | ✅ |
| `--feedback-color-border` | Default input border | `#d1d5db` | ✅ | ⚪ |
| `--feedback-color-border-focus` | Input border when focused | `#2563eb` | ✅ | ⚪ |
| `--feedback-color-focus-ring` | Keyboard focus outline — must meet WCAG 2.4.11 (3:1 contrast ratio against adjacent colours) | `#2563eb` | ✅ | ⚪ |
| `--feedback-color-error` | Error state — border colour and inline error text | `#dc2626` | ✅ | ⚪ |
| `--feedback-color-error-background` | Error message banner background | `#fef2f2` | ⚪ | ⚪ |
| `--feedback-color-success` | Success confirmation text and icon | `#16a34a` | ✅ | ⚪ |
| `--feedback-color-primary` | Submit button background | `#2563eb` | ✅ | ✅ |
| `--feedback-color-primary-hover` | Submit button background on hover / active | `#1d4ed8` | ✅ | ✅ |
| `--feedback-color-overlay` | Modal backdrop tint | `rgba(0,0,0,0.5)` | ✅ | ⚪ |

---

### Typography

All font-size values **must use `rem`** — `px` values break WCAG 1.4.4 (Resize Text).

| Token | Purpose | Fallback | Required | App DS |
|-------|---------|----------|----------|--------|
| `--feedback-font-family` | Widget font stack | `system-ui, sans-serif` | ✅ | ✅ |
| `--feedback-font-size-heading` | Widget title / section heading | `1.125rem` | ✅ | ⚪ |
| `--feedback-font-size-body` | Body copy, textarea input text | `1rem` | ✅ | ⚪ |
| `--feedback-font-size-label` | Form field labels | `0.875rem` | ✅ | ⚪ |
| `--feedback-font-size-small` | Helper text, character count | `0.75rem` | ✅ | ⚪ |
| `--feedback-font-weight-normal` | Regular weight — body text | `400` | ✅ | ⚪ |
| `--feedback-font-weight-medium` | Medium weight — labels | `500` | ⚪ | ⚪ |
| `--feedback-font-weight-bold` | Bold weight — widget title | `600` | ✅ | ⚪ |
| `--feedback-line-height-body` | Line height — body copy and textarea | `1.5` | ✅ | ⚪ |

---

### Spacing

| Token | ~px equivalent | Purpose | Required | App DS |
|-------|---------------|---------|----------|--------|
| `--feedback-spacing-xs` | ~4px | Tight internal spacing | ⚪ | ⚪ |
| `--feedback-spacing-sm` | ~8px | Label-to-input gap, button icon gap | ✅ | ⚪ |
| `--feedback-spacing-md` | ~16px | Default internal padding, gap between fields | ✅ | ⚪ |
| `--feedback-spacing-lg` | ~24px | Widget container padding, section spacing | ✅ | ⚪ |
| `--feedback-spacing-xl` | ~32px | Large outer spacing, heading-to-form gap | ⚪ | ⚪ |

All spacing values should use `rem` — consistent with the typography rule above.

---

### Border radius

| Token | Purpose | Fallback | Required | App DS |
|-------|---------|----------|----------|--------|
| `--feedback-border-radius-sm` | Chip / badge corners | `0.25rem` | ⚪ | ⚪ |
| `--feedback-border-radius-md` | Input and button corners | `0.375rem` | ✅ | ✅ |
| `--feedback-border-radius-lg` | Widget modal container | `0.75rem` | ✅ | ✅ |

---

### Motion

Implementations must honour `prefers-reduced-motion` — wrap transitions in:

```css
@media (prefers-reduced-motion: no-preference) {
  .feedback-widget * {
    transition-duration: var(--feedback-transition-duration);
    transition-timing-function: var(--feedback-transition-easing);
  }
}
```

| Token | Purpose | Fallback | Required |
|-------|---------|----------|----------|
| `--feedback-transition-duration` | Hover / focus / open state transitions | `150ms` | ⚪ |
| `--feedback-transition-easing` | Easing function | `ease-in-out` | ⚪ |

---

## Reference implementation — `feedback-theme.css`

Each app ships a `feedback-theme.css` at the root of its frontend directory.
This file only maps app tokens to `--feedback-*` properties — no hardcoded values.

### gaming_app

```css
/* frontend/feedback-theme.css */
:root {
  /* Colors — map to gaming_app's existing token set */
  --feedback-color-background:        var(--color-surface);
  --feedback-color-background-input:  var(--color-surface-raised);
  --feedback-color-text-primary:      var(--color-text-primary);
  --feedback-color-text-secondary:    var(--color-text-muted);
  --feedback-color-text-on-primary:   var(--color-text-on-brand);
  --feedback-color-border:            var(--color-border);
  --feedback-color-border-focus:      var(--color-brand-primary);
  --feedback-color-focus-ring:        var(--color-brand-primary);
  --feedback-color-error:             var(--color-error);
  --feedback-color-error-background:  var(--color-error-subtle);
  --feedback-color-success:           var(--color-success);
  --feedback-color-primary:           var(--color-brand-primary);
  --feedback-color-primary-hover:     var(--color-brand-primary-hover);
  --feedback-color-overlay:           var(--color-overlay);

  /* Typography */
  --feedback-font-family:        var(--font-family-body);
  --feedback-font-size-heading:  var(--font-size-lg);
  --feedback-font-size-body:     var(--font-size-md);
  --feedback-font-size-label:    var(--font-size-sm);
  --feedback-font-size-small:    var(--font-size-xs);
  --feedback-font-weight-normal: var(--font-weight-regular);
  --feedback-font-weight-medium: var(--font-weight-medium);
  --feedback-font-weight-bold:   var(--font-weight-semibold);
  --feedback-line-height-body:   var(--line-height-relaxed);

  /* Spacing */
  --feedback-spacing-xs: var(--space-1);
  --feedback-spacing-sm: var(--space-2);
  --feedback-spacing-md: var(--space-4);
  --feedback-spacing-lg: var(--space-6);
  --feedback-spacing-xl: var(--space-8);

  /* Border radius */
  --feedback-border-radius-sm: var(--radius-sm);
  --feedback-border-radius-md: var(--radius-md);
  --feedback-border-radius-lg: var(--radius-lg);

  /* Motion */
  --feedback-transition-duration: var(--duration-fast);
  --feedback-transition-easing:   var(--easing-standard);
}
```

### book_app

```css
/* frontend/feedback-theme.css */
:root {
  /* Colors — book_app uses a Tailwind-based token set */
  --feedback-color-background:        var(--color-white);
  --feedback-color-background-input:  var(--color-gray-50);
  --feedback-color-text-primary:      var(--color-gray-900);
  --feedback-color-text-secondary:    var(--color-gray-500);
  --feedback-color-text-on-primary:   var(--color-white);
  --feedback-color-border:            var(--color-gray-300);
  --feedback-color-border-focus:      var(--color-indigo-600);
  --feedback-color-focus-ring:        var(--color-indigo-600);
  --feedback-color-error:             var(--color-red-600);
  --feedback-color-error-background:  var(--color-red-50);
  --feedback-color-success:           var(--color-green-600);
  --feedback-color-primary:           var(--color-indigo-600);
  --feedback-color-primary-hover:     var(--color-indigo-700);
  --feedback-color-overlay:           rgb(0 0 0 / 0.5);

  /* Typography */
  --feedback-font-family:        var(--font-sans);
  --feedback-font-size-heading:  1.125rem;
  --feedback-font-size-body:     1rem;
  --feedback-font-size-label:    0.875rem;
  --feedback-font-size-small:    0.75rem;
  --feedback-font-weight-normal: 400;
  --feedback-font-weight-medium: 500;
  --feedback-font-weight-bold:   600;
  --feedback-line-height-body:   1.5;

  /* Spacing (Tailwind scale) */
  --feedback-spacing-xs: 0.25rem;
  --feedback-spacing-sm: 0.5rem;
  --feedback-spacing-md: 1rem;
  --feedback-spacing-lg: 1.5rem;
  --feedback-spacing-xl: 2rem;

  /* Border radius — book_app uses rounded-md / rounded-xl */
  --feedback-border-radius-sm: 0.25rem;
  --feedback-border-radius-md: 0.375rem;
  --feedback-border-radius-lg: 0.75rem;

  /* Motion */
  --feedback-transition-duration: 150ms;
  --feedback-transition-easing:   ease-in-out;
}
```

---

## Provenance map format

Each app maintains a `src/locales/provenance.json` file that marks every i18n
key as either `"ai"` (auto-translated by the Claude pipeline) or `"human"`
(manually written or reviewed by a human translator).

The feedback widget reads this file to render provenance labels in the
localization suggestion form (#112).

### Format

```json
{
  "common.save": "human",
  "common.cancel": "human",
  "game.leaderboard.title": "ai",
  "game.settings.language": "human",
  "book.shelf.empty_state": "ai"
}
```

### Rules

- Every key present in any locale file **must** have an entry.
- New keys added by the Claude translation pipeline are added as `"ai"`.
- Keys reviewed or written by a human translator are updated to `"human"`.
- A CI check enforcing full coverage will be added in a future story — for
  now enforcement is manual: the `provenance.json` update is part of every
  i18n PR checklist.

### Location

```
frontend/
└── src/
    └── locales/
        ├── en.json
        ├── es.json
        └── provenance.json   ← lives here
```

---

## New app integration checklist

To add the feedback widget to a new app:

```
1. Register the app in the worker
   └── Add `newapp:wcmchenry3-stack/newapp` to the FEEDBACK_WORKER APP_REPO_MAP secret
   └── Add the app's origin to ALLOWED_ORIGINS secret
   └── Run: wrangler deploy (from cloudflare/feedback-worker/)

2. Set the env var in the app
   └── VITE_FEEDBACK_WORKER_URL=https://feedback-worker.wcmchenry3.workers.dev
   └── Add to render.yaml and .env.example

3. Create feedback-theme.css
   └── Location: frontend/feedback-theme.css
   └── Map every required ✅ --feedback-* token to an app token (no hardcoded values)
   └── Import it at the app root: import './feedback-theme.css'

4. Create provenance.json
   └── Location: frontend/src/locales/provenance.json
   └── Seed with all existing i18n keys marked "human" or "ai" as appropriate

5. Add the feedback i18n namespace
   └── Create: frontend/src/locales/en/feedback.json (and all supported locales)
   └── Required keys: title, type_bug, type_feature, type_localization,
       placeholder_title, placeholder_description, submit, submit_success,
       submit_error, screenshot_label, logs_label

6. Mount the component
   └── Import <FeedbackWidget appId="newapp" /> in the app shell
   └── Wrap with the i18n provider and theme context
```

---

## CI and the design token check

`called-design-token-check.yml` does **not** flag `var(--feedback-*)` references
— it only detects hardcoded values (hex, rgb/hsl literals, px font sizes).

`feedback-theme.css` in each app must map tokens using `var()` references only.
If an app has no design tokens yet (e.g. uses hardcoded Tailwind values as in
book_app above), it must use the fallback values from this contract rather than
arbitrary hardcoded ones. The workflow will flag any `#hex` or `rgb()` values
in `feedback-theme.css` as violations.

A dedicated lint step validating that all required `--feedback-*` tokens are
defined in each app's `feedback-theme.css` will be added in a follow-up story
once both gaming_app and book_app implementations are merged.
