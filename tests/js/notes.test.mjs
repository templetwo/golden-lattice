// Pure page decisions from static/js/notes.js, run with `node --test`.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  disputesView, feedSkippedText, parityLineHtml, openingStep, critiquesCaption,
} from '../../src/golden_lattice/dashboard/static/js/notes.js';

const claim = (o = {}) => ({ disagreements: [], disputed_by: [], ...o });

test('live run with counts but no per-claim reasons never says nobody disputed', () => {
  const v = disputesView({ session: { live: true, stats: { disputed: 0 }, claims: [claim()] }, readingsIn: true, filter: 'disputed' });
  assert.equal(v.filter, 'all');
  assert.equal(v.note, "readers' per-claim reasons arrive when the run completes");
});

test('a finished run with zero disputes says so and shows every claim', () => {
  const v = disputesView({ session: { stats: { disputed: 0 }, claims: [claim()] }, readingsIn: true, filter: 'disputed' });
  assert.deepEqual(v, { filter: 'all', note: 'no reader disputed anything in this run' });
});

test('before cross-reading the note says readers have not cross-read yet', () => {
  const v = disputesView({ session: { stats: { disputed: 3 }, claims: [] }, readingsIn: false, filter: 'disputed' });
  assert.deepEqual(v, { filter: 'all', note: 'readers have not cross-read yet' });
});

test('an ordinary disputed view keeps the chosen filter and adds no note', () => {
  const v = disputesView({ session: { stats: { disputed: 3 }, claims: [] }, readingsIn: true, filter: 'elevated' });
  assert.deepEqual(v, { filter: 'elevated', note: null });
});

test('a skipped feed reports what the file holds, not what happened', () => {
  assert.equal(feedSkippedText(false), 'No Phase 0 record in this file.');
  assert.doesNotMatch(feedSkippedText(false), /held only/);
});

test('a flagged parity line carries the share and the threshold', () => {
  const html = parityLineHtml({
    metrics: { parity_threshold: 0.15, distinct_claim_share: {} }, metrics_recomputed: false,
    seats: [{}, {}, {}],
    interpretations: [{ model: 'claude-sonnet-5', dimension: 'edge_case_coverage_share', reading: 'not_recognized', gloss: 'g', share: 0.1 }],
  });
  assert.match(html, /share 0\.10/);
  assert.match(html, /threshold 0\.15/);
});

test('a replay opens at the end, whatever was saved before', () => {
  assert.equal(openingStep({ N: 126 }), 126);
});

test('the dialogue caption counts only critiques that sit on a claim', () => {
  assert.equal(critiquesCaption({ critiques: 3, critiques_attached: 2 }),
    '2 critiques are on their claims in 02; 1 targets no Phase 1 claim and is shown below.');
  assert.equal(critiquesCaption({ critiques: 1, critiques_attached: 1 }), '1 critique is on its claim in 02.');
});
