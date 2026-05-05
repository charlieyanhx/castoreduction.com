// Generic job poller — reusable across all panels
import { get } from './api.js';

/**
 * Poll a job until complete or error.
 * @param {string} jobId
 * @param {object} callbacks
 * @param {function} callbacks.onTick({state, elapsed_s})
 * @param {function} callbacks.onDone(result)
 * @param {function} callbacks.onError(errorString)
 * @param {number} [interval=1200] ms between polls
 */
export async function pollJob(jobId, { onTick, onDone, onError, interval = 1200 }) {
  const start = Date.now();
  while (true) {
    try {
      const j = await get(`/jobs/${jobId}`);
      const elapsed_s = Math.floor((Date.now() - start) / 1000);
      if (j.state === 'complete') {
        onDone(j.result);
        return j.result;
      }
      if (j.state === 'error') {
        onError(j.error || 'unknown error');
        return null;
      }
      onTick({ state: j.state, elapsed_s });
    } catch (e) {
      onError(e.message);
      return null;
    }
    await new Promise(r => setTimeout(r, interval));
  }
}
