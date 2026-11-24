/**
 * Token accounting: per-card pill formatting, accumulated totals pill,
 * and the link to the hourly statistics page.
 *
 * Pill format:  xxt yyt zzt
 *   xx = input tokens
 *   yy = estimated reasoning tokens (blue, shown only when > 0)
 *   zz = estimated answer tokens, or total output tokens when there is no reasoning
 */

export function tokenTripleHtml(input, reasoningEst, answerEst, totalOutput, options = {}) {
  const t = v => {
    const rounded = Math.max(0, Math.round(v) || 0);
    const value = options.groupThousands ? rounded.toLocaleString('en-US') : rounded;
    return `${value}<span class="token-value">t</span>`;
  };
  const inp = input || 0;
  const rea = reasoningEst || 0;
  const ans = answerEst || 0;
  const out = totalOutput || 0;
  const inputTokens = options.styleInput
    ? `<span class="tok-input">${t(inp)}</span>`
    : t(inp);
  if (out <= 0 && rea <= 0) return inputTokens;
  const parts = [inputTokens];
  if (rea > 0) parts.push(`<span class="tok-reasoning">${t(rea)}</span>`);
  parts.push(t(rea > 0 ? ans : out));
  return parts.join(' ');
}

export function tokenPillTooltip(input, reasoningEst, answerEst, totalOutput) {
  return `Input tokens: ${input || 0}` +
    ` | Reasoning tokens (est): ${reasoningEst || 0}` +
    ` | Answer tokens (est): ${answerEst || 0}` +
    ` | Total output tokens: ${totalOutput || 0}`;
}

export function tokenPillHtml(input, reasoningEst, answerEst, totalOutput) {
  return `<span class="token-display token-triple" title="${tokenPillTooltip(input, reasoningEst, answerEst, totalOutput)}">${tokenTripleHtml(input, reasoningEst, answerEst, totalOutput)}</span>`;
}

const TOTALS_TOOLTIP = 'Accumulated tokens since last clear (input, reasoning est., answer est.). Click to open hourly statistics (last 240 hours).';

export function renderTotalsPill(totals) {
  const el = document.getElementById('totals-pill');
  if (!el || !totals) return;
  const inp = totals.input || 0;
  const rea = totals.reasoning || 0;
  const ans = totals.answer || 0;
  el.innerHTML = tokenTripleHtml(inp, rea, ans, rea + ans, {
    groupThousands: true,
    styleInput: true
  });
  el.title = TOTALS_TOOLTIP;
}

/**
 * Initialise the header totals pill: restore persisted totals, wire up the
 * click-through to the hourly statistics page and the descriptive tooltip.
 */
export async function initTotals() {
  const el = document.getElementById('totals-pill');
  if (!el) return;
  el.title = TOTALS_TOOLTIP;
  el.style.cursor = 'pointer';
  el.addEventListener('click', () => window.open('/ui/stats', '_blank'));
  try {
    const resp = await fetch('/ui/stats/totals');
    renderTotalsPill(await resp.json());
  } catch (err) {
    console.error('Could not load token totals', err);
  }
}

/**
 * Reset accumulated totals on the server (called when the user presses "clear").
 * The server broadcasts the zeroed totals to every connected UI client.
 */
export async function resetTotals() {
  try {
    const resp = await fetch('/ui/stats/reset-totals', { method: 'POST' });
    renderTotalsPill(await resp.json());
  } catch (err) {
    console.error('Could not reset token totals', err);
  }
}
