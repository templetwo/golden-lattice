// Reading pass (spec §12) decisions, run with `node --test`.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  SECTION_NAMES, challengeLabel, outcomeWord, stepDisagreement, sortRuns, recordSummary,
} from '../../src/golden_lattice/dashboard/static/js/notes.js';
import { phaseStops } from '../../src/golden_lattice/dashboard/static/js/beats.js';

test('sections carry plain names alongside their numbers', () => {
  assert.deepEqual(SECTION_NAMES, {
    '00': 'Research', '01': 'Initial answers', '02': 'Disagreements', '03': 'Discussion', '04': 'Final answer',
  });
});

test('the row mark has a spoken explanation', () => {
  assert.equal(challengeLabel(1), 'Challenged by one reader');
  assert.equal(challengeLabel(2), 'Challenged by two readers');
  assert.equal(challengeLabel(3), 'Challenged by three readers');
  assert.equal(challengeLabel(5), 'Challenged by 5 readers');
});

test('a claim outcome reads as retained, changed or omitted', () => {
  assert.equal(outcomeWord('present'), 'Retained');
  assert.equal(outcomeWord('modified'), 'Changed');
  assert.equal(outcomeWord('omitted'), 'Omitted');
  assert.equal(outcomeWord(null), 'Not yet in the final answer');
});

test('previous and next disagreement step through the table order and wrap', () => {
  const ids = ['a', 'b', 'c'];
  assert.equal(stepDisagreement(ids, null, 1), 'a');
  assert.equal(stepDisagreement(ids, null, -1), 'c');
  assert.equal(stepDisagreement(ids, 'a', 1), 'b');
  assert.equal(stepDisagreement(ids, 'c', 1), 'a');
  assert.equal(stepDisagreement(ids, 'a', -1), 'c');
  assert.equal(stepDisagreement(ids, 'zz', 1), 'a');
  assert.equal(stepDisagreement([], 'a', 1), null);
});

const runs = [
  { id: 'session_20260501_000000_a', disputed: 5, parity: 'holds' },
  { id: 'session_20260503_000000_c', disputed: 2, parity: 'flag' },
  { id: 'session_20260502_000000_b', disputed: 9, parity: 'undefined' },
];

test('runs sort by newest, most disputed, or flagged first', () => {
  assert.deepEqual(sortRuns(runs, 'newest').map((r) => r.id.slice(-1)), ['c', 'b', 'a']);
  assert.deepEqual(sortRuns(runs, 'disputed').map((r) => r.id.slice(-1)), ['b', 'a', 'c']);
  assert.deepEqual(sortRuns(runs, 'flagged').map((r) => r.id.slice(-1)), ['c', 'b', 'a']);
  assert.equal(runs[0].id.slice(-1), 'a', 'sorting must not mutate the input');
});

test('the Record opens with a one-line summary', () => {
  const record = {
    runs: [{ parity: 'holds' }, { parity: 'holds' }, { parity: 'flag' }, { parity: 'undefined' }],
    flags: [{ family: 'sonnet' }],
    persistent: { present: false },
    series: { opus: [{ share: 0.4 }, { share: 0.38 }], haiku: [{ share: 0.26 }, { share: 0.24 }] },
    threshold: 0.15,
  };
  assert.equal(recordSummary(record),
    '4 runs · parity holds in 2 of 3 measured · 1 flag · lowest seat HAIKU, never below 0.24 · no persistent flagging');
});

test('replay offers a named stop for each phase that has steps', () => {
  const B = { first: { '00': 0, '01': 4, '02': 50, '03': 60, '04': 90 } };
  assert.deepEqual(phaseStops(B), [
    { sec: '00', label: 'Research', step: 0 },
    { sec: '01', label: 'Initial answers', step: 4 },
    { sec: '02', label: 'Disagreements', step: 50 },
    { sec: '03', label: 'Discussion', step: 60 },
    { sec: '04', label: 'Final answer', step: 90 },
  ]);
  assert.deepEqual(phaseStops({ first: { '00': 0, '04': 9 } }).map((s) => s.sec), ['00', '04']);
});

test('a replay stop shows its section, pinned beside the one that leads into it', async () => {
  const { stopLayout } = await import('../../src/golden_lattice/dashboard/static/js/notes.js');
  assert.deepEqual(stopLayout('00'), { active: '00', pinned: null });
  assert.deepEqual(stopLayout('01'), { active: '01', pinned: null });
  assert.deepEqual(stopLayout('02'), { active: '02', pinned: '01' });
  assert.deepEqual(stopLayout('03'), { active: '03', pinned: '02' });
  assert.deepEqual(stopLayout('04'), { active: '04', pinned: '02' });
});

test('the inline pane split applies only to two panes on a wide window', async () => {
  const { splitColumns } = await import('../../src/golden_lattice/dashboard/static/js/notes.js');
  assert.equal(splitColumns({ two: true, wide: true, split: 0.6 }), 'minmax(0, 0.6fr) 8px minmax(0, 0.4fr)');
  assert.equal(splitColumns({ two: true, wide: false, split: 0.6 }), '');
  assert.equal(splitColumns({ two: false, wide: true, split: 0.6 }), '');
});
