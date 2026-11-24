/**
 * SSE connection management
 */

import { store, connEl } from '/ui/scripts/state.js';
import { handleEvent } from '/ui/scripts/events/handlers.js';
import { syncLogprobs } from '/ui/scripts/utils/logprobs.js';

let es;

/**
 * Connect to SSE endpoint
 */
export function connect() {
  es = new EventSource('/ui/events');
  
  es.onopen = () => {
    connEl.innerHTML = '• connected';
    connEl.className = 'ok';
    // The server owns the logprobs state: re-read it on every (re)connect so the
    // pill cannot drift after a restart or when another tab changes it.
    syncLogprobs();
  };
  
  es.onmessage = e => {
    try {
      handleEvent(JSON.parse(e.data));
    } catch (err) {
      console.error('SSE parse error', err, e.data);
    }
  };
  
  es.onerror = () => {
    connEl.innerHTML = '• disconnected';
    connEl.className = 'err';
    es.close();
    setTimeout(connect, 3000);
  };
}
