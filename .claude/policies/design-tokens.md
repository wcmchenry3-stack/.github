# Design Tokens & Accessibility Policy

Policy references:

- https://www.w3.org/TR/WCAG22/ (WCAG 2.2)
- https://www.w3.org/WAI/ARIA/apg/ (ARIA Authoring Practices)
- https://developer.mozilla.org/en-US/docs/Web/Accessibility/ARIA

## Detection

Files matching ANY of these patterns trigger this policy:

- `:\s*#[0-9a-fA-F]{3,8}|rgba?\s*\(|hsla?\s*\(|font-size:\s*[0-9]+px|font-family:\s*["']|tabindex=["'][1-9]`

Skip the following (checked against full path and basename):

- `node_modules/`, `dist/`, `build/`, `.cache/`, `coverage/`, `public/`
- Token definition files: `tokens/`, `*.tokens.json`, `tailwind.config.*`, `theme.*.ts/js`
- Minified files: `*.min.css`, `*.min.js`
- Storybook: `*.stories.tsx`, `*.stories.ts`
- Tests: `*.test.*`, `*.spec.*`, `__snapshots__/`
- Non-UI assets: `*.svg`, `*.md`, `*.mdx`

## Checks

### Check 1: Hex color hardcoded [error]

**Pattern**: `:\s*#[0-9a-fA-F]{3,8}[;\s"'\}]`
**Presence**: forbidden
**Message**: Hardcoded hex color detected. Replace with a design token (e.g. `var(--color-brand-primary)` in CSS, or the equivalent token constant in JS/TS). Hard-coded colors diverge from brand guidelines and make theme changes impossible.
**Reference**: https://www.w3.org/TR/WCAG22/#use-of-color

### Check 2: RGB/HSL color literal [error]

**Pattern**: `(color|background|background-color|border-color|fill|stroke|outline-color):\s*(rgb|rgba|hsl|hsla)\s*\(`
**Presence**: forbidden
**Message**: Hardcoded RGB/HSL color detected. Use a design token instead. RGB/HSL literals bypass the branding system just as hex codes do.
**Reference**: https://www.w3.org/TR/WCAG22/#use-of-color

### Check 3: Hardcoded font-size in px [error]

**Pattern**: `font-size:\s*[0-9]+px`
**Presence**: forbidden
**Message**: Hardcoded `font-size` in `px` detected. Use a design token (e.g. `var(--font-size-md)`) or a relative unit (`rem`/`em`) so user browser font-size preferences are respected. Hard-coded `px` sizes break WCAG 1.4.4 (Resize Text).
**Reference**: https://www.w3.org/TR/WCAG22/#resize-text

### Check 4: Hardcoded font-family [warning]

**Pattern**: `font-family:\s*["'][^"']+["']`
**Presence**: forbidden
**Message**: Hardcoded `font-family` string literal detected. Reference a design token (e.g. `var(--font-family-sans)`) so the typeface can be updated centrally without touching component code.
**Reference**: https://developer.mozilla.org/en-US/docs/Web/CSS/font-family

### Check 5: tabindex > 0 [error]

**Pattern**: `tabindex=["'][1-9][0-9]*["']`
**Presence**: forbidden
**Message**: Positive `tabindex` value detected. Values > 0 override the natural DOM tab order and create a confusing keyboard navigation experience. Use `tabindex="0"` to make an element focusable in DOM order, or `tabindex="-1"` for programmatic-only focus. See WCAG 2.4.3 (Focus Order).
**Reference**: https://www.w3.org/TR/WCAG22/#focus-order

### Check 6: onclick/onkeydown on non-interactive elements [warning]

**Pattern**: `<(div|span|p|section|article|header|footer|main|aside)\b[^>]*on(click|keydown|keyup|keypress)=`
**Presence**: forbidden
**Message**: Event handler on a non-interactive element detected. Use a native `<button>` or `<a>` element instead — they have built-in keyboard support, focus management, and ARIA semantics. If a custom interactive element is truly required, add `role="button"` and `tabindex="0"`. See WCAG 4.1.2 (Name, Role, Value).
**Reference**: https://www.w3.org/TR/WCAG22/#name-role-value

### Check 7: img without alt attribute [error]

**Pattern**: `<img\b` on a line that does not contain `alt=`
**Presence**: forbidden
**Message**: `<img>` without `alt` attribute detected. All images must have `alt` text for screen-reader users (WCAG 1.1.1 Non-text Content). Use `alt=""` for purely decorative images (add `role="presentation"` too), or a descriptive string for informational images.
**Reference**: https://www.w3.org/TR/WCAG22/#non-text-content

### Check 8: aria-hidden on focusable elements [warning]

**Pattern**: `aria-hidden="true"` on the same line as `href=|tabindex=|<button|<input|<select|<textarea|<a\s`
**Presence**: forbidden
**Message**: `aria-hidden="true"` on a potentially focusable element. Hidden elements remain keyboard-reachable, creating invisible focus traps for keyboard-only and AT users. Remove `aria-hidden` or move it to a non-focusable wrapper. See WCAG 4.1.2.
**Note**: This is a same-line heuristic only — multi-line JSX may not be caught. Manual review required.
**Reference**: https://www.w3.org/TR/WCAG22/#name-role-value

## Auto-Fix Guidance

### Safe to auto-fix

- **Hex colors / RGB / HSL in CSS files**: Replace literal value with a CSS custom property (`var(--color-...)`) if a matching token exists in the project's token file. If no token exists, flag for human review — do not invent token names.
- **Font-size px → rem**: Convert `font-size: Npx` to `font-size: calc(N / 16 * 1rem)` as a migration comment, then flag for the developer to map to the appropriate token.
- **tabindex > 0 → 0 or -1**: Replace `tabindex="N"` (N > 0) with `tabindex="0"` when the element needs to be in the natural tab order. Flag if the intent is unclear.
- **img missing alt**: Add `alt=""` as a placeholder and add a `TODO(a11y): add descriptive alt text` comment. Never invent alt text — flag for human review.

### Requires human review

- **Font-family literals**: Token mapping depends on the project's design system. Flag with the token name suggestion if the project has a `tokens/` directory.
- **Event handlers on non-interactive elements**: Refactoring to `<button>` / `<a>` may break layout or styling. Flag the element and the required fix; do not auto-rewrite.
- **aria-hidden on focusable elements**: Fixing this requires understanding the intent. Flag only.

### Never auto-fix

- **Brand colors**: Do not silently remap a hex value to a token — the color may be intentional and unmatched in the design system. Always flag for human review.
- **Accessibility roles**: Do not add or remove ARIA roles automatically.
