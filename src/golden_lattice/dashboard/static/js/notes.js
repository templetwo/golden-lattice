// Pure page decisions: which note a section says, which filter shows, how a
// parity line reads. No DOM. Tested with `node --test tests/js`.
import { esc, READING_WORDS, DIMENSION_WORDS } from './util.js';
import { labelOf, colorOf } from './seats.js';

const seatName = (id) => `<span class="seat-name" style="color:${colorOf(id)}">${esc(labelOf(id))}</span>`;

// The disputes table's effective filter and the one note it carries.
// A live run knows per-pair counts during cross-reading but not which claim
// each dispute names, so it must never say nobody disputed (review, Important 2).
export function disputesView({ live, complete, readingsIn, filter, stats, claims }) {
  if (!readingsIn) return { filter: filter === 'disputed' ? 'all' : filter, note: 'readers have not cross-read yet' };
  const reasonsKnown = (claims || []).some((c) => (c.disagreements || []).length);
  if (live && !complete && !reasonsKnown) {
    return { filter: filter === 'disputed' && !(stats || {}).disputed ? 'all' : filter,
      note: "readers' per-claim reasons arrive when the run completes" };
  }
  if (!(stats || {}).disputed) return { filter: 'all', note: 'no reader disputed anything in this run' };
  return { filter, note: null };
}

export function feedSkippedText(live) {
  return live ? 'No investigation events yet.' : 'No Phase 0 record in this file.';
}

export function openingStep(B) { return B.N; }

export function critiquesCaption(stats) {
  const all = (stats || {}).critiques || 0;
  const on = (stats || {}).critiques_attached != null ? stats.critiques_attached : all;
  const lead = on === 1 ? '1 critique is on its claim in 02' : `${on} critiques are on their claims in 02`;
  const off = all - on;
  if (!off) return `${lead}.`;
  return `${lead}; ${off} target${off === 1 ? 's' : ''} no Phase 1 claim and ${off === 1 ? 'is' : 'are'} shown below.`;
}

export function parityLineHtml(S) {
  if (!S.metrics) {
    return `<p class="mono small dim">parity is undefined at ${(S.seats || []).length < 3 ? 'two seats' : 'this session'}</p>`;
  }
  const thr = Number(S.metrics.parity_threshold).toFixed(2);
  const rec = S.metrics_recomputed ? ' · <span class="dimmer">recomputed</span>' : '';
  const readings = S.interpretations || [];
  if (readings.length) {
    return readings.map((i) => `<p class="mono small"><span class="converge">flag</span> · ${seatName(i.model)} · ${esc(DIMENSION_WORDS[i.dimension] || i.dimension)} · ${esc(READING_WORDS[i.reading] || i.reading)} · share ${Number(i.share).toFixed(2)} · threshold ${thr}${rec}${i.gloss ? `<br><span class="serif-gloss">${esc(i.gloss)}</span>` : ''}</p>`).join('');
  }
  const shares = S.metrics.distinct_claim_share || {};
  const low = Object.entries(shares).sort((a, b) => a[1] - b[1])[0];
  return `<p class="mono small">parity <span class="present">holds</span> · threshold ${thr} · lowest share ${low ? `${esc(labelOf(low[0]))} ${low[1].toFixed(2)}` : '—'}${rec}</p>`;
}
