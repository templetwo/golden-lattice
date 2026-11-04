// Replay beats (spec §6.3). Pure functions over the normalized session:
// a beat never touches the DOM. Sections read `revealedAt(key) <= step`.

export const PLAY_PERIOD_MS = 360;
export const SECTIONS = ['00', '01', '02', '03', '04'];
// Plain names beside the phase numbers (spec §12).
export const SECTION_NAMES = {
  '00': 'Research', '01': 'Initial answers', '02': 'Disagreements', '03': 'Discussion', '04': 'Final answer',
};

export function buildBeats(s) {
  const beats = [];
  const at = {};
  const push = (section, key) => { at[key] = beats.length; beats.push({ section, key }); };

  const p0 = s.phase0;
  if (p0) {
    push('00', 'g');
    (p0.proposals || []).forEach((_, i) => push('00', `prop${i}`));
    push('00', 'frozen');
    (p0.results || []).forEach((_, i) => push('00', `res${i}`));
    (p0.failed || []).forEach((_, i) => push('00', `fail${i}`));
  } else {
    push('00', 'p0skip');
  }

  // Phase 1: claims interleaved by position across seats, then reflections.
  const bySeat = (s.seats || []).map((seat) => (s.claims || []).filter((c) => c.author === seat.id));
  const most = Math.max(0, ...bySeat.map((a) => a.length));
  for (let i = 0; i < most; i++) {
    bySeat.forEach((claims) => { if (claims[i]) push('01', `c:${claims[i].id}`); });
  }
  (s.seats || []).forEach((seat) => {
    if (!s.live || (s.reflections || {})[seat.id]) push('01', `r:${seat.id}`);
  });

  // Phase 2: one beat per cross-reading. Phase 3: one beat per turn.
  (s.pairs || []).forEach((p) => push('02', `x:${p.reader}>${p.target}`));
  (s.turns || []).forEach((t) => push('03', `t:${t.id}`));

  // Phase 4: artifact, each output line, then metrics and readings. A live
  // session only has these beats once the events have actually arrived.
  if (!s.live || s.artArrived) {
    push('04', 'art');
    (s.synthesis_lines || []).forEach((l) => push('04', `l:${l.index}`));
  }
  if (!s.live || s.metricsArrived) push('04', 'metrics');

  const first = {};
  beats.forEach((b, i) => { if (first[b.section] == null) first[b.section] = i; });
  return { beats, at, N: Math.max(0, beats.length - 1), first };
}

// The beat at which `reader`'s dispute of `claim` becomes visible: the
// earliest of their cross-reading of the author and their critique turns.
export function disputeBeat(B, claim, reader) {
  const options = [];
  if ((claim.disagreements || []).some((d) => d.reader === reader)) {
    const k = B.at[`x:${reader}>${claim.author}`];
    if (k != null) options.push(k);
  }
  (claim.critiques || []).forEach((c) => {
    if (c.speaker === reader && B.at[`t:${c.turn_id}`] != null) options.push(B.at[`t:${c.turn_id}`]);
  });
  return options.length ? Math.min(...options) : Infinity;
}

export function phaseLabel(B, step) {
  const b = B.beats[Math.min(step, B.N)];
  return b ? `${b.section} · ${SECTION_NAMES[b.section]}` : '';
}

// One named stop per phase that has steps, for the replay transport.
export function phaseStops(B) {
  return SECTIONS.filter((sec) => B.first[sec] != null)
    .map((sec) => ({ sec, label: SECTION_NAMES[sec], step: B.first[sec] }));
}
