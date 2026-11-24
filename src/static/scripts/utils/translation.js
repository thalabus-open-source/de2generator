/**
 * Translation management utilities
 */

/**
 * Initialize translation selector from config
 */
export function initTranslationSelector(config) {
  const selector = document.getElementById('translation-selector');
  if (!selector) return;
  
  const translation = config.translation || {};
  const isEnabled = translation.enabled || false;
  const thalabus = translation.thalabus || {};
  const language = thalabus.ui_language || 'disabled';
  
  if (isEnabled && language && language !== 'disabled') {
    selector.value = language;
  } else {
    selector.value = 'disabled';
  }
  
  // Apply styling based on current value
  updateTranslationStyling(selector);
}

/**
 * Update translation styling based on selected value
 */
function updateTranslationStyling(selector) {
  if (selector.value === 'disabled') {
    selector.classList.remove('enabled');
  } else {
    selector.classList.add('enabled');
  }
}

/**
 * Update translation configuration
 */
export function updateTranslation(value) {
  const selector = document.getElementById('translation-selector');
  if (!selector) return;
  
  // Update styling immediately
  updateTranslationStyling(selector);
  
  // Send update to backend
  const payload = {
    enabled: value !== 'disabled',
    language: value !== 'disabled' ? value : null
  };
  
  fetch('/ui/update-translation', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(payload)
  })
  .then(r => {
    if (!r.ok) {
      console.error('Failed to update translation settings');
      // Reset selector to previous value
      const config = window.CONFIG || {};
      initTranslationSelector(config);
    }
  })
  .catch(e => {
    console.error('Error updating translation:', e);
    // Reset selector to previous value
    const config = window.CONFIG || {};
    initTranslationSelector(config);
  });
}
