/**
 * DOM utility functions
 */

/**
 * Escape HTML special characters
 */
export function esc(s) {
  if (!s) return '';
  return s.replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

/**
 * Truncate string with ellipsis
 */
export function trunc(s, n = 300) {
  if (!s) return '';
  return s.length > n ? s.slice(0, n) + '…' : s;
}

/**
 * Check if element is scrolled near bottom
 */
export function isScrolledToBottom(el, threshold = 0.8) {
  if (!el) return false;
  const scrolled = el.scrollTop + el.clientHeight;
  const total = el.scrollHeight;
  return scrolled >= total * threshold;
}

/**
 * Auto-scroll element to bottom if already near bottom
 */
export function autoScrollIfNeeded(el) {
  if (el && isScrolledToBottom(el)) {
    el.scrollTop = el.scrollHeight;
  }
}
