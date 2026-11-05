// Readable streaming. Live runs stream each model's forced tool call as
// partial JSON; this pulls the prose out of it as it grows (the answer,
// claims, reasons, discussion turns) and skips ids, tags and numbers.
// Pure; tested with `node --test tests/js`.

const PROSE_KEYS = new Set(['response', 'reason', 'content', 'tag_justification', 'rationale']);
const LIST_KEYS = new Set(['claim_texts', 'missing_texts', 'queries']);

// Decode a JSON string starting just after its opening quote. Returns the
// text so far, whether the string closed, and the index after it. A
// half-written escape at the end of the input is held back, not shown.
function readString(raw, start) {
  let out = '';
  let j = start;
  while (j < raw.length) {
    const c = raw[j];
    if (c === '\\') {
      if (j + 1 >= raw.length) return { text: out, done: false, next: raw.length };
      const e = raw[j + 1];
      if (e === 'u') {
        if (j + 6 > raw.length) return { text: out, done: false, next: raw.length };
        out += String.fromCharCode(parseInt(raw.slice(j + 2, j + 6), 16));
        j += 6;
        continue;
      }
      out += { n: '\n', t: '\t', r: '', b: '', f: '' }[e] ?? e;
      j += 2;
      continue;
    }
    if (c === '"') return { text: out, done: true, next: j + 1 };
    out += c;
    j += 1;
  }
  return { text: out, done: false, next: raw.length };
}

export function readableStream(raw) {
  const segs = [];
  const stack = [];
  let i = 0;
  while (i < raw.length) {
    const ch = raw[i];
    const top = stack[stack.length - 1];
    if (ch === '"') {
      const s = readString(raw, i + 1);
      if (top && top.type === 'obj' && top.expectKey) {
        top.key = s.text;
        top.expectKey = false;
      } else if (top) {
        const key = top.type === 'obj' ? top.key : top.listKey;
        const prose = (top.type === 'obj' && PROSE_KEYS.has(key)) || (top.type === 'arr' && LIST_KEYS.has(key));
        if (prose) segs.push({ key, text: s.text, done: s.done });
      }
      i = s.next;
      continue;
    }
    if (ch === '{') stack.push({ type: 'obj', key: null, expectKey: true });
    else if (ch === '[') stack.push({ type: 'arr', listKey: top ? (top.type === 'obj' ? top.key : top.listKey) : null });
    else if (ch === '}' || ch === ']') stack.pop();
    else if (ch === ',' && top && top.type === 'obj') top.expectKey = true;
    i += 1;
  }
  return segs;
}

export function streamText(segs) {
  const parts = [];
  segs.forEach((s) => {
    if (LIST_KEYS.has(s.key)) {
      const bullet = `• ${s.text}`;
      const prev = parts[parts.length - 1];
      if (prev && prev.list) prev.lines.push(bullet); else parts.push({ list: true, lines: [bullet] });
    } else {
      parts.push({ list: false, lines: [s.text] });
    }
  });
  return parts.map((p) => p.lines.join('\n')).join('\n\n');
}

const ACTIVITY = {
  phase_0_proposal: 'proposing searches',
  phase_1: 'writing an initial answer',
  self_reflection: 'reflecting on its answer',
  phase_2_cross_reading: 'cross-reading a peer',
  phase_2_tagging: 'tagging claims',
  phase_3: 'in discussion',
};

export function phaseActivity(phase) {
  return ACTIVITY[phase] || String(phase || '').replace(/_/g, ' ');
}
