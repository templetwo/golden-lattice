// The Session page (spec §6.2, reading pass §12): rail with one window and a
// pin. Renders only from the normalized session shape; computes no metrics.
import { esc, runDate, lsGet, lsSet } from './util.js';
import { labelOf, colorOf, familyColor } from './seats.js';
import { buildBeats, disputeBeat, phaseLabel, phaseStops, PLAY_PERIOD_MS, SECTIONS, SECTION_NAMES } from './beats.js';
import {
  disputesView, feedSkippedText, parityLineHtml, openingStep, critiquesCaption, filterForClaim,
  challengeLabel, outcomeWord, stepDisagreement, stopLayout, splitColumns,
} from './notes.js';
import { phaseActivity } from './stream.js';
import { renderMarkdown } from './markdown.js';

const md = (text) => `<div class="md">${renderMarkdown(text)}</div>`;

const TAG_FAMILY = { F: 'fable', O: 'opus', S: 'sonnet', H: 'haiku' };
const SPLIT_MIN = 0.28;
const SPLIT_MAX = 0.72;

let S = null;   // the normalized session being shown
let B = null;   // its beats
let root = null;
let mode = 'replay';
let timer = null;
const ui = {
  active: '02', pinned: '04', open: new Set(), filter: 'disputed',
  scroll: {}, step: 0, playing: false, speed: 1, hovered: null,
  selected: null,       // the claim whose row and output line stay highlighted
  returnTo: null,       // claim to go back to after following "View in final answer"
  reading: true,        // a completed run opens to read; replay is a separate choice
  split: Number(lsGet('gl-split', '0.56')) || 0.56,
  details: lsGet('gl-details', '0') === '1',
};

// ---------- small renderers ----------
const seatName = (id) => `<span class="seat-name" style="color:${colorOf(id)}">${esc(labelOf(id))}</span>`;
const on = (key) => B.at[key] != null && B.at[key] <= ui.step;

function claimById(id) { return (S.claims || []).find((c) => c.id === id); }

function revealedDisputers(c) {
  return (c.disputed_by || []).filter((r) => disputeBeat(B, c, r) <= ui.step);
}

// ---------- 00 · Research ----------
function sectionFeed() {
  const p0 = S.phase0;
  if (!p0) return `<p class="lead">${esc(feedSkippedText(!!S.live))}</p>`;
  const props = (p0.proposals || []).map((p, i) => on(`prop${i}`) ? `
    <div class="prop"><div class="k">${seatName(p.model)} · proposes ${p.count}</div>
      ${(p.queries || []).map((q) => `<div class="q mono">${esc(q)}</div>`).join('')}</div>` : '').join('');
  const results = (p0.results || []).map((r, i) => on(`res${i}`) ? `
    <div class="res"><div class="mono q-ok">✓ ${esc(r.query)}</div><div class="caption">${esc(r.preview)}</div></div>` : '').join('');
  const failed = (p0.failed || []).map((f, i) => on(`fail${i}`) ? `
    <div class="res fail"><div class="mono q-no">✗ ${esc(f.query)}</div><div class="caption">${esc(f.reason)}</div></div>` : '').join('');
  return `
    ${on('g') ? `<p class="k">grounding · ${esc(p0.grounding)} · deterministic, not a model action</p>` : ''}
    <div class="props">${props}</div>
    ${on('frozen') ? `<p class="k">rule-based union · ${p0.unique} unique → <span class="converge">feed frozen · ${p0.frozen} entries</span></p>` : ''}
    ${results}${failed}`;
}

// ---------- 01 · Initial answers ----------
function sectionSeats() {
  const cols = (S.seats || []).map((seat) => {
    const refl = (S.reflections || {})[seat.id] || {};
    const strong = refl.strong && claimById(refl.strong);
    const weak = refl.weak && claimById(refl.weak);
    const shown = on(`r:${seat.id}`);
    const p1 = (S.phase1 || {})[seat.id] || {};
    const w = (S.streams || {})[`phase_1:${seat.id}`];
    let answer = p1.response || '';
    if (S.live && w && w.phase === 'phase_1' && w.answer) answer = w.answer;
    const answerShown = S.live ? !!answer : (answer && on(`r:${seat.id}`) || (answer && (S.claims || []).some((c) => c.author === seat.id && on(`c:${c.id}`))));
    const answerHtml = answerShown ? `<div class="answer"><div class="k">initial answer</div>${md(answer)}</div>` : '';
    return `
      <div class="seat-col">
        <div class="k">${seatName(seat.id)} · ${esc(seat.focus || '')} · confidence ${Number(seat.conf || 0).toFixed(2)} · ${seat.claim_count} claims</div>
        ${answerHtml}
        ${shown ? `
          <div class="refl">${md(refl.just || '')}</div>
          ${strong ? `<p class="small-serif"><span class="k">strongest</span> ${esc(strong.text)}</p>` : ''}
          ${weak ? `<p class="small-serif"><span class="k">weakest</span> ${esc(weak.text)}</p>` : ''}` : '<p class="caption">self-reflection not yet in</p>'}
      </div>`;
  }).join('');
  return `<div class="seat-grid">${cols}</div>`;
}

// ---------- 02 · Disagreements ----------
function rowMark(c, disputers) {
  if (disputers.length) {
    const label = challengeLabel(disputers.length);
    return `<span class="critique" role="img" aria-label="${esc(label)}" title="${esc(label)}">×${disputers.length}</span>`;
  }
  if (c.elevated && on('art')) return '<span class="converge" role="img" aria-label="Elevated by agreement" title="Elevated by agreement">✦</span>';
  if (c.disposition === 'omitted' && on('art')) return '<span class="dim" role="img" aria-label="Omitted from the final answer" title="Omitted from the final answer">—</span>';
  return '';
}

// The outcome comes first in an expanded claim (spec §12).
function outcomeBlock(c) {
  if (!on('art')) return '<div class="outcome"><span class="outcome-word dim">Not yet in the final answer</span></div>';
  const color = c.disposition === 'omitted' ? 'critique' : c.disposition === 'modified' ? 'converge' : 'present';
  let detail = '';
  if (c.disposition === 'modified' && c.modified_text) {
    const bare = c.modified_text.split('[DISPUTED')[0].trim();
    if (bare && bare !== c.text) detail += `<p class="note"><span class="k">now reads</span> ${esc(bare)}</p>`;
  }
  if (c.disputed_hedge) detail += `<p class="hedge">${esc(c.disputed_hedge)}</p>`;
  if (c.disposition === 'omitted' && c.omission_reason) detail += `<p class="note"><span class="k">omitted because</span> ${esc(c.omission_reason)}</p>`;
  if (c.surfaced_note) detail += `<p class="note"><span class="k">surfaced disagreement</span> ${esc(c.surfaced_note)}</p>`;
  return `<div class="outcome"><span class="outcome-word ${color}">${esc(outcomeWord(c.disposition))}</span><span class="outcome-tail"> in the final answer</span>${detail}</div>`;
}

function challengeBlocks(c, disputers) {
  const cross = disputers.flatMap((r) => (c.disagreements || [])
    .filter((d) => d.reader === r && on(`x:${r}>${c.author}`))
    .map((d) => `<div class="voice">${seatName(r)}${md(d.reason)}</div>`));
  const later = disputers.flatMap((r) => (c.critiques || [])
    .filter((k) => k.speaker === r && on(`t:${k.turn_id}`))
    .map((k) => {
      const after = (c.agreed_by || []).includes(r) && !(c.disagreements || []).some((d) => d.reader === r)
        ? ' <span class="k">after endorsing it in cross-reading</span>' : '';
      return `<div class="voice">${seatName(r)}${after}${md(k.content)}</div>`;
    }));
  if (!cross.length && !later.length) return '<p class="caption">No reader challenged this claim.</p>';
  return `<div class="story-h">Challenged by</div>
    ${cross.length ? `<div class="phase-h">During cross-reading</div>${cross.join('')}` : ''}
    ${later.length ? `<div class="phase-h">Later in discussion</div>${later.join('')}` : ''}`;
}

function endorsedLine(c, disputers) {
  const endorsers = (c.agreed_by || []).filter((r) => !disputers.includes(r) && on(`x:${r}>${c.author}`));
  return endorsers.length ? `<p class="endorsed"><span class="story-h inline">Endorsed by</span> ${endorsers.map(seatName).join(' · ')}</p>` : '';
}

function viewInAnswer(c) {
  if (!on('art') || c.synthesis_line == null) return '';
  return `<p class="view-answer"><a href="#" class="line-link" data-line="${c.synthesis_line}" data-from="${esc(c.id)}">View in final answer → line ${c.synthesis_line + 1}</a></p>`;
}

function disputedOrder() {
  const finalCount = (c) => (c.disputed_by || []).length;
  const seatOrder = Object.fromEntries((S.seats || []).map((s, i) => [s.id, i]));
  return (S.claims || [])
    .filter((c) => on(`c:${c.id}`))
    .sort((a, b) => finalCount(b) - finalCount(a) || seatOrder[a.author] - seatOrder[b.author] || a.index - b.index);
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
  const view = disputesView({ session: S, readingsIn, filter: ui.filter });
  const filter = view.filter;
  const note = view.note || '';

  const rows = disputedOrder()
    .filter((c) => filter === 'all'
      || (filter === 'disputed' && revealedDisputers(c).length)
      || (filter === 'elevated' && c.elevated && on('art'))
      || (filter === 'omitted' && c.disposition === 'omitted' && on('art')))
    .map((c) => {
      const disputers = revealedDisputers(c);
      const open = ui.open.has(c.id);
      const sel = ui.selected === c.id ? ' selected' : '';
      const head = `<div class="row${open ? ' is-open' : ''}${sel}" data-claim="${esc(c.id)}" role="button" tabindex="0" aria-expanded="${open}">
          <span class="who">${seatName(c.author)}</span><span class="txt">${esc(c.text)}</span><span class="mark">${rowMark(c, disputers)}</span></div>`;
      if (!open) return head;
      return `<div class="open${sel}" data-open="${esc(c.id)}">${head}<div class="open-body">
        ${outcomeBlock(c)}${challengeBlocks(c, disputers)}${endorsedLine(c, disputers)}${viewInAnswer(c)}</div></div>`;
    }).join('');

  const pairLines = mode === 'live' && !(S.claims || []).some((c) => (c.disagreements || []).length)
    ? (S.pairs || []).map((p) => `<p class="k">${seatName(p.reader)} read ${seatName(p.target)} · ${p.endorsed} endorsed · ${p.disputed} disputed · ${p.missing} missing</p>`).join('')
    : '';
  const tab = (key, label) => `<button class="tab ${filter === key ? 'on' : ''}" data-filter="${key}">${label} · ${counts[key]}</button>`;
  const stepper = counts.disputed ? `<div class="stepper">
      <button class="step-btn" data-step-dis="-1">‹ Previous disagreement</button>
      <button class="step-btn" data-step-dis="1">Next disagreement ›</button></div>` : '';
  return `
    <div class="dis-head">
      <div class="filters">${tab('disputed', 'Disputed')}${tab('all', 'All')}${tab('elevated', 'Elevated')}${tab('omitted', 'Omitted')}</div>
      ${stepper}
    </div>
    ${note ? `<p class="caption">${esc(note)}</p>` : ''}
    ${st.missing ? `<p class="caption dimmer">${st.missing} ${st.missing === 1 ? 'claim readers said was missing is' : 'claims readers said were missing are'} not shown</p>` : ''}
    ${S.hydrationNote ? `<p class="caption critique">${esc(S.hydrationNote)}</p>` : ''}
    ${pairLines}
    <div class="rows">${rows || '<p class="caption">nothing in this filter yet</p>'}</div>`;
}

// ---------- 03 · Discussion ----------
function sectionDialogue() {
  const elevatedTurns = new Set((S.elevations || []).flatMap((e) => e.converge_turn_ids || []));
  const turns = (S.turns || []).filter((t) => t.channel !== 'critique' && on(`t:${t.id}`)).map((t) => `
    <div class="turn"><div class="k">${seatName(t.speaker)} · <span class="${t.channel}">${t.channel}</span> · ${t.target ? seatName(t.target) : 'all'}${elevatedTurns.has(t.id) && on('art') ? ' · <span class="converge">✦ elevated</span>' : ''}</div>
      ${md(t.content)}</div>`).join('');
  const loose = (S.unattached_critiques || []).filter((k) => on(`t:${k.turn_id}`)).map((k) => `
    <div class="turn"><div class="k">${seatName(k.speaker)} · <span class="critique">critique</span> · ${k.target ? seatName(k.target) : 'all'} · <span class="dim">no Phase 1 claim</span></div>
      ${md(k.content)}</div>`).join('');
  return `<p class="caption">${esc(critiquesCaption(S.stats))}</p>${turns || '<p class="caption">no augment or converge turns yet</p>'}${loose}`;
}

// ---------- 04 · Final answer ----------
function parityLine() {
  if (!on('metrics')) return '<p class="k">parity · pending</p>';
  return parityLineHtml(S);
}

function sectionSynthesis() {
  if (!on('art')) return '<p class="caption">the final answer has not been composed yet</p>';
  const st = S.stats || {};
  const present = st.claims - st.omitted - st.modified;
  const traced = ((S.synthesis || {}).trace || []).length;
  const back = ui.returnTo && claimById(ui.returnTo)
    ? `<div class="back-bar"><button class="back-btn" data-back="1">← Back to claim</button> <span class="caption">${seatName(claimById(ui.returnTo).author)}</span></div>` : '';
  const ledger = `<p class="k">${traced} of ${st.claims} traced · <span class="present">${present} retained</span> · <span class="converge">${st.modified} changed</span> · <span class="critique">${st.omitted} omitted</span></p>`;
  const lines = (S.synthesis_lines || []).filter((l) => on(`l:${l.index}`)).map((l) => {
    const fam = TAG_FAMILY[l.tag];
    const c = l.claim_id ? claimById(l.claim_id) : null;
    const n = c ? (c.disputed_by || []).length : 0;
    const label = n ? challengeLabel(n) : '';
    const mark = n ? ` <a href="#" class="claim-link critique mono" data-claim-link="${esc(c.id)}" aria-label="${esc(label)}: open the claim" title="${esc(label)}: open the claim">×${n}</a>` : '';
    const sel = c && ui.selected === c.id ? ' selected' : '';
    return `<div class="line${sel}" data-line-index="${l.index}">
      <span class="tag mono" style="color:${fam ? familyColor(fam) : 'var(--dim)'}">[${esc(l.tag)}]</span>
      <span>${esc(l.text)}${mark}${l.hedge ? `<span class="hedge">${esc(l.hedge)}</span>` : ''}</span></div>`;
  }).join('');
  const omitted = (S.claims || []).filter((c) => c.disposition === 'omitted').map((c) => `
    <p class="note">${seatName(c.author)} ${esc(c.text)}<br><span class="k">omitted because</span> ${esc(c.omission_reason || '')}</p>`).join('');
  return `${back}${ledger}${parityLine()}<div class="output">${lines}</div>${omitted ? `<div class="omitted"><div class="k">omitted</div>${omitted}</div>` : ''}`;
}

const RENDER = { '00': sectionFeed, '01': sectionSeats, '02': sectionDisputes, '03': sectionDialogue, '04': sectionSynthesis };

function isPending(sec) {
  if (B.first[sec] == null) return !!(S && S.live);
  return ui.step < B.first[sec];
}

// ---------- frame ----------
function frameHtml() {
  const seats = (S.seats || []).map((s) => `<span class="mast-seat">${seatName(s.id)} <span class="dim">${s.claim_count} claims</span></span>`).join('');
  const ids = (S.seats || []).map((s) => `<div>${seatName(s.id)} <span class="mono">${esc(s.id)}</span>${s.legacy ? ' <span class="dim">legacy id</span>' : ''}</div>`).join('');
  const rail = SECTIONS.map((sec) => `
    <div class="rail-item" data-sec="${sec}"><button class="rail-sel" data-select="${sec}">${sec} · ${SECTION_NAMES[sec]}</button><button class="rail-pin" data-pin="${sec}" title="Pin beside the active section" aria-label="Pin ${SECTION_NAMES[sec]} beside the active section">⌖</button></div>`).join('');
  const modeLabel = mode === 'live' ? 'Live' : 'Run';
  return `
  <div class="session-page">
    <header class="mast">
      <a class="nav-link" href="/">← Record</a>
      <span class="k ${mode === 'live' ? 'critique' : ''}">${modeLabel}</span>
      <span class="prompt" title="click to expand">${esc(S.prompt)}</span>
      <span class="k">${esc(S.id && S.id !== 'live' ? runDate(S.id) : '')}</span>
      <button class="details-btn" data-details="1" aria-expanded="${ui.details}">Details</button>
    </header>
    <div class="mast-seats">${seats}</div>
    ${mode === 'live' ? '<section class="writing" id="gl-writing" aria-live="polite" aria-label="Writing now" hidden><div class="k">writing now</div><div class="writing-cards"></div></section>' : ''}
    <div class="details" ${ui.details ? '' : 'hidden'}><div class="k">model ids</div>${ids}<div class="k" style="margin-top:6px">session</div><div class="mono">${esc(S.id || '')}</div></div>
    <div class="body">
      <nav class="rail" aria-label="Sections">${rail}</nav>
      <div class="panes" id="gl-panes"></div>
    </div>
    <footer class="transport" id="gl-transport"></footer>
  </div>`;
}

function paneHtml(sec, pinned) {
  const actions = pinned
    ? `<button class="pane-btn" data-focus="${sec}">Focus this pane</button><button class="pane-btn converge" data-unpin="1" title="Unpin (Esc)">⌖ Unpin</button>`
    : (ui.pinned && ui.pinned !== ui.active ? `<button class="pane-btn" data-focus="${sec}">Focus this pane</button>` : '');
  return `<section class="pane ${pinned ? 'pinned' : ''}" data-pane="${sec}">
    <div class="hd"><span class="n">${sec}</span><span class="t">${SECTION_NAMES[sec]}</span><span class="hd-actions">${actions}</span></div>
    <div class="bd" data-scroll="${sec}">${RENDER[sec]()}</div></section>`;
}

const WIDE = typeof window !== 'undefined' && window.matchMedia ? window.matchMedia('(min-width: 761px)') : null;

function applySplit(panes) {
  panes.style.gridTemplateColumns = splitColumns({
    two: panes.classList.contains('two'), wide: !!(WIDE && WIDE.matches), split: ui.split,
  });
}

// Crossing the breakpoint recalculates the split, so a narrowed window never
// keeps the desktop grid (review of PR #2 at a297b4a, finding 2).
if (WIDE) {
  const recompute = () => { const panes = root && root.querySelector('#gl-panes'); if (panes) applySplit(panes); };
  WIDE.addEventListener('change', recompute);
  window.addEventListener('resize', recompute, { passive: true });
}

function renderPanes() {
  const panes = root.querySelector('#gl-panes');
  if (!panes) return;
  panes.querySelectorAll('[data-scroll]').forEach((el) => { ui.scroll[el.getAttribute('data-scroll')] = el.scrollTop; });
  const visible = [ui.active].concat(ui.pinned && ui.pinned !== ui.active ? [ui.pinned] : []);
  panes.className = `panes ${visible.length === 2 ? 'two' : 'one'}`;
  panes.innerHTML = visible.length === 2
    ? `${paneHtml(visible[0], false)}<div class="divider" role="separator" aria-orientation="vertical" aria-label="Resize panes" tabindex="0"></div>${paneHtml(visible[1], true)}`
    : paneHtml(visible[0], false);
  applySplit(panes);
  panes.querySelectorAll('[data-scroll]').forEach((el) => {
    const sec = el.getAttribute('data-scroll');
    el.scrollTop = ui.scroll[sec] || 0;
    el.addEventListener('scroll', () => { ui.scroll[sec] = el.scrollTop; }, { passive: true });
  });
  wireDivider(panes);
  root.querySelectorAll('.rail-item').forEach((item) => {
    const sec = item.getAttribute('data-sec');
    item.classList.toggle('on', sec === ui.active);
    item.classList.toggle('pinned', sec === ui.pinned && sec !== ui.active);
    item.classList.toggle('pending', isPending(sec));
  });
}

function wireDivider(panes) {
  const div = panes.querySelector('.divider');
  if (!div) return;
  const setSplit = (v) => {
    ui.split = Math.max(SPLIT_MIN, Math.min(SPLIT_MAX, v));
    lsSet('gl-split', ui.split.toFixed(3));
    applySplit(panes);
  };
  div.addEventListener('pointerdown', (e) => {
    e.preventDefault();
    div.setPointerCapture(e.pointerId);
    const rect = panes.getBoundingClientRect();
    const move = (ev) => setSplit((ev.clientX - rect.left) / rect.width);
    const up = () => { div.removeEventListener('pointermove', move); div.removeEventListener('pointerup', up); };
    div.addEventListener('pointermove', move);
    div.addEventListener('pointerup', up);
  });
  div.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowLeft') { setSplit(ui.split - 0.04); e.preventDefault(); }
    if (e.key === 'ArrowRight') { setSplit(ui.split + 0.04); e.preventDefault(); }
  });
}

export function renderSection(sec) {
  const el = root && root.querySelector(`[data-scroll="${sec}"]`);
  if (!el) return;
  const top = el.scrollTop;
  el.innerHTML = RENDER[sec]();
  el.scrollTop = top;
}

// ---------- transport: read, or replay how it developed ----------
function transportHtml() {
  if (mode === 'live') return '';
  if (ui.reading) {
    return `<span class="k">Reading the completed run</span><span class="spacer"></span>
      <button class="t-btn" data-act="replay">Replay how it developed</button>`;
  }
  const stops = phaseStops(B).map((s) => `<button class="stop-btn ${B.beats[ui.step] && B.beats[ui.step].section === s.sec ? 'on' : ''}" data-stop="${s.step}" data-stop-sec="${s.sec}">${s.sec} · ${s.label}</button>`).join('');
  return `
    <button class="t-btn" data-act="read">← Back to reading</button>
    <div class="stops">${stops}</div>
    <button class="tbtn" data-act="prev" title="Previous step" aria-label="Previous step">‹</button>
    <button class="tbtn play" data-act="toggle" aria-label="${ui.playing ? 'Pause' : 'Play'}">${ui.playing ? '❚❚' : '▶'}</button>
    <button class="tbtn" data-act="next" title="Next step" aria-label="Next step">›</button>
    <input id="gl-scrub" type="range" min="0" max="${B.N}" value="${ui.step}" aria-label="Replay position">
    <span class="k steps">step ${ui.step} of ${B.N} · ${esc(phaseLabel(B, ui.step))}</span>
    <button class="tbtn speed" data-act="speed" aria-label="Playback speed">${ui.speed}×</button>`;
}

function renderTransport() {
  const t = root.querySelector('#gl-transport');
  if (!t || mode === 'live') return;
  const scrub = t.querySelector('#gl-scrub');
  if (ui.reading || !scrub) { t.innerHTML = transportHtml(); return; }
  if (document.activeElement !== scrub) scrub.value = ui.step;
  t.querySelector('.steps').textContent = `step ${ui.step} of ${B.N} · ${phaseLabel(B, ui.step)}`;
  t.querySelector('.play').textContent = ui.playing ? '❚❚' : '▶';
  t.querySelector('.speed').textContent = `${ui.speed}×`;
  const cur = B.beats[ui.step] && B.beats[ui.step].section;
  t.querySelectorAll('.stop-btn').forEach((b) => b.classList.toggle('on', b.textContent.startsWith(cur)));
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

function focusPane(sec) {
  ui.active = sec; ui.pinned = null;
  writeHash(); renderPanes();
}

function visible(sec) { return ui.active === sec || ui.pinned === sec; }

export function jumpToLine(index, fromClaim) {
  if (!visible('04')) ui.pinned = '04';
  if (fromClaim) { ui.selected = fromClaim; ui.returnTo = fromClaim; }
  writeHash(); renderPanes();
  const el = root.querySelector(`[data-line-index="${index}"]`);
  if (el) el.scrollIntoView({ block: 'center' });
}

export function openClaim(id, { keepReturn = false } = {}) {
  if (!visible('02')) ui.active = '02';
  const c = claimById(id);
  if (c) ui.filter = filterForClaim(ui.filter, c, revealedDisputers(c).length);
  ui.open.add(id);
  ui.selected = id;
  if (!keepReturn) ui.returnTo = null;
  writeHash(); renderPanes();
  const el = root.querySelector(`[data-open="${CSS.escape(id)}"]`);
  if (el) el.scrollIntoView({ block: 'start' });
  focusRow(id);
}

function stepDispute(dir) {
  const ids = disputedOrder().filter((c) => revealedDisputers(c).length).map((c) => c.id);
  const next = stepDisagreement(ids, ui.selected, dir);
  if (next) openClaim(next);
}

// ---------- replay ----------
function setStep(step) {
  ui.step = Math.max(0, Math.min(B.N, step));
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
function focusRow(id) {
  const row = root.querySelector(`.row[data-claim="${CSS.escape(id)}"]`);
  if (row) row.focus({ preventScroll: true });
}

function toggleRow(id) {
  if (ui.open.has(id)) ui.open.delete(id); else ui.open.add(id);
  ui.selected = id;
  renderSection('02');
  if (visible('04')) renderSection('04');
  focusRow(id);   // the re-render replaced the row; keep keyboard focus on it
}

function wire() {
  root.onclick = (e) => {
    const t = e.target;
    const sel = t.closest('[data-select]');
    if (sel) { selectSection(sel.getAttribute('data-select')); return; }
    const pin = t.closest('[data-pin]');
    if (pin) { togglePin(pin.getAttribute('data-pin')); return; }
    if (t.closest('[data-unpin]')) { ui.pinned = null; writeHash(); renderPanes(); return; }
    const focus = t.closest('[data-focus]');
    if (focus) { focusPane(focus.getAttribute('data-focus')); return; }
    if (t.closest('[data-details]')) {
      ui.details = !ui.details; lsSet('gl-details', ui.details ? '1' : '0');
      const d = root.querySelector('.details'); if (d) d.hidden = !ui.details;
      t.closest('[data-details]').setAttribute('aria-expanded', String(ui.details));
      return;
    }
    const f = t.closest('[data-filter]');
    if (f) { ui.filter = f.getAttribute('data-filter'); renderSection('02'); return; }
    const sd = t.closest('[data-step-dis]');
    if (sd) { stepDispute(Number(sd.getAttribute('data-step-dis'))); return; }
    const line = t.closest('[data-line]');
    if (line) { e.preventDefault(); jumpToLine(Number(line.getAttribute('data-line')), line.getAttribute('data-from')); return; }
    if (t.closest('[data-back]')) { const id = ui.returnTo; ui.returnTo = null; if (id) openClaim(id); return; }
    const cl = t.closest('[data-claim-link]');
    if (cl) { e.preventDefault(); openClaim(cl.getAttribute('data-claim-link')); return; }
    const row = t.closest('.row[data-claim]');
    if (row) { toggleRow(row.getAttribute('data-claim')); return; }
    if (t.closest('.prompt')) { t.closest('.prompt').classList.toggle('expanded'); return; }
    const stopBtn = t.closest('[data-stop]');
    if (stopBtn) {
      const layout = stopLayout(stopBtn.getAttribute('data-stop-sec'));
      ui.active = layout.active; ui.pinned = layout.pinned;
      stop(); writeHash(); setStep(Number(stopBtn.getAttribute('data-stop')));
      return;
    }
    const act = t.closest('[data-act]');
    if (!act) return;
    const a = act.getAttribute('data-act');
    if (a === 'replay') { ui.reading = false; stop(); setStep(0); renderTransport(); }
    else if (a === 'read') { stop(); ui.reading = true; setStep(B.N); renderTransport(); }
    else if (a === 'prev') { stop(); setStep(ui.step - 1); }
    else if (a === 'next') { stop(); setStep(ui.step + 1); }
    else if (a === 'toggle') { if (ui.playing) stop(); else play(); }
    else if (a === 'speed') { ui.speed = ui.speed >= 4 ? 1 : ui.speed * 2; if (ui.playing) play(); else renderTransport(); }
  };
  root.onkeydown = (e) => {
    const row = e.target.closest && e.target.closest('.row[data-claim]');
    if (row && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); toggleRow(row.getAttribute('data-claim')); }
  };
  root.oninput = (e) => {
    if (e.target.id !== 'gl-scrub') return;
    const v = Number(e.target.value) || 0;   // read before stop() redraws the transport
    stop(); setStep(v);
  };
  root.onmouseover = (e) => { const item = e.target.closest('.rail-item'); ui.hovered = item ? item.getAttribute('data-sec') : null; };
  document.onkeydown = (e) => {
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    if (e.target && /^(INPUT|TEXTAREA|BUTTON)$/.test(e.target.tagName) && e.key !== 'Escape') return;
    if (/^[0-4]$/.test(e.key)) { selectSection(`0${e.key}`); }
    else if (e.key === 'p') { if (ui.hovered) togglePin(ui.hovered); else if (ui.pinned) { ui.pinned = null; writeHash(); renderPanes(); } }
    else if (e.key === 'Escape') { ui.pinned = null; writeHash(); renderPanes(); }
    else if (e.key === 'j') stepDispute(1);
    else if (e.key === 'k') stepDispute(-1);
  };
}

export function renderSession(rootEl, session, opts = {}) {
  root = rootEl; S = session; mode = opts.mode || 'replay';
  if (timer) clearInterval(timer);
  timer = null; ui.playing = false;
  ui.selected = null; ui.returnTo = null; ui.open = new Set(); ui.reading = true;
  B = buildBeats(S);
  readHash();
  ui.step = mode === 'live' ? B.N : openingStep(B);
  ui.filter = (S.stats || {}).disputed === 0 && !S.live ? 'all' : 'disputed';
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
    item.classList.toggle('pending', isPending(sec));
  });
}

// Live: the "Writing now" strip, one card per seat, readable text only.
// One card per streaming request; a request with no readable prose yet
// (tagging, for one) gets no card.
export function updateWriting(stream, text) {
  const strip = root && root.querySelector('#gl-writing');
  if (!strip || !text) return;
  let card = strip.querySelector(`[data-writing="${CSS.escape(stream.key)}"]`);
  if (!card) {
    card = document.createElement('div');
    card.className = 'writing-card';
    card.setAttribute('data-writing', stream.key);
    card.innerHTML = `<div class="writing-hd">${seatName(stream.model)} <span class="k activity"></span>${stream.target ? ` <span class="k">→</span> ${seatName(stream.target)}` : ''}</div><div class="writing-text"></div>`;
    strip.querySelector('.writing-cards').appendChild(card);
  }
  card.querySelector('.activity').textContent = phaseActivity(stream.phase);
  const body = card.querySelector('.writing-text');
  const stick = body.scrollHeight - body.scrollTop - body.clientHeight < 24;
  body.textContent = text || '…';
  if (stick) body.scrollTop = body.scrollHeight;
  strip.hidden = false;
}

export function removeWriting(keys) {
  const strip = root && root.querySelector('#gl-writing');
  if (!strip) return;
  keys.forEach((k) => { const c = strip.querySelector(`[data-writing="${CSS.escape(k)}"]`); if (c) c.remove(); });
  if (!strip.querySelector('.writing-card')) strip.hidden = true;
}

export function clearWriting() {
  const strip = root && root.querySelector('#gl-writing');
  if (!strip) return;
  strip.hidden = true;
  strip.querySelector('.writing-cards').innerHTML = '';
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
    <a class="nav-link" href="/">← Record</a></div>`;
}
