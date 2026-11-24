/**
 * Auto-scroll utilities for the main panel
 */

/**
 * Check if user is near the bottom of the panel (within threshold)
 */
export function isNearBottom(el, threshold = 250) {
  if (!el) return false;
  const distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
  return distFromBottom < threshold;
}

/**
 * Scroll panel to bottom if user is already near the bottom
 */
export function autoScrollIfNearBottom(el, threshold = 250) {
  if (!el) return;
  if (isNearBottom(el, threshold)) {
    el.scrollTop = el.scrollHeight;
  }
}
