/**
 * Card rendering functions
 */

import { THINKING_CHARS_PER_TOKEN } from '/ui/scripts/config.js';
import { panel, emptyEl, store } from '/ui/scripts/state.js';
import { checkpointHtml } from '/ui/scripts/utils/logprobs.js';
import { esc, autoScrollIfNeeded } from '/ui/scripts/utils/dom.js';
import { tokenPillHtml, resetTotals } from '/ui/scripts/utils/totals.js';

function prefillStatus(d) {
  if (d.stats?.ttft_seconds != null) {
    const ttft = d.stats.ttft_seconds.toFixed(1);
    const elapsed = d.stats.elapsed_total_seconds != null
      ? d.stats.elapsed_total_seconds.toFixed(1)
      : null;
    const tooltip = elapsed
      ? `Time to first token: ${ttft}s\nTotal elapsed time: ${elapsed}s`
      : `Time to first token: ${ttft}s`;
    const value = elapsed
      ? `<span class="ttft-value">${ttft}s</span> <span class="elapsed-value">${elapsed}s</span>`
      : `<span class="ttft-value">${ttft}s</span>`;
    return `<span class="token-display ttft-badge" title="${tooltip}">${value}</span>`;
  }
  const elapsed = Math.max(0, Math.floor(Date.now() / 1000 - (d.started_at || Date.now() / 1000)));
  return `<div class="prefill-status" data-started-at="${d.started_at || ''}">Pre-fill in progress... (${elapsed} seconds)</div>`;
}

setInterval(() => {
  document.querySelectorAll('.prefill-status').forEach(el => {
    const startedAt = Number(el.dataset.startedAt);
    if (startedAt) {
      el.textContent = `Pre-fill in progress... (${Math.max(0, Math.floor(Date.now() / 1000 - startedAt))} seconds)`;
    }
  });
}, 1000);

/**
 * Toggle card expanded/collapsed state
 */
export function toggleCard(id, e) {
  if (e) e.stopPropagation();
  const d = store.get(id);
  if (!d) return;
  d.expanded = !d.expanded;
  renderCard(d);
}

export function toggleLlmIn(id, e) {
  if (e) e.stopPropagation();
  const d = store.get(id);
  if (!d) return;
  d.llmInExpanded = !d.llmInExpanded;
  renderCard(d);
}

export function toggleLlmInRow(id, rowIndex, e) {
  if (e) e.stopPropagation();
  const d = store.get(id);
  if (!d) return;
  d.llmInRows = d.llmInRows || {};
  d.llmInRows[rowIndex] = !d.llmInRows[rowIndex];
  renderCard(d);
}

/**
 * Collapse all completed cards except the specified one
 */
export function collapseAllDone(exceptId) {
  for (const [id, d] of store) {
    if (id !== exceptId && d.status !== 'in_progress' && d.expanded) {
      d.expanded = false;
      renderCard(d);
    }
  }
}

/**
 * Clear all completed interactions
 */
export function clearDone() {
  const toDelete = [];
  for (const [id, d] of store) {
    if (d.status !== 'in_progress') {
      toDelete.push(id);
    }
  }
  for (const id of toDelete) {
    store.delete(id);
    const el = document.getElementById('card-' + id);
    if (el) el.remove();
  }
  if (store.size === 0) {
    if (emptyEl.parentNode) emptyEl.remove();
    panel.appendChild(emptyEl);
  }
  // Pressing "clear" also resets the accumulated token totals pill
  resetTotals();
}

/**
 * Re-render every interaction currently held in the store.
 * Used when the logprobs toggle changes and existing cards must be repainted.
 */
export function rerenderAll() {
  for (const d of store.values()) renderCard(d);
}

/**
 * Render a card's content
 */
export function renderCard(d) {
  const el = document.getElementById('card-' + d.id);
  if (!el) return;
  
  el.className = 'card' + (d.status === 'in_progress' ? ' live' : '');
  const hdr = el.querySelector('.card-hdr');
  
  // Build thinking badge
  let thinkingBadge = '';
  if (d.thinking_budget === null || d.thinking_budget === undefined) {
    if (d.enable_thinking === false) {
      thinkingBadge = '';
    } else {
      thinkingBadge = `<span class="thinking-badge unlimited-thinking">∞ THINKING</span>`;
    }
  } else if (d.thinking_budget > 0) {
    let displayValue = '';
    let displayFormat = '';
    
    if (d.status === 'in_progress' && d.thinking_char_count !== undefined) {
      // Use same ratio as backend detection for accuracy
      const estimatedTokens = Math.ceil(d.thinking_char_count / THINKING_CHARS_PER_TOKEN);
      const pct = Math.round((estimatedTokens / d.thinking_budget) * 100);
      displayValue = `~${estimatedTokens}<span class="token-value">t</span> ${pct}%`;
      displayFormat = '';
    } else if (d.status !== 'in_progress' && d.stats) {
      let thinkingTok = d.stats.thinking_tokens || 0;
      const contentTok = d.stats.output_tokens || 0;
      
      if (thinkingTok === 0 && d.thinking_char_count !== undefined) {
        thinkingTok = Math.ceil(d.thinking_char_count / THINKING_CHARS_PER_TOKEN);
        const totalTok = thinkingTok + contentTok;
        if (totalTok > 0) {
          const thinkPct = Math.round((thinkingTok / totalTok) * 100);
          displayValue = `~${thinkingTok}<span class="token-value">t</span> ${thinkPct}%`;
        } else {
          displayValue = `~${thinkingTok}<span class="token-value">t</span> est`;
        }
      } else {
        const totalTok = thinkingTok + contentTok;
        if (totalTok > 0) {
          const thinkPct = Math.round((thinkingTok / totalTok) * 100);
          displayValue = `${thinkingTok}<span class="token-value">t</span> ${thinkPct}%`;
        } else {
          displayValue = '0<span class="token-value">t</span>';
        }
      }
      displayFormat = '';
    } else if (d.thinking_char_count !== undefined) {
      displayValue = Math.ceil(d.thinking_char_count / THINKING_CHARS_PER_TOKEN) + '<span class="token-value">t</span>';
      displayFormat = '';
    } else {
      displayValue = '0<span class="token-value">t</span>';
      displayFormat = '';
    }
    const thinkingTokenValue = displayValue.replace(/<[^>]*>/g, '').split(' ')[0].replace('~', '');
    const thinkingTokens = `~${thinkingTokenValue}`;
    const thinkingSeconds = d.stats?.thinking_phase_seconds != null
      ? d.stats.thinking_phase_seconds.toFixed(1)
      : '0.0';
    const thinkingTooltip = `Thinking tokens (approximate): ${thinkingTokenValue}\nElapsed time to think: ${thinkingSeconds}s`;
    thinkingBadge = `<span class="thinking-badge has-thinking" title="${thinkingTooltip}">${thinkingTokens} ${thinkingSeconds}s</span>`;
  }
  
  // Token pill: "xxt [yyt] zzt" — input, estimated reasoning (if any),
  // estimated answer (or total output when there is no reasoning).
  const s = d.stats || {};
  const inpTok = s.input_tokens ?? 0;
  const reaTok = s.reasoning_tokens_est ?? 0;
  const ansTok = s.answer_tokens_est ?? 0;
  const outTok = s.total_output_tokens
    ?? ((s.output_tokens || 0) + (s.thinking_tokens || 0));
  const outputDisplay = (outTok > 0 || inpTok > 0 || reaTok > 0)
    ? tokenPillHtml(inpTok, reaTok, ansTok, outTok)
    : '';
  const ttftBadge = d.stats?.ttft_seconds !== undefined ? prefillStatus(d) : '';
  
  const rejectionCount = d.stats?.rejected_checkpoints_count || 0;
  const rejectionBadgeHeader = rejectionCount > 0
    ? `<span class="rejection-count-badge">${rejectionCount} rejection${rejectionCount !== 1 ? 's' : ''}</span>`
    : '';
  
  // Transaction metadata (agent name, goal ID)
  let transactionMetadata = '';
  if (d.agent_name || d.goal_id) {
    const goalId = d.goal_id ? `<span class="goal-id">${esc(d.goal_id)}</span>` : '';
    const agentName = d.agent_name ? `<span class="agent-name">${esc(d.agent_name)}</span>` : '';
    transactionMetadata = `${goalId}${agentName}`;
  }
  
  // Build header
  // Calculate total translation time if available
  let translationSummaryBadge = '';
  if (d.checkpoints && d.checkpoints.some(cp => cp.translation_language)) {
    const totalTranslationMs = d.checkpoints
      .filter(cp => cp.translation_elapsed_ms)
      .reduce((sum, cp) => sum + (cp.translation_elapsed_ms || 0), 0);
    const translationLang = d.checkpoints.find(cp => cp.translation_language)?.translation_language || '';
    const translationSeconds = (totalTranslationMs / 1000).toFixed(1);
    translationSummaryBadge = `<span class="translation-summary-badge" title="Translation: ${translationLang} (${translationSeconds}s total)">${translationLang}</span>`;
  }
  
  hdr.innerHTML = `<span class="card-num">#${d.number}</span>`
    + `<span class="card-model">${esc(d.model || '')}</span>`
    + `<span class="transaction-metadata">${transactionMetadata || ''}</span>`
    + `<div class="card-right-items">${thinkingBadge}${rejectionBadgeHeader}${ttftBadge}${outputDisplay}${translationSummaryBadge}`
    + `<span class="badge ${d.status === 'in_progress' ? 'live' : 'done'}">${d.status === 'in_progress' ? 'live' : d.status}</span>`
    + `<button class="toggle-btn" onclick="window.toggleCard('${d.id}',event)">${d.expanded ? '▲ collapse' : '▼ expand'}</button></div>`;
  
  const body = el.querySelector('.card-body');
  
  if (d.expanded) {
    // Expanded view: show checkpoints
    let html = d.checkpoints.map(cp => {
      let statusBadge = '';
      if (cp.kind === 'ok') statusBadge = `<span class="cp-status ok-status">✓ OK</span>`;
      else if (cp.kind === 'reject') statusBadge = `<span class="cp-status reject-status">⊘ REJECTED</span>`;
      else if (cp.kind === 'error') statusBadge = `<span class="cp-status error-status">✕ ERROR</span>`;
      
      // Add translation badge if available
      let translationBadge = '';
      if (cp.translation_language && cp.translation_elapsed_ms !== undefined) {
        const elapsedSec = (cp.translation_elapsed_ms / 1000).toFixed(2);
        translationBadge = `<span class="translation-badge" title="Translation: ${cp.translation_language} (${elapsedSec}s)">${cp.translation_language}</span>`;
      }
      
      // Add entropy badge if available
      let entropyBadge = '';
      if (cp.entropy !== null && cp.entropy !== undefined) {
        const entropyVal = cp.entropy.toFixed(2);
        let entropyClass = 'entropy-healthy';
        if (cp.entropy < 3.5 || cp.entropy > 7.0) {
          entropyClass = 'entropy-warning';
        }
        entropyBadge = `<span class="entropy-badge ${entropyClass}">entropy: ${entropyVal}</span>`;
      }
      
      // Add semantic badge if available
      let semanticBadge = '';
      if (cp.semantic_verdict) {
        const sv = cp.semantic_verdict;
        let svStatus, svClass, badgeText;
        if (sv === 'llm_unavailable') {
          svStatus = 'OFF';
          svClass = 'semantic-off';
          badgeText = 'OFF';
        } else {
          svStatus = sv === 'yes' ? 'OK' : sv === 'no' ? 'DETECTED' : sv === 'pending' ? 'PENDING' : 'UNCLEAR';
          svClass = sv === 'yes' ? 'semantic-ok' : sv === 'no' ? 'semantic-no' : sv === 'pending' ? 'semantic-pending' : 'semantic-unclear';
          badgeText = `semantic: ${svStatus}`;
        }
        const tooltipText = cp.semantic_full_response ? esc(cp.semantic_full_response) : '';
        semanticBadge = `<span class="semantic-badge ${svClass}" ${tooltipText ? `title="${tooltipText}"` : ''} >${badgeText}</span>`;
      }
      
      // If translated, show both original (green) and translated (purple) blocks
      if (cp.original_text && cp.translation_language) {
        return `<div class="cp ${cp.kind}"><div class="cp-hdr"><div class="cpl">${esc(cp.label)}</div><div class="cp-badges">${semanticBadge}${entropyBadge}${statusBadge}</div></div>${checkpointHtml(cp, cp.original_text)}</div>`
          + `<div class="cp translated"><div class="cp-hdr"><div class="cpl">Translated (${cp.translation_language})</div><div class="cp-badges">${translationBadge}</div></div>${esc(cp.text)}</div>`;
      } else {
        return `<div class="cp ${cp.kind}"><div class="cp-hdr"><div class="cpl">${esc(cp.label)}</div><div class="cp-badges">${semanticBadge}${entropyBadge}${statusBadge}</div></div>${checkpointHtml(cp)}</div>`;
      }
    }).join('');
    
    if (d.status === 'in_progress') {
      html += `<div class="cp spinner-row"><div class="spin"></div>generating…</div>`;
    }
    
    const clientRequest = d.client_request || [];
    const llmInToggle = `<button class="toggle-btn llm-in-toggle" onclick="window.toggleLlmIn('${d.id}',event)">${d.llmInExpanded ? '▲ collapse' : '▼ expand'}</button>`;
    d.llmInRows = d.llmInRows || {};
    const llmInRows = clientRequest.map((message, rowIndex) => {
      const role = message && typeof message === 'object' ? message.role || '' : '';
      const rawContent = message && typeof message === 'object' ? message.content : message;
      const content = typeof rawContent === 'string'
        ? rawContent
        : JSON.stringify(rawContent ?? '', null, 2);
      const isLong = content.length > 250;
      const isExpanded = Boolean(d.llmInRows[rowIndex]);
      const displayedContent = isLong && !isExpanded ? content.slice(0, 250) : content;
      const rowToggle = isLong
        ? `<button class="llm-in-row-toggle${isExpanded ? ' expanded' : ''}" onclick="window.toggleLlmInRow('${d.id}',${rowIndex},event)">${isExpanded ? 'collapse' : '...'}</button>`
        : '';
      return `<tr><th scope="row">${esc(String(role))}</th><td><span class="llm-in-row-content">${esc(displayedContent)}</span>${rowToggle}</td></tr>`;
    }).join('');
    const llmInHtml = clientRequest.length > 0
      ? `<div class="llm-in-box"><div class="llm-in-header"><span>LLM-IN</span>${llmInToggle}</div>${d.llmInExpanded ? `<table class="llm-in-table"><thead><tr><th scope="col">Role</th><th scope="col">Content</th></tr></thead><tbody>${llmInRows}</tbody></table>` : ''}</div>`
      : '';

    // Model parameters display
    let paramsHtml = '';
    if (d.model_parameters) {
      const params = d.model_parameters;
      const paramEntries = Object.entries(params)
        .filter(([k, v]) => v !== null && v !== undefined && v !== '')
        .map(([k, v]) => `<div style="font-size:.85rem;line-height:1.4"><span style="color:var(--text-dim)">${esc(k)}:</span> <span style="font-family:monospace">${typeof v === 'number' ? v.toString() : esc(v.toString())}</span></div>`)
        .join('');
      
      if (paramEntries) {
        paramsHtml = `<div style="padding:10px;background:var(--bg-card-hdr);border-radius:4px;margin-bottom:10px;border-left:3px solid var(--border)">${paramEntries}</div>`;
      }
    }
    
    const prefill = d.status === 'in_progress' && d.stats?.ttft_seconds === undefined ? prefillStatus(d) : '';
    body.innerHTML = `${paramsHtml}${llmInHtml}${prefill}<div class="checkpoints">${html || '<div style="padding:10px;color:var(--text-dim);font-size:.78rem">Starting...</div>'}</div>`;
    const cps = body.querySelector('.checkpoints');
    if (cps) autoScrollIfNeeded(cps);
  } else {
    // Collapsed view: show summary
    const stats = d.stats || {};
    const contentStats = `<div class="content-stats">Generated: ${stats.output_tokens || 0}t  |  TPS: ${(stats.tokens_per_second || 0).toFixed(1)}t/s  |  Elapsed: ${(stats.elapsed_total_seconds || 0).toFixed(1)}s  |  Rejected: ${stats.rejected_checkpoints_count || 0}  |  Rej.Tokens: ${stats.rejected_checkpoints_tokens || 0}t</div>`;
    
    // Check if we have translated content (show translated in purple if available)
    let hasTranslation = d.checkpoints && d.checkpoints.some(cp => cp.translation_language);
    let contentDisplay, contentBlock;
    
    if (hasTranslation) {
      // Get translated content from checkpoints that are 'ok' kind
      const translatedContent = d.checkpoints
        .filter(cp => cp.kind === 'ok' && cp.translation_language)
        .map(cp => cp.text)
        .join('');
      contentDisplay = translatedContent ? esc(translatedContent.trim()) : '<span style="opacity:.3">—</span>';
      contentBlock = 'st';  // translated style (purple)
    } else {
      contentDisplay = d.final_content ? esc(d.final_content.trim()) : '<span style="opacity:.3">—</span>';
      contentBlock = 'sc';  // content style (green)
    }
    
    const cv = `<div class="sum-block ${contentBlock}"><div class="lbl-row"><span class="lbl">Content${hasTranslation ? ' (Translated)' : ''}</span></div><div style="margin-bottom:8px">${contentDisplay}</div>${contentStats}</div>`;
    
    // Check for thinking rejection
    let thinkingRejected = false;
    if (d.checkpoints && d.checkpoints.length > 0) {
      for (const cp of d.checkpoints) {
        if (cp.kind === 'reject' && (cp.label.includes('thinking disabled') || cp.label.includes('thinking budget exhausted'))) {
          thinkingRejected = true;
          break;
        }
      }
    }
    
    let rejectionBadge = thinkingRejected ? `<span class="sum-status reject-badge">⊘ REJECTED</span>` : '';
    if (thinkingRejected && d.log_file_path) {
      rejectionBadge += `&nbsp;<a href="/logs/${d.log_file_path}" target="_blank" style="color:var(--accent);text-decoration:underline;font-size:0.85rem">📋 view log</a>`;
    }
    
    let thinkingTokenValue = stats.thinking_tokens || 0;
    if (thinkingTokenValue === 0 && d.thinking_char_count !== undefined) {
      thinkingTokenValue = Math.ceil(d.thinking_char_count / THINKING_CHARS_PER_TOKEN);
      const rv = `<div class="sum-block sr"><div class="lbl-row"><span class="lbl">Reasoning</span>${rejectionBadge}<span class="stat-inline">Thinking ~${thinkingTokenValue}t est</span></div>${d.final_reasoning ? esc(d.final_reasoning.trim()) : '<span style="opacity:.3">—</span>'}</div>`;
      body.innerHTML = `<div class="summary"><div class="sum-row">${rv}${cv}</div></div>`;
    } else {
      const rv = `<div class="sum-block sr"><div class="lbl-row"><span class="lbl">Reasoning</span>${rejectionBadge}<span class="stat-inline">Thinking ${thinkingTokenValue}t</span></div>${d.final_reasoning ? esc(d.final_reasoning.trim()) : '<span style="opacity:.3">—</span>'}</div>`;
      body.innerHTML = `<div class="summary"><div class="sum-row">${rv}${cv}</div></div>`;
    }
  }
}

/**
 * Add a new card to the panel
 */
export function addCard(d) {
  const el = document.createElement('div');
  el.id = 'card-' + d.id;
  el.className = 'card';
  el.innerHTML = '<div class="card-hdr"></div><div class="card-body"></div>';
  
  el.querySelector('.card-hdr').addEventListener('click', e => {
    if (!e.target.classList.contains('toggle-btn')) {
      toggleCard(d.id, e);
    }
  });
  
  if (emptyEl.parentNode) emptyEl.remove();
  panel.appendChild(el);
}
