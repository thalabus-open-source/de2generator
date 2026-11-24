/**
 * Logprobs feature: header toggle + confidence-based token colouring.
 *
 * The server owns the toggle state (it decides whether requests ask the LLM for
 * logprobs). The UI mirrors it via SSE events so every open tab stays in sync.
 *
 * Colour algorithm
 * ----------------
 * Each token carries `p`, the probability of the emitted token renormalised
 * over the candidate set the server returned (`--max-logprobs` many). Low p
 * means the model nearly chose something else, so such tokens are painted red
 * and confident ones get the palette's healthy end colour:
 *
 *   reasoning : red -> dark orange -> orange -> yellow -> light yellow -> blue
 *   generation: red -> dark orange -> orange -> yellow -> light yellow -> green
 */

export const LP = { enabled: false, unsupported: false };

/** Probability bands, upper bound exclusive. Index maps to .lp0 .. .lp5 */
const BANDS = [
  [0.20, 'lp0'],
  [0.40, 'lp1'],
  [0.60, 'lp2'],
  [0.80, 'lp3'],
  [0.95, 'lp4'],
  [1.01, 'lp5'],
];
const REASONING_SUFFIX = '-r';
const GENERATION_SUFFIX = '-g';

function bandClass(p, kind) {
  const suffix = kind === 'reasoning' ? REASONING_SUFFIX : GENERATION_SUFFIX;
  for (const [limit, cls] of BANDS) {
    if (p < limit) return cls + suffix;
  }
  return 'lp5' + suffix;
}

function escHtml(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

/**
 * Colourise a checkpoint's text using its per-token confidence data.
 * Falls back to plain escaped text whenever the token data cannot be trusted
 * (feature off, hidden content, translated block, no logprobs available).
 *
 * `textOverride` is used instead of `cp.text` for translated checkpoints,
 * whose token data still describes the original (untranslated) text.
 */
export function checkpointHtml(cp, textOverride) {
  const text = textOverride ?? cp.text ?? '';
  if (!LP.enabled) return escHtml(text);
  if (text.indexOf('OMITTED') !== -1) return escHtml(text);
  const lps = cp.token_lps;
  if (!Array.isArray(lps) || lps.length === 0) return escHtml(text);

  const joined = lps.map(e => e.t).join('');
  const a = text.trim();
  const b = joined.trim();
  // Guard against drift (rollback retries, omitted content, translation).
  if (a.slice(0, 40) !== b.slice(0, 40)) return escHtml(text);

  return lps.map(e => {
    const cls = bandClass(typeof e.p === 'number' ? e.p : 1, cp.kind === 'thinking' ? 'reasoning' : 'generation');
    const tip = Array.isArray(e.x) && e.x.length
      ? ` title="${escHtml('p=' + (e.p ?? 0).toFixed(3) + '  top: ' + e.x.map(([t, v]) => `${JSON.stringify(t)}=${v}`).join('  '))}"`
      : '';
    const style = e.m !== undefined && e.m < 0 ? ' lp-notop' : '';
    return `<span class="${cls}${style}"${tip}>${escHtml(e.t)}</span>`;
  }).join('');
}

/* ------------------------------------------------------------- header pill */

function pillEl() {
  return document.getElementById('lp-toggle');
}

export function renderPill() {
  const el = pillEl();
  if (!el) return;
  el.classList.remove('lp-off', 'lp-on', 'lp-err');
  if (LP.unsupported) {
    el.classList.add('lp-err');
    el.textContent = '● logprobs';
    el.title = 'This feature is not enabled in the LLM';
    return;
  }
  if (LP.enabled) {
    el.classList.add('lp-on');
    el.textContent = '◉ logprobs';
    el.title = 'Logprobs: enabled — requests ask the LLM for per-token confidence';
    return;
  }
  el.classList.add('lp-off');
  el.textContent = '◌ logprobs';
  el.title = 'Logprobs: disabled — click to enable per-token confidence';
}

/** Merge a state payload from config.json or an SSE event and repaint. */
export function applyState(state) {
  if (!state || typeof state !== 'object') return false;
  let changed = false;
  if (typeof state.enabled === 'boolean' && state.enabled !== LP.enabled) {
    LP.enabled = state.enabled;
    changed = true;
  }
  if (typeof state.unsupported === 'boolean' && state.unsupported !== LP.unsupported) {
    LP.unsupported = state.unsupported;
    changed = true;
  }
  renderPill();
  return changed;
}

/**
 * Pull the authoritative state from the server and repaint everything.
 * Called on every SSE (re)connect so the pill is never stale.
 */
export async function syncLogprobs() {
  try {
    const res = await fetch('/ui/logprobs');
    if (!res.ok) return;
    const changed = applyState(await res.json());
    // Repaint unconditionally: a restart may have dropped token data.
    rerenderCards?.();
    return changed;
  } catch (err) {
    console.error('logprobs sync failed', err);
  }
}

/** POST a new toggle value to the server, then repaint from its answer. */
export async function setEnabled(enabled) {
  try {
    const res = await fetch('/ui/logprobs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled }),
    });
    applyState(await res.json());
  } catch (err) {
    console.error('logprobs toggle failed', err);
  }
  // Repaint every card: colours appear/disappear instantly, even for old requests.
  rerenderCards?.();
}

/** Wire the click handler and repaint once. */
export function initLogprobs(state, rerender) {
  applyState(state);
  rerenderCards = rerender;
  const el = pillEl();
  if (el) el.addEventListener('click', () => setEnabled(!LP.enabled));
}

let rerenderCards = null;
