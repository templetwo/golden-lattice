// Pure page decisions: which note a section says, which filter shows, how a
// parity line reads. No DOM. Tested with `node --test tests/js`.
import { esc, READING_WORDS, DIMENSION_WORDS } from './util.js';
import { labelOf, colorOf, FAMILY_ORDER } from './seats.js';
export { SECTION_NAMES } from './beats.js';

const seatName = (id) => `<span class="seat-name" style="color:${colorOf(id)}">${esc(labelOf(id))}</span>`;

// The disputes table's effective filter and the one note it carries.
// A live run knows per-pair counts during cross-reading but not which claim
// each dispute names, so it must never say nobody disputed (review, Important 2).
// Per-claim reasons are complete only for a persisted session (replayed or
// hydrated); a live object knows per-pair counts at most, even after synthesis
// arrives (review of PR #2, finding 4).
export function disputesView({ session, readingsIn, filter }) {
  const live = !!session.live;
  const complete = !live;
  const stats = session.stats;
  const claims = session.claims;
  if (!readingsIn) return { filter: filter === 'disputed' ? 'all' : filter, note: 'readers have not cross-read yet' };
  const reasonsKnown = (claims || []).some((c) => (c.disagreements || []).length);
  if (live && !complete && !reasonsKnown) {
    return { filter: filter === 'disputed' && !(stats || {}).disputed ? 'all' : filter,
      note: "readers' per-claim reasons arrive when the run completes" };
  }
  if (!(stats || {}).disputed) return { filter: 'all', note: 'no reader disputed anything in this run' };
  return { filter, note: null };
}

// The filter that keeps `claim` visible when a synthesis link opens it
// (review of PR #2, finding 3). Unchanged when the claim already shows.
export function filterForClaim(filter, claim, revealedDisputers) {
  if (filter === 'disputed' && revealedDisputers > 0) return filter;
  if (filter === 'elevated' && claim.elevated) return filter;
  if (filter === 'omitted' && claim.disposition === 'omitted') return filter;
  return 'all';
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

// ---------- reading pass (spec §12) ----------
const NUMBER_WORDS = { 1: 'one', 2: 'two', 3: 'three', 4: 'four' };

export function challengeLabel(n) {
  const word = NUMBER_WORDS[n] || String(n);
  return `Challenged by ${word} reader${n === 1 ? '' : 's'}`;
}

export function outcomeWord(disposition) {
  return { present: 'Retained', modified: 'Changed', omitted: 'Omitted' }[disposition] || 'Not yet in the final answer';
}

// Previous/Next disagreement: step through `ids` (table order), wrapping.
export function stepDisagreement(ids, current, dir) {
  if (!ids.length) return null;
  const i = ids.indexOf(current);
  if (i < 0) return dir < 0 ? ids[ids.length - 1] : ids[0];
  return ids[(i + dir + ids.length) % ids.length];
}

export function sortRuns(runs, mode) {
  const newest = (a, b) => (a.id < b.id ? 1 : a.id > b.id ? -1 : 0);
  const out = runs.slice();
  if (mode === 'disputed') out.sort((a, b) => (b.disputed - a.disputed) || newest(a, b));
  else if (mode === 'flagged') out.sort((a, b) => ((b.parity === 'flag') - (a.parity === 'flag')) || newest(a, b));
  else out.sort(newest);
  return out;
}

export function recordSummary(record) {
  const runs = record.runs || [];
  const measured = runs.filter((r) => r.parity !== 'undefined');
  const holds = measured.filter((r) => r.parity === 'holds').length;
  const nf = (record.flags || []).length;
  const parts = [`${runs.length} run${runs.length === 1 ? '' : 's'}`];
  if (measured.length) parts.push(`parity holds in ${holds} of ${measured.length} measured`);
  parts.push(`${nf} flag${nf === 1 ? '' : 's'}`);
  let low = null;
  for (const f of FAMILY_ORDER) {
    const shares = ((record.series || {})[f] || []).map((p) => p.share).filter((v) => v != null);
    if (!shares.length) continue;
    const m = Math.min(...shares);
    if (!low || m < low[1]) low = [f, m];
  }
  if (low) parts.push(`lowest seat ${low[0].toUpperCase()}, never below ${low[1].toFixed(2)}`);
  const p = record.persistent || {};
  parts.push(p.present ? `persistent flagging: ${String(p.family).toUpperCase()}` : 'no persistent flagging');
  return parts.join(' · ');
}
