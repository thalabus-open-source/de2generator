/**
 * Event handlers for SSE events
 */

import { store, emptyEl, panel } from '/ui/scripts/state.js';
import { renderCard, addCard, collapseAllDone, rerenderAll } from '/ui/scripts/rendering/card.js';
import { autoScrollIfNearBottom } from '/ui/scripts/utils/autoscroll.js';
import { applyState } from '/ui/scripts/utils/logprobs.js';
import { renderTotalsPill } from '/ui/scripts/utils/totals.js';

/**
 * Refresh the header pill with the number of in-progress sessions
 */
export function updateLivePill() {
  const el = document.getElementById('live-pill');
  if (!el) return;
  let n = 0;
  for (const d of store.values()) if (d.status === 'in_progress') n++;
  el.textContent = `● ${n} live`;
  el.className = n > 0 ? 'active' : 'idle';
}

/**
 * Handle incoming SSE event
 */
export function handleEvent(ev) {
  handleEventInner(ev);
  updateLivePill();
}

function handleEventInner(ev) {
  const t = ev.type;
  
  // Logprobs toggle / unsupported notifications: mirror the state and repaint
  // every card so colours appear or vanish immediately.
  if (t === 'logprobs_setting' || t === 'logprobs_unsupported') {
    applyState(ev);
    rerenderAll();
    return;
  }
  
  // Accumulated token totals (header pill)
  if (t === 'token_totals') {
    renderTotalsPill(ev.totals);
    return;
  }
  
  if (t === 'history') {
    for (const d of (ev.interactions || [])) {
      d.expanded = (d.status === 'in_progress');
      d.llmInExpanded = false;
      
      // Extract thinking char count from checkpoints if not set
      if (!d.thinking_char_count && d.checkpoints) {
        for (const cp of d.checkpoints) {
          const label = cp.label || '';
          if (label.includes('thinking') && label.includes('CHAR-')) {
            const match = label.match(/CHAR-\s*(\d+)/);
            if (match) {
              d.thinking_char_count = parseInt(match[1]);
              break;
            }
          }
        }
      }
      
      store.set(d.id, d);
      if (!document.getElementById('card-' + d.id)) {
        addCard(d);
      }
      renderCard(d);
    }
    if (store.size > 0 && emptyEl.parentNode) {
      emptyEl.remove();
    }
    return;
  }
  
  if (t === 'interaction_start') {
    const d = ev.interaction;
    d.expanded = true;
    d.llmInExpanded = false;
    store.set(d.id, d);
    addCard(d);
    renderCard(d);
    collapseAllDone(d.id);
    autoScrollIfNearBottom(panel, 250);
    return;
  }
  
  if (t === 'checkpoint') {
    const d = store.get(ev.request_id);
    if (!d) return;
    
    d.checkpoints.push(ev.checkpoint);
    
    // Extract thinking char count from label
    const label = ev.checkpoint.label || '';
    if (label.includes('thinking') && label.includes('CHAR-')) {
      const match = label.match(/CHAR-\s*(\d+)/);
      if (match) {
        d.thinking_char_count = parseInt(match[1]);
      }
    }
    
    if (d.expanded) {
      renderCard(d);
      setTimeout(() => {
        const cps = document.querySelector('#card-' + d.id + ' .checkpoints');
        if (cps) cps.scrollTop = cps.scrollHeight;
      }, 0);
    }
    autoScrollIfNearBottom(panel, 250);
    return;
  }
  
  if (t === 'stats_update') {
    const d = store.get(ev.request_id);
    if (!d) return;
    d.stats = Object.assign(d.stats || {}, ev.stats);
    renderCard(d);
    autoScrollIfNearBottom(panel, 250);
    return;
  }
  
  if (t === 'interaction_complete') {
    const d = store.get(ev.request_id);
    if (!d) return;
    d.status = ev.status || 'complete';
    d.final_content = ev.final_content;
    d.final_reasoning = ev.final_reasoning;
    d.stats = ev.stats || {};
    d.log_file_path = ev.log_file_path || '';
    renderCard(d);
    autoScrollIfNearBottom(panel, 250);
    return;
  }
  
  if (t === 'relabel_last_checkpoint') {
    const d = store.get(ev.request_id);
    if (!d) return;
    if (d.checkpoints && d.checkpoints.length > 0) {
      d.checkpoints[d.checkpoints.length - 1].label = ev.label;
    }
    if (d.expanded) {
      renderCard(d);
      setTimeout(() => {
        const cps = document.querySelector('#card-' + d.id + ' .checkpoints');
        if (cps) cps.scrollTop = cps.scrollHeight;
      }, 0);
    }
    autoScrollIfNearBottom(panel, 250);
    return;
  }
  
  if (t === 'checkpoint_semantic_update') {
    const d = store.get(ev.request_id);
    if (!d) return;
    if (d.checkpoints && ev.cp_idx >= 0 && ev.cp_idx < d.checkpoints.length) {
      d.checkpoints[ev.cp_idx].semantic_verdict = ev.verdict;
      if (ev.reason) {
        d.checkpoints[ev.cp_idx].semantic_reason = ev.reason;
      }
      if (ev.full_response) {
        d.checkpoints[ev.cp_idx].semantic_full_response = ev.full_response;
      }
    }
    if (d.expanded) {
      renderCard(d);
    }
    autoScrollIfNearBottom(panel, 250);
    return;
  }
  
  if (t === 'checkpoint_translation_update') {
    const d = store.get(ev.request_id);
    if (!d) return;
    if (d.checkpoints && ev.cp_idx >= 0 && ev.cp_idx < d.checkpoints.length) {
      const cp = d.checkpoints[ev.cp_idx];
      // Preserve original text before overwriting with translated
      if (!cp.original_text) {
        cp.original_text = cp.text;
      }
      cp.text = ev.translated_text;
      cp.translation_language = ev.language;
      cp.translation_elapsed_ms = ev.elapsed_ms;
    }
    if (d.expanded) {
      renderCard(d);
    }
    autoScrollIfNearBottom(panel, 250);
    return;
  }
}
