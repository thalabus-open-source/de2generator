/**
 * Theme management utilities
 */

/**
 * Get current theme
 */
export function getTheme() {
  return document.documentElement.getAttribute('data-theme') || 'dark';
}

/**
 * Toggle between dark and light theme
 */
export function toggleTheme() {
  const t = getTheme() === 'dark' ? 'light' : 'dark';
  document.documentElement.setAttribute('data-theme', t);
  document.getElementById('theme-btn').innerHTML = t === 'dark' ? '☼ Light' : '☾ Dark';
  localStorage.setItem('de2-theme', t);
}

/**
 * Initialize theme from localStorage
 */
export function initTheme() {
  const t = localStorage.getItem('de2-theme') || 'dark';
  document.documentElement.setAttribute('data-theme', t);
  const btn = document.getElementById('theme-btn');
  if (btn) {
    btn.innerHTML = t === 'dark' ? '☼ Light' : '☾ Dark';
  }
}
