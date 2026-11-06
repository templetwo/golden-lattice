// Frontend halves of the daily-dash audit items, run with `node --test`
// from tests/dashboard/test_daily_dash.py.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { sortRuns } from '../../../src/golden_lattice/dashboard/static/js/notes.js';
import { runDate, splitAccountability } from '../../../src/golden_lattice/dashboard/static/js/util.js';
import { applyEvent } from '../../../src/golden_lattice/dashboard/static/js/live.js';

const O = 'claude-opus-5', S = 'claude-sonnet-5', H = 'claude-haiku-4-5-20251001';

const runs = [
  { id: 'session_20260504_120000_abcd1234', created_at: '2026-05-04T12:00:00+00:00', disputed: 1, parity: 'holds' },
  { id: 'daily-20260514T120000Z-0a1b2c3d', created_at: '2026-05-14T12:00:00+00:00', disputed: 1, parity: 'holds' },
  { id: 'daily-20260424T120000Z-99887766', created_at: '2026-04-24T12:00:00+00:00', disputed: 3, parity: 'flag' },
];

test('newest sort follows created_at, not the id string', () => {
  assert.deepEqual(sortRuns(runs, 'newest').map((r) => r.id),
    ['daily-20260514T120000Z-0a1b2c3d', 'session_20260504_120000_abcd1234', 'daily-20260424T120000Z-99887766']);
});

test('tie-breaks in disputed and flagged sorts are newest by created_at', () => {
  assert.deepEqual(sortRuns(runs, 'disputed').map((r) => r.id),
    ['daily-20260424T120000Z-99887766', 'daily-20260514T120000Z-0a1b2c3d', 'session_20260504_120000_abcd1234']);
});

test('runs with no created_at still sort by id', () => {
  const old = [{ id: 'session_20260101_000000_a' }, { id: 'session_20260301_000000_b' }];
  assert.deepEqual(sortRuns(old, 'newest').map((r) => r.id), ['session_20260301_000000_b', 'session_20260101_000000_a']);
});

test('runDate reads a daily run id', () => {
  assert.equal(runDate('daily-20260514T120305Z-0a1b2c3d'), '05-14 12:03');
  assert.equal(runDate('session_20260516_204119_ed014de7'), '05-16 20:41');
  assert.equal(runDate('live'), '');
});

test('splitAccountability stops at the first section header line', () => {
  const body = '[O] one\n[H] two';
  for (const header of ['--- elevated (1) ---', '--- disagreements surfaced (2) ---', '--- omitted: what was dropped and why (1) ---']) {
    const footer = `${header}\n[H] abc: dropped`;
    assert.deepEqual(splitAccountability(`${body}\n\n${footer}`), [`${body}\n\n`, footer]);
  }
  assert.deepEqual(splitAccountability(body), [body, '']);
  assert.deepEqual(splitAccountability(''), ['', '']);
  assert.deepEqual(splitAccountability('[O] x --- elevated (1) ---'), ['[O] x --- elevated (1) ---', '']);
});

test('a live artifact does not render the footer as seat lines', () => {
  const state = { L: null };
  applyEvent(state, { event_type: 'session_started', timestamp_offset_ms: 0, session_id: 'daily-x', prompt: 'p', prompt_hash: 'h', models_invited: [O, S, H] });
  assert.equal(state.L.accountability, '');
  const footer = '--- omitted: what was dropped and why (1) ---\n[H] c9: restates opus';
  applyEvent(state, { event_type: 'phase_4_artifact', output: `[O] opus one\n\n${footer}`, claim_trace: [], elevations: [], surfaced_disagreements: [] });
  assert.deepEqual(state.L.synthesis_lines.map((l) => [l.tag, l.text]), [['O', 'opus one']]);
  assert.equal(state.L.accountability, footer);
});

test('a naive created_at sorts as UTC', () => {
  const mixed = [
    { id: 'a', created_at: '2026-05-04T12:30:00' },
    { id: 'b', created_at: '2026-05-04T13:00:00+00:00' },
    { id: 'c', created_at: '2026-05-04T12:00:00Z' },
  ];
  assert.deepEqual(sortRuns(mixed, 'newest').map((r) => r.id), ['b', 'a', 'c']);
});

test('the live prompt box renders disabled with a title naming the CLI', async () => {
  const { shellHtml, liveBarHtml, PROMPT_OFF } = await import('../../../src/golden_lattice/dashboard/static/js/live.js');
  assert.match(PROMPT_OFF, /"golden-lattice run"/);
  for (const html of [shellHtml('connecting…'), liveBarHtml('connected')]) {
    const input = /<input id="gl-prompt"[^>]*>/.exec(html)[0];
    assert.match(input, / disabled>$/);
    assert.match(input, /title="[^"]*&quot;golden-lattice run&quot;[^"]*"/);
    assert.match(/<button class="send"[^>]*>/.exec(html)[0], / disabled>$/);
  }
});
