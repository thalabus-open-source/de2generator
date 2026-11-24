/**
 * Main entry point for de²generator UI
 */

import { loadConfig, CONFIG } from '/ui/scripts/config.js';
import { initTheme, toggleTheme } from '/ui/scripts/utils/theme.js';
import { initTranslationSelector, updateTranslation } from '/ui/scripts/utils/translation.js';
import { initLogprobs } from '/ui/scripts/utils/logprobs.js';
import { toggleCard, toggleLlmIn, toggleLlmInRow, clearDone, rerenderAll } from '/ui/scripts/rendering/card.js';
import { connect } from '/ui/scripts/connection.js';
import { initTotals } from '/ui/scripts/utils/totals.js';

// Initialize theme
initTheme();

// Restore the accumulated token totals pill (persists server-side across reloads)
initTotals();

// Load configuration and check if UI is enabled
loadConfig().then(() => {
  // Initialize translation selector
  initTranslationSelector(CONFIG);

  // Initialize the logprobs toggle (state lives on the server, default disabled)
  initLogprobs(CONFIG.logprobs, rerenderAll);
  
  // Check if UI is disabled
  if (CONFIG.ui && CONFIG.ui.enabled === false) {
    // Show disabled message
    const disabledMsg = document.getElementById('ui-disabled-message');
    if (disabledMsg) {
      disabledMsg.style.display = 'block';
    }
    // Disable clear button
    const clearBtn = document.getElementById('clear-btn');
    if (clearBtn) {
      clearBtn.style.pointerEvents = 'none';
      clearBtn.style.opacity = '0.5';
      clearBtn.style.cursor = 'not-allowed';
    }
    // Don't connect to SSE if UI is disabled
    return;
  }
  
  // UI is enabled, proceed with normal connection
  connect();
});

// Expose functions to global scope for onclick handlers
window.toggleTheme = toggleTheme;
window.toggleCard = toggleCard;
window.toggleLlmIn = toggleLlmIn;
window.toggleLlmInRow = toggleLlmInRow;
window.clearDone = clearDone;
window.updateTranslation = updateTranslation;
