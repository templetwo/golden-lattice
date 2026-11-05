// Live-mode decisions from the PR #2 review, run with `node --test`.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { applyEvent, acceptHydration } from '../../src/golden_lattice/dashboard/static/js/live.js';
import { disputesView, filterForClaim } from '../../src/golden_lattice/dashboard/static/js/notes.js';

const O = 'claude-opus-5', S = 'claude-sonnet-5', H = 'claude-haiku-4-5-20251001';
const start = (id) => ({ event_type: 'session_started', timestamp_offset_ms: 0, session_id: id, prompt: 'p', prompt_hash: 'h', models_invited: [O, S, H] });

test('a hydration fetched for run A is refused once run B has started', () => {
  const state = { L: null };
  applyEvent(state, start('session_a'));
  const captured = state.L;
  applyEvent(state, start('session_b'));
  assert.equal(acceptHydration(state, captured, { id: 'session_a' }), false);
  assert.equal(acceptHydration(state, state.L, { id: 'session_b' }), true);
  assert.equal(acceptHydration(state, state.L, { id: 'session_a' }), false);
});

test('a live critique aimed at no Phase 1 claim is kept, not dropped', () => {
  const state = { L: null };
  applyEvent(state, start('session_a'));
  applyEvent(state, { event_type: 'phase_1_claim', model_id: O, claim_id: 'c1', text: 'opus one' });
  applyEvent(state, { event_type: 'phase_3_turn', turn_id: 't9', speaker_model: H, channel: 'critique', target_model: S, target_claim_ids: ['missing-1'], content: 'aimed at a missing claim' });
  assert.deepEqual(state.L.unattached_critiques.map((u) => u.turn_id), ['t9']);
  assert.equal(state.L.unattached_critiques[0].content, 'aimed at a missing claim');
  assert.equal(state.L.stats.critiques, 1);
  assert.equal(state.L.stats.critiques_attached, 0);
});

test('synthesis arriving does not make a live run claim nobody disputed', () => {
  const live = { live: true, artArrived: true, stats: { disputed: 0 }, claims: [{ disagreements: [] }],
    pairs: [{ reader: O, target: S, disputed: 5, endorsed: 3, missing: 0 }] };
  const v = disputesView({ session: live, readingsIn: true, filter: 'disputed' });
  assert.notEqual(v.note, 'no reader disputed anything in this run');
  assert.equal(v.note, "readers' per-claim reasons arrive when the run completes");
});

test('a hydrated or replayed run with zero disputes still says so', () => {
  const done = { stats: { disputed: 0 }, claims: [{ disagreements: [] }], pairs: [] };
  assert.equal(disputesView({ session: done, readingsIn: true, filter: 'disputed' }).note, 'no reader disputed anything in this run');
});

test('opening a claim from synthesis clears any filter that would hide it', () => {
  const disputed = { elevated: false, disposition: 'present' };
  assert.equal(filterForClaim('omitted', disputed, 2), 'all');
  assert.equal(filterForClaim('elevated', disputed, 2), 'all');
  assert.equal(filterForClaim('disputed', disputed, 0), 'all');
  assert.equal(filterForClaim('disputed', disputed, 2), 'disputed');
  assert.equal(filterForClaim('elevated', { elevated: true, disposition: 'present' }, 0), 'elevated');
  assert.equal(filterForClaim('omitted', { elevated: false, disposition: 'omitted' }, 0), 'omitted');
  assert.equal(filterForClaim('all', disputed, 0), 'all');
});

test('interleaved requests from one seat keep separate buffers', async () => {
  const { writingText } = await import('../../src/golden_lattice/dashboard/static/js/live.js');
  const state = { L: null };
  applyEvent(state, start('session_a'));
  const d = (stream_id, target, delta, phase = 'phase_2_cross_reading') => applyEvent(state,
    { event_type: 'model_stream_delta', model_id: O, phase, delta, delta_kind: 'tool_input', stream_id, target_model: target });
  const toS = `phase_2_cross_reading:${O}>${S}`, toH = `phase_2_cross_reading:${O}>${H}`, tag = `phase_2_tagging:${O}`;
  d(toS, S, '{"disagreements": [{"target_claim_id": "a", "reason": "Sonnet is');
  d(toH, H, '{"disagreements": [{"target_claim_id": "b", "reason": "Haiku is');
  d(tag, null, '{"peer_tags": [', 'phase_2_tagging');
  d(toS, S, ' too strong"}]');
  d(toH, H, ' too vague"}]');
  assert.equal(writingText(state.L.streams[toS]), 'Sonnet is too strong');
  assert.equal(writingText(state.L.streams[toH]), 'Haiku is too vague');
  assert.equal(state.L.streams[toS].target, S);
});

test('deltas without a stream id fall back to seat and phase', async () => {
  const { writingText } = await import('../../src/golden_lattice/dashboard/static/js/live.js');
  const state = { L: null };
  applyEvent(state, start('session_a'));
  applyEvent(state, { event_type: 'model_stream_delta', model_id: O, phase: 'phase_1', delta: '{"response": "Hel', delta_kind: 'tool_input' });
  applyEvent(state, { event_type: 'model_stream_delta', model_id: O, phase: 'phase_1', delta: 'lo"', delta_kind: 'tool_input' });
  assert.equal(writingText(state.L.streams[`phase_1:${O}`]), 'Hello');
});
