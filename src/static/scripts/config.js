/**
 * Configuration management for de²generator UI
 */

// Default thinking token estimation (will be updated from config)
export let THINKING_CHARS_PER_TOKEN = 4;
export let CONFIG = {};

/**
 * Load configuration from backend
 */
export function loadConfig() {
  return fetch('/ui/config.json')
    .then(r => r.json())
    .then(cfg => {
      CONFIG = cfg;
      if (cfg.thinking && cfg.thinking.chars_per_token) {
        THINKING_CHARS_PER_TOKEN = cfg.thinking.chars_per_token;
        console.log('Loaded config: THINKING_CHARS_PER_TOKEN =', THINKING_CHARS_PER_TOKEN);
      }
      return cfg;
    })
    .catch(e => {
      console.warn('Could not load config.json:', e);
      return null;
    });
}
