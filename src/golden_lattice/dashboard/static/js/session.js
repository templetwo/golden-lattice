// The Session page (spec §6.2): rail with one window and a pin.
// Renders only from the normalized session shape; computes no metrics.
import { esc, runDate, lsGet, lsSet, READING_WORDS, DIMENSION_WORDS } from './util.js';
import { labelOf, colorOf, familyColor } from './seats.js';
import { buildBeats, disputeBeat, phaseLabel, PLAY_PERIOD_MS, SECTIONS } from './beats.js';

const TITLES = { '00': 'feed', '01': 'seats', '02': 'disputes', '03': 'dialogue', '04': 'synthesis' };
const LONG = { '00': 'Investigation', '01': 'Independent', '02': 'Where they disagreed', '03': 'Dialogue', '04': 'Synthesis' };
const TAG_FAMILY = { F: 'fable', O: 'opus', S: 'sonnet', H: 'haiku' };

let S = null;   // the normalized session being shown
let B = null;   // its beats
let root = null;
let mode = 'replay';
let timer = null;
const ui = {
  active: '02', pinned: '04', open: new Set(), filter: 'disputed',
  scroll: {}, step: 0, playing: false, speed: 1, hovered: null, flash: null,
};

// ---------- small renderers ----------
const seatName = (id) => `<span class="seat-name" style="color:${colorOf(id)}">${esc(labelOf(id))}</span>`;
const on = (key) => B.at[key] != null && B.at[key] <= ui.step;

function claimById(id) { return (S.claims || []).find((c) => c.id === id); }

function revealedDisputers(c) {
  return (c.disputed_by || []).filter((r) => disputeBeat(B, c, r) <= ui.step);
}

// ---------- 00 · feed ----------
function sectionFeed() {
  const p0 = S.phase0;
  if (!p0) {
    return `<p class="lead">Investigation was skipped. The shared feed held only the temporal grounding; the run went straight to independent generation.</p>`;
  }
  const props = (p0.proposals || []).map((p, i) => on(`prop${i}`) ? `
    <div class="prop"><div class="k">${seatName(p.model)} · proposes ${p.count}</div>
      ${(p.queries || []).map((q) => `<div class="q mono">${esc(q)}</div>`).join('')}</div>` : '').join('');
  const results = (p0.results || []).map((r, i) => on(`res${i}`) ? `
    <div class="res"><div class="mono q-ok">✓ ${esc(r.query)}</div><div class="caption">${esc(r.preview)}</div></div>` : '').join('');
  const failed = (p0.failed || []).map((f, i) => on(`fail${i}`) ? `
    <div class="res fail"><div class="mono q-no">✗ ${esc(f.query)}</div><div class="caption">${esc(f.reason)}</div></div>` : '').join('');
  return `
    ${on('g') ? `<p class="mono small dim">grounding · ${esc(p0.grounding)} · deterministic, not a model action</p>` : ''}
    <div class="props">${props}</div>
    ${on('frozen') ? `<p class="mono small">rule-based union · ${p0.unique} unique → <span class="converge">feed frozen · ${p0.frozen} entries</span></p>` : ''}
    ${results}${failed}`;
}

// ---------- 01 · seats ----------
function sectionSeats() {
  const cols = (S.seats || []).map((seat) => {
    const refl = (S.reflections || {})[seat.id] || {};
    const strong = refl.strong && claimById(refl.strong);
    const weak = refl.weak && claimById(refl.weak);
    const shown = on(`r:${seat.id}`);
    const stream = mode === 'live' ? `<pre class="stream" data-stream="${esc(seat.id)}">${esc(((S.phase1 || {})[seat.id] || {}).stream || '')}</pre>` : '';
    return `
      <div class="seat-col">
        <div class="k">${seatName(seat.id)} · ${esc(seat.focus || '')} · ${Number(seat.conf || 0).toFixed(2)} · ${seat.claim_count} claims</div>
        <div class="mono small dimmer">${esc(seat.id)}</div>
        ${stream}
        ${shown ? `
          <p class="refl">${esc(refl.just || '')}</p>
          ${strong ? `<p class="small-serif"><span class="k">strongest</span> ${esc(strong.text)}</p>` : ''}
          ${weak ? `<p class="small-serif"><span class="k">weakest</span> ${esc(weak.text)}</p>` : ''}` : '<p class="dim small">self-reflection not yet in</p>'}
      </div>`;
  }).join('');
  return `<div class="seat-grid">${cols}</div>`;
}

// ---------- 02 · disputes ----------
function rowMark(c, disputers) {
  if (disputers.length) return `<span class="critique">×${disputers.length}</span>`;
  if (c.elevated && on('art')) return '<span class="converge">✦</span>';
  if (c.disposition === 'omitted' && on('art')) return '<span class="dim">—</span>';
  return '';
}

function voiceBlocks(c, disputers) {
  const blocks = disputers.map((r) => {
    const dis = (c.disagreements || []).filter((d) => d.reader === r && on(`x:${r}>${c.author}`));
    const crit = (c.critiques || []).filter((k) => k.speaker === r && on(`t:${k.turn_id}`));
    const endorsed = (c.agreed_by || []).includes(r);
    const parts = [];
    dis.forEach((d) => parts.push(`<p>${esc(d.reason)}</p>`));
    if (crit.length) {
      const lead = dis.length ? 'In dialogue:' : (endorsed ? 'endorsed in cross-reading, then in dialogue:' : 'In dialogue:');
      crit.forEach((k, i) => parts.push(`<p>${i === 0 ? `<span class="k">${lead}</span> ` : ''}${esc(k.content)}</p>`));
    }
    return `<div class="voice">${seatName(r)}${parts.join('')}</div>`;
  }).join('');
  const endorsers = (c.agreed_by || []).filter((r) => !disputers.includes(r) && on(`x:${r}>${c.author}`));
  const endorsedLine = endorsers.length
    ? `<p class="endorsed"><span class="k">endorsed by</span> ${endorsers.map(seatName).join(' · ')}</p>` : '';
  return blocks + endorsedLine;
}

function fateLine(c) {
  if (!on('art')) return '<p class="fate dim">synthesis has not run yet</p>';
  const color = c.disposition === 'omitted' ? 'critique' : c.disposition === 'modified' ? 'converge' : 'present';
  const parts = [`synthesis · <span class="${color}">${esc(c.disposition || 'untraced')}</span>`];
  if (c.synthesis_line != null) parts.push(`<a href="#" class="line-link" data-line="${c.synthesis_line}">line ${c.synthesis_line + 1}</a>`);
  let extra = '';
  if (c.disputed_hedge) extra += `<p class="hedge">${esc(c.disputed_hedge)}</p>`;
  if (c.surfaced_note) extra += `<p class="note"><span class="k">surfaced</span> ${esc(c.surfaced_note)}</p>`;
  if (c.disposition === 'omitted' && c.omission_reason) extra += `<p class="note"><span class="k">omitted because</span> ${esc(c.omission_reason)}</p>`;
  if (c.disposition === 'modified' && c.modified_text) {
    const bare = c.modified_text.split('[DISPUTED')[0].trim();
    if (bare && bare !== c.text) extra += `<p class="note"><span class="k">became</span> ${esc(bare)}</p>`;
  }
  return `<p class="fate">${parts.join(' · ')}</p>${extra}`;
}

function sectionDisputes() {
  const st = S.stats || {};
  const readingsIn = B.first['02'] != null && ui.step >= B.first['02'];
  const counts = {
    disputed: (S.claims || []).filter((c) => on(`c:${c.id}`) && revealedDisputers(c).length).length,
    all: (S.claims || []).filter((c) => on(`c:${c.id}`)).length,
    elevated: on('art') ? st.elevated : 0,
    omitted: on('art') ? st.omitted : 0,
  };
  let filter = ui.filter;
  let note = '';
  if (st.disputed === 0) { filter = 'all'; note = 'no reader disputed anything in this run'; }
  else if (filter === 'disputed' && !readingsIn) { filter = 'all'; note = 'readers have not cross-read yet'; }

  const finalCount = (c) => (c.disputed_by || []).length;
  const seatOrder = Object.fromEntries((S.seats || []).map((s, i) => [s.id, i]));
  const rows = (S.claims || [])
    .filter((c) => on(`c:${c.id}`))
    .filter((c) => filter === 'all'
      || (filter === 'disputed' && revealedDisputers(c).length)
      || (filter === 'elevated' && c.elevated && on('art'))
      || (filter === 'omitted' && c.disposition === 'omitted' && on('art')))
    .sort((a, b) => finalCount(b) - finalCount(a) || seatOrder[a.author] - seatOrder[b.author] || a.index - b.index)
    .map((c) => {
      const disputers = revealedDisputers(c);
      const open = ui.open.has(c.id);
      const head = `<div class="row ${open ? 'is-open' : ''}" data-claim="${esc(c.id)}">
          <span class="who">${seatName(c.author)}</span><span class="txt">${esc(c.text)}</span><span class="mark">${rowMark(c, disputers)}</span></div>`;
      if (!open) return head;
      return `<div class="open" data-open="${esc(c.id)}">${head}<div class="open-body">${voiceBlocks(c, disputers)}${fateLine(c)}</div></div>`;
    }).join('');

  const pairLines = mode === 'live' && !(S.claims || []).some((c) => (c.disagreements || []).length)
    ? (S.pairs || []).map((p) => `<p class="mono small dim">${seatName(p.reader)} read ${seatName(p.target)} · ${p.endorsed} endorsed · ${p.disputed} disputed · ${p.missing} missing</p>`).join('')
    : '';
  const tab = (key, label) => `<button class="tab ${filter === key ? 'on' : ''}" data-filter="${key}">${label} · ${counts[key]}</button>`;
  return `
    <div class="filters">${tab('disputed', 'disputed')}${tab('all', 'all')}${tab('elevated', 'elevated')}${tab('omitted', 'omitted')}</div>
    ${note ? `<p class="caption">${esc(note)}</p>` : ''}
    ${st.missing ? `<p class="caption dimmer">${st.missing} ${st.missing === 1 ? 'claim readers said was missing is' : 'claims readers said were missing are'} not shown</p>` : ''}
    ${S.hydrationNote ? `<p class="caption critique">${esc(S.hydrationNote)}</p>` : ''}
    ${pairLines}
    <div class="rows">${rows || '<p class="caption">nothing in this filter yet</p>'}</div>`;
}

// ---------- 03 · dialogue ----------
function sectionDialogue() {
  const elevatedTurns = new Set((S.elevations || []).flatMap((e) => e.converge_turn_ids || []));
  const turns = (S.turns || []).filter((t) => t.channel !== 'critique' && on(`t:${t.id}`)).map((t) => `
    <div class="turn"><div class="k">${seatName(t.speaker)} · <span class="${t.channel}">${t.channel}</span> · ${t.target ? seatName(t.target) : 'all'}${elevatedTurns.has(t.id) && on('art') ? ' · <span class="converge">✦ elevated</span>' : ''}</div>
      <p>${esc(t.content)}</p></div>`).join('');
  const crit = (S.stats || {}).critiques || 0;
  return `<p class="caption">${crit} critique${crit === 1 ? ' is' : 's are'} on ${crit === 1 ? 'its claim' : 'their claims'} in 02.</p>${turns || '<p class="caption">no augment or converge turns yet</p>'}`;
}

// ---------- 04 · synthesis ----------
function parityLine() {
  if (!on('metrics')) return '<p class="mono small dim">parity · pending</p>';
  if (!S.metrics) {
    return `<p class="mono small dim">parity is undefined at ${(S.seats || []).length < 3 ? 'two seats' : 'this session'}</p>`;
  }
  const rec = S.metrics_recomputed ? ' · <span class="dimmer">recomputed</span>' : '';
  const readings = S.interpretations || [];
  if (readings.length) {
    return readings.map((i) => `<p class="mono small"><span class="converge">flag</span> · ${seatName(i.model)} · ${esc(DIMENSION_WORDS[i.dimension] || i.dimension)} · ${esc(READING_WORDS[i.reading] || i.reading)}${rec}<br><span class="serif-gloss">${esc(i.gloss)}</span></p>`).join('');
  }
  const shares = S.metrics.distinct_claim_share || {};
  const low = Object.entries(shares).sort((a, b) => a[1] - b[1])[0];
  return `<p class="mono small">parity <span class="present">holds</span> · threshold ${S.metrics.parity_threshold} · lowest share ${low ? `${esc(labelOf(low[0]))} ${low[1].toFixed(2)}` : '—'}${rec}</p>`;
}

function sectionSynthesis() {
  if (!on('art')) return '<p class="caption">synthesis has not run yet</p>';
  const st = S.stats || {};
  const present = st.claims - st.omitted - st.modified;
  const traced = ((S.synthesis || {}).trace || []).length;
  const ledger = `<p class="mono small">${traced} of ${st.claims} traced · <span class="present">${present} present</span> · <span class="converge">${st.modified} modified</span> · <span class="critique">${st.omitted} omitted</span></p>`;
  const lines = (S.synthesis_lines || []).filter((l) => on(`l:${l.index}`)).map((l) => {
    const fam = TAG_FAMILY[l.tag];
    const c = l.claim_id ? claimById(l.claim_id) : null;
    const n = c ? (c.disputed_by || []).length : 0;
    const mark = n ? ` <a href="#" class="claim-link critique mono" data-claim-link="${esc(c.id)}">×${n}</a>` : '';
    return `<div class="line ${ui.flash === l.index ? 'flash' : ''}" data-line-index="${l.index}">
      <span class="tag mono" style="color:${fam ? familyColor(fam) : 'var(--dim)'}">[${esc(l.tag)}]</span>
      <span>${esc(l.text)}${mark}${l.hedge ? `<span class="hedge">${esc(l.hedge)}</span>` : ''}</span></div>`;
  }).join('');
  const omitted = (S.claims || []).filter((c) => c.disposition === 'omitted').map((c) => `
    <p class="note">${seatName(c.author)} ${esc(c.text)}<br><span class="k">omitted because</span> ${esc(c.omission_reason || '')}</p>`).join('');
  return `${ledger}${parityLine()}<div class="output">${lines}</div>${omitted ? `<div class="omitted"><div class="k">omitted</div>${omitted}</div>` : ''}`;
}

const RENDER = { '00': sectionFeed, '01': sectionSeats, '02': sectionDisputes, '03': sectionDialogue, '04': sectionSynthesis };

// ---------- frame ----------
function frameHtml() {
  const seats = (S.seats || []).map((s) => `<span class="mast-seat">${seatName(s.id)} <span class="mono dimmer">${esc(s.id)}</span></span>`).join('');
  const rail = SECTIONS.map((sec) => `
    <div class="rail-item" data-sec="${sec}"><button class="rail-sel" data-select="${sec}">${sec} · ${TITLES[sec]}</button><button class="rail-pin" data-pin="${sec}" title="pin beside the active section">⌖</button></div>`).join('');
  return `
  <div class="session-page">
    <header class="mast">
      <a class="k link" href="/">← record</a>
      <span class="k ${mode === 'live' ? 'critique' : ''}">${mode === 'live' ? 'Live' : 'Replay'}</span>
      <span class="prompt" title="click to expand">${esc(S.prompt)}</span>
      <span class="mono small dim">${esc(S.id && S.id !== 'live' ? runDate(S.id) : '')}</span>
    </header>
    <div class="mast-seats">${seats}<span class="mono small dimmer">${esc(S.id || '')}</span></div>
    <div class="body">
      <nav class="rail">${rail}</nav>
      <div class="panes" id="gl-panes"></div>
    </div>
    <footer class="transport" id="gl-transport"></footer>
  </div>`;
}

function paneHtml(sec, pinned) {
  return `<section class="pane ${pinned ? 'pinned' : ''}" data-pane="${sec}">
    <div class="hd"><span class="n">${sec}</span><span class="t">${LONG[sec]}</span>${pinned ? '<button class="x" data-unpin="1" title="unpin (Esc)">⌖ pinned</button>' : ''}</div>
    <div class="bd" data-scroll="${sec}">${RENDER[sec]()}</div></section>`;
}

function renderPanes() {
  const panes = root.querySelector('#gl-panes');
  if (!panes) return;
  // remember scroll before replacing
  panes.querySelectorAll('[data-scroll]').forEach((el) => { ui.scroll[el.getAttribute('data-scroll')] = el.scrollTop; });
  const visible = [ui.active].concat(ui.pinned && ui.pinned !== ui.active ? [ui.pinned] : []);
  panes.className = `panes ${visible.length === 2 ? 'two' : 'one'}`;
  panes.innerHTML = visible.map((sec, i) => paneHtml(sec, i === 1)).join('');
  panes.querySelectorAll('[data-scroll]').forEach((el) => {
    const sec = el.getAttribute('data-scroll');
    el.scrollTop = ui.scroll[sec] || 0;
    el.addEventListener('scroll', () => { ui.scroll[sec] = el.scrollTop; }, { passive: true });
  });
  root.querySelectorAll('.rail-item').forEach((item) => {
    const sec = item.getAttribute('data-sec');
    item.classList.toggle('on', sec === ui.active);
    item.classList.toggle('pinned', sec === ui.pinned && sec !== ui.active);
    item.classList.toggle('pending', B.first[sec] != null && ui.step < B.first[sec]);
  });
}

export function renderSection(sec) {
  const el = root && root.querySelector(`[data-scroll="${sec}"]`);
  if (!el) return;
  const top = el.scrollTop;
  el.innerHTML = RENDER[sec]();
  el.scrollTop = top;
}

function transportHtml() {
  if (mode === 'live') return '';
  return `
    <button class="tbtn" data-act="restart" title="restart">↺</button>
    <button class="tbtn play" data-act="toggle">${ui.playing ? '❚❚' : '▶'}</button>
    <span class="mono small phase">${esc(phaseLabel(B, ui.step))}</span>
    <input id="gl-scrub" type="range" min="0" max="${B.N}" value="${ui.step}" aria-label="replay position">
    <span class="mono small dim steps">${ui.step} / ${B.N}</span>
    <button class="tbtn speed" data-act="speed">${ui.speed}×</button>`;
}

function renderTransport() {
  const t = root.querySelector('#gl-transport');
  if (!t || mode === 'live') return;
  const scrub = t.querySelector('#gl-scrub');
  if (!scrub) { t.innerHTML = transportHtml(); return; }
  if (document.activeElement !== scrub) scrub.value = ui.step;
  t.querySelector('.phase').textContent = phaseLabel(B, ui.step);
  t.querySelector('.steps').textContent = `${ui.step} / ${B.N}`;
  t.querySelector('.play').textContent = ui.playing ? '❚❚' : '▶';
  t.querySelector('.speed').textContent = `${ui.speed}×`;
}

// ---------- navigation ----------
function writeHash() {
  const h = `#${ui.active}${ui.pinned && ui.pinned !== ui.active ? `+${ui.pinned}` : ''}`;
  if (location.hash !== h) history.replaceState(null, '', h);
}

function readHash() {
  const m = /^#(0[0-4])(?:\+(0[0-4]))?$/.exec(location.hash);
  if (!m) return;
  ui.active = m[1];
  ui.pinned = m[2] && m[2] !== m[1] ? m[2] : null;
}

export function selectSection(sec) {
  if (sec === ui.pinned) ui.pinned = ui.active;   // swap
  ui.active = sec;
  writeHash(); renderPanes();
}

export function togglePin(sec) {
  if (sec === ui.active) return;
  ui.pinned = ui.pinned === sec ? null : sec;
  writeHash(); renderPanes();
}

function visible(sec) { return ui.active === sec || ui.pinned === sec; }

export function jumpToLine(index) {
  if (!visible('04')) ui.pinned = '04';
  ui.flash = index;
  writeHash(); renderPanes();
  const el = root.querySelector(`[data-line-index="${index}"]`);
  if (el) el.scrollIntoView({ block: 'center' });
  setTimeout(() => { ui.flash = null; const e = root.querySelector('.line.flash'); if (e) e.classList.remove('flash'); }, 1400);
}

export function openClaim(id) {
  if (!visible('02')) ui.active = '02';
  const c = claimById(id);
  if (c && ui.filter === 'disputed' && !revealedDisputers(c).length) ui.filter = 'all';
  ui.open.add(id);
  writeHash(); renderPanes();
  const el = root.querySelector(`[data-open="${CSS.escape(id)}"]`);
  if (el) el.scrollIntoView({ block: 'start' });
}

// ---------- replay ----------
function setStep(step) {
  ui.step = Math.max(0, Math.min(B.N, step));
  lsSet(`gl-replay-step-${S.id}`, ui.step);
  renderPanes(); renderTransport();
}
function stop() { if (timer) clearInterval(timer); timer = null; ui.playing = false; renderTransport(); }
function play() {
  if (ui.step >= B.N) ui.step = 0;
  ui.playing = true;
  if (timer) clearInterval(timer);
  timer = setInterval(() => { if (ui.step >= B.N) { stop(); return; } setStep(ui.step + 1); }, PLAY_PERIOD_MS / ui.speed);
  renderTransport();
}

// ---------- events (attached once per render) ----------
function wire() {
  root.onclick = (e) => {
    const t = e.target;
    const sel = t.closest('[data-select]');
    if (sel) { selectSection(sel.getAttribute('data-select')); return; }
    const pin = t.closest('[data-pin]');
    if (pin) { togglePin(pin.getAttribute('data-pin')); return; }
    if (t.closest('[data-unpin]')) { ui.pinned = null; writeHash(); renderPanes(); return; }
    const f = t.closest('[data-filter]');
    if (f) { ui.filter = f.getAttribute('data-filter'); renderSection('02'); return; }
    const line = t.closest('[data-line]');
    if (line) { e.preventDefault(); jumpToLine(Number(line.getAttribute('data-line'))); return; }
    const cl = t.closest('[data-claim-link]');
    if (cl) { e.preventDefault(); openClaim(cl.getAttribute('data-claim-link')); return; }
    const row = t.closest('.row[data-claim]');
    if (row) {
      const id = row.getAttribute('data-claim');
      if (ui.open.has(id)) ui.open.delete(id); else ui.open.add(id);
      renderSection('02'); return;
    }
    if (t.closest('.prompt')) { t.closest('.prompt').classList.toggle('expanded'); return; }
    const act = t.closest('[data-act]');
    if (!act) return;
    const a = act.getAttribute('data-act');
    if (a === 'restart') { stop(); setStep(0); play(); }
    else if (a === 'toggle') { if (ui.playing) stop(); else play(); }
    else if (a === 'speed') { ui.speed = ui.speed >= 4 ? 1 : ui.speed * 2; if (ui.playing) play(); else renderTransport(); }
  };
  root.oninput = (e) => {
    if (e.target.id !== 'gl-scrub') return;
    const v = Number(e.target.value) || 0;   // read before stop() redraws the transport
    stop(); setStep(v);
  };
  root.onmouseover = (e) => { const item = e.target.closest('.rail-item'); ui.hovered = item ? item.getAttribute('data-sec') : null; };
  document.onkeydown = (e) => {
    if (e.target && /^(INPUT|TEXTAREA)$/.test(e.target.tagName)) return;
    if (/^[0-4]$/.test(e.key)) { selectSection(`0${e.key}`); }
    else if (e.key === 'p') { if (ui.hovered) togglePin(ui.hovered); else if (ui.pinned) { ui.pinned = null; writeHash(); renderPanes(); } }
    else if (e.key === 'Escape') { ui.pinned = null; writeHash(); renderPanes(); }
  };
}

export function renderSession(rootEl, session, opts = {}) {
  root = rootEl; S = session; mode = opts.mode || 'replay';
  if (timer) clearInterval(timer);
  timer = null; ui.playing = false;
  B = buildBeats(S);
  readHash();
  if (mode === 'live') {
    ui.step = B.N;
  } else {
    const saved = Number(lsGet(`gl-replay-step-${S.id}`, B.N));
    ui.step = Number.isFinite(saved) ? Math.max(0, Math.min(B.N, saved)) : B.N;
  }
  if ((S.stats || {}).disputed === 0) ui.filter = 'all';
  root.innerHTML = frameHtml();
  root.querySelector('#gl-transport').innerHTML = transportHtml();
  wire();
  renderPanes();
  writeHash();
}

// Live mode: rebuild beats and re-render only the named sections.
export function refreshLive(session, sections) {
  S = session; B = buildBeats(S); ui.step = B.N;
  if (!root.querySelector('#gl-panes')) { renderSession(root, S, { mode: 'live' }); return; }
  const promptEl = root.querySelector('.prompt');
  if (promptEl && promptEl.textContent !== S.prompt) { renderSession(root, S, { mode: 'live' }); return; }
  (sections || SECTIONS).forEach((sec) => { if (visible(sec)) renderSection(sec); });
  root.querySelectorAll('.rail-item').forEach((item) => {
    const sec = item.getAttribute('data-sec');
    item.classList.toggle('pending', B.first[sec] != null && ui.step < B.first[sec]);
  });
}

export function appendStream(modelId, delta) {
  const el = root && root.querySelector(`[data-stream="${CSS.escape(modelId)}"]`);
  if (el) { el.appendChild(document.createTextNode(delta)); el.scrollTop = el.scrollHeight; }
}

// ---------- route ----------
export async function renderSessionRoute(rootEl, id, fetchBundle) {
  if (id === 'live') {
    const { startLive } = await import('./live.js');
    startLive(rootEl);
    return;
  }
  const bundle = await fetchBundle();
  const session = (bundle.sessions || []).find((s) => s.id === id);
  if (session) { renderSession(rootEl, session, { mode: 'replay' }); return; }
  const bad = (bundle.unloadable || []).find((u) => u.id === id);
  rootEl.innerHTML = `<div class="empty"><div class="k">Golden Lattice</div>
    <div class="empty-msg">${bad ? `this session could not be loaded: ${esc(bad.error)}` : 'session not found'}</div>
    <a class="k link" href="/">← record</a></div>`;
}
