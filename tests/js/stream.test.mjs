// Readable streaming (live mode): prose pulled out of partial tool-call JSON.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readableStream, streamText, phaseActivity } from '../../src/golden_lattice/dashboard/static/js/stream.js';

test('the answer streams as text while its JSON string is still open', () => {
  assert.deepEqual(readableStream('{"response": "The cache should evi'), [{ key: 'response', text: 'The cache should evi', done: false }]);
});

test('escapes decode, and a half-written escape at the tail is held back', () => {
  assert.equal(readableStream('{"response": "line one\\nline \\"two\\" \\u00e9')[0].text, 'line one\nline "two" é');
  assert.equal(readableStream('{"response": "ends mid escape \\u00')[0].text, 'ends mid escape ');
  assert.equal(readableStream('{"response": "trailing backslash \\')[0].text, 'trailing backslash ');
});

test('claims stream as list items; structural fields are skipped', () => {
  const raw = '{"response": "Answer.", "focus_tag": "correctness", "confidence": 0.8, "claim_texts": ["first claim", "second cl';
  assert.deepEqual(readableStream(raw).map((s) => [s.key, s.text, s.done]), [
    ['response', 'Answer.', true], ['claim_texts', 'first claim', true], ['claim_texts', 'second cl', false],
  ]);
});

test('cross-reading reasons and discussion turns stream; ids do not', () => {
  const cr = '{"agreements": [{"claim_id": "abc"}], "disagreements": [{"target_claim_id": "def", "reason": "Too strong because'
  assert.deepEqual(readableStream(cr).map((s) => s.text), ['Too strong because']);
  const d = '{"turns": [{"channel": "critique", "target_model": "claude-opus-5", "target_claim_ids": ["x"], "content": "The claim overstates"}, {"channel": "augment", "content": "Also';
  assert.deepEqual(readableStream(d).map((s) => s.text), ['The claim overstates', 'Also']);
});

test('tags and ids alone produce no readable text', () => {
  assert.deepEqual(readableStream('{"peer_tags": [{"claim_id": "a", "edge_case_tags": ["failure_mode"]}]}'), []);
});

test('segments become readable text: prose as paragraphs, list items as bullets', () => {
  const segs = [{ key: 'response', text: 'Answer.' }, { key: 'claim_texts', text: 'one' }, { key: 'claim_texts', text: 'two' }];
  assert.equal(streamText(segs), 'Answer.\n\n• one\n• two');
});

test('each phase has a plain activity name', () => {
  assert.equal(phaseActivity('phase_1'), 'writing an initial answer');
  assert.equal(phaseActivity('phase_2_cross_reading'), 'cross-reading a peer');
  assert.equal(phaseActivity('phase_3'), 'in discussion');
  assert.equal(phaseActivity('something_new'), 'something new');
});

test('section 01 shows only the answer prose, not the claim list', async () => {
  const { initialAnswerText } = await import('../../src/golden_lattice/dashboard/static/js/live.js');
  const w = { phase: 'phase_1', raw: '{"response": "LRU is the default.", "claim_texts": ["one", "tw' };
  assert.equal(initialAnswerText(w), 'LRU is the default.');
});
