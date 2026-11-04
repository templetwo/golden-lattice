// The Record (spec §6.1): library list with a cross-session strip above it.
// Reads bundle.record only; computes nothing beyond layout.
import { esc, runDate, lsGet, lsSet, READING_WORDS, DIMENSION_WORDS } from './util.js';
import { sortRuns, recordSummary } from './notes.js';
import { FAMILY_ORDER, familyColor } from './seats.js';
import { connectLive } from './live.js';

// Chart geometry inside the SVG viewBox. Labels and legend stay inside it.
const VB = { w: 340, h: 120, x0: 30, x1: 286, y0: 10, y1: 100, max: 0.5 };
const yOf = (share) => VB.y1 - (VB.y1 - VB.y0) * (share / VB.max);

function xOf(i, n) {
  if (n <= 1) return (VB.x0 + VB.x1) / 2;
  return VB.x0 + ((VB.x1 - VB.x0) * i) / (n - 1);
}

function chartCaption(series, threshold) {
  const families = FAMILY_ORDER.filter((f) => series[f]);
  if (!families.length) return 'No runs with parity metrics yet.';
  const n = series[families[0]].length;
  const lowest = {};
  let crossed = 0;
  let measured = 0;
  for (let i = 0; i < n; i++) {
    const pts = families.map((f) => [f, series[f][i].share]).filter(([, s]) => s != null);
    if (!pts.length) continue;
    measured += 1;
    pts.sort((a, b) => a[1] - b[1]);
    lowest[pts[0][0]] = (lowest[pts[0][0]] || 0) + 1;
    if (pts[0][1] < threshold) crossed += 1;
  }
  if (!measured) return 'No runs with parity metrics yet.';
  const [low, count] = Object.entries(lowest).sort((a, b) => b[1] - a[1])[0];
  const floor = Math.min(...series[low].map((p) => p.share).filter((s) => s != null));
  const lead = `${low[0].toUpperCase()}${low.slice(1)} has been the lowest seat in ${count} of ${measured} runs, never below ${floor.toFixed(2)}.`;
  const tail = crossed ? ` A seat crossed the line in ${crossed} run${crossed === 1 ? '' : 's'}.` : ' Nothing has crossed the line.';
  return lead + tail + ' Shares count every claim, including those readers said were missing.';
}

function chartSvg(record) {
  const { series, threshold } = record;
  const families = FAMILY_ORDER.filter((f) => series[f]);
  const n = families.length ? series[families[0]].length : 0;
  const parts = [];
  parts.push(`<line x1="${VB.x0}" y1="${VB.y1}" x2="${VB.x1}" y2="${VB.y1}" class="axis"/>`);
  const ty = yOf(threshold);
  parts.push(`<line x1="${VB.x0}" y1="${ty}" x2="${VB.x1}" y2="${ty}" class="threshold"/>`);
  parts.push(`<text x="${VB.x0 - 4}" y="${VB.y1 + 3}" class="tick" text-anchor="end">0</text>`);
  parts.push(`<text x="${VB.x0 - 4}" y="${ty + 3}" class="tick" text-anchor="end">${String(threshold).replace(/^0/, '')}</text>`);
  parts.push(`<text x="${VB.x0 - 4}" y="${VB.y0 + 3}" class="tick" text-anchor="end">.50</text>`);
  if (n) {
    const ids = series[families[0]].map((p) => p.run_id);
    parts.push(`<text x="${VB.x0}" y="${VB.h - 4}" class="tick">${esc(runDate(ids[0]).slice(0, 5))}</text>`);
    if (n > 1) parts.push(`<text x="${VB.x1}" y="${VB.h - 4}" class="tick" text-anchor="end">${esc(runDate(ids[n - 1]).slice(0, 5))}</text>`);
  }
  // id-change rules, then lines, then end markers and direct labels.
  for (const f of families) {
    series[f].forEach((p, i) => {
      if (p.id_changed) parts.push(`<line x1="${xOf(i, n)}" y1="${VB.y0}" x2="${xOf(i, n)}" y2="${VB.y1}" class="idchange"/>`);
    });
  }
  const labelYs = [];
  for (const f of families) {
    let seg = [];
    const segs = [];
    series[f].forEach((p, i) => {
      if (p.share == null) { if (seg.length) segs.push(seg); seg = []; return; }
      seg.push(`${xOf(i, n).toFixed(1)},${yOf(p.share).toFixed(1)}`);
    });
    if (seg.length) segs.push(seg);
    for (const s of segs) {
      parts.push(`<polyline points="${s.join(' ')}" fill="none" stroke="${familyColor(f)}" stroke-width="2" stroke-linejoin="round"/>`);
    }
    const lastIdx = series[f].map((p) => p.share != null).lastIndexOf(true);
    if (lastIdx >= 0) {
      const x = xOf(lastIdx, n);
      const y = yOf(series[f][lastIdx].share);
      parts.push(`<circle cx="${x}" cy="${y}" r="4" fill="var(--bg)" stroke="${familyColor(f)}" stroke-width="2"/>`);
      labelYs.push([f, y]);
    }
  }
  // Direct labels, nudged apart so they never collide.
  labelYs.sort((a, b) => a[1] - b[1]);
  let prev = -Infinity;
  for (const [f, y] of labelYs) {
    const ly = Math.max(y + 3, prev + 10);
    prev = ly;
    parts.push(`<text x="${VB.x1 + 8}" y="${ly}" class="dlabel">${f.toUpperCase()}</text>`);
  }
  // Hover columns.
  for (let i = 0; i < n; i++) {
    const w = n > 1 ? (VB.x1 - VB.x0) / (n - 1) : VB.x1 - VB.x0;
    parts.push(`<rect class="hit" data-i="${i}" x="${xOf(i, n) - w / 2}" y="${VB.y0}" width="${w}" height="${VB.y1 - VB.y0}"/>`);
  }
  const caption = chartCaption(series, threshold);
  return {
    caption,
    svg: `<svg class="chart" viewBox="0 0 ${VB.w} ${VB.h}" role="img" aria-label="${esc(caption)}">${parts.join('')}</svg>`,
  };
}

function wireChartTooltip(root, record) {
  const cell = root.querySelector('.cell-chart');
  const tip = root.querySelector('.cell-chart .tip');
  if (!cell || !tip) return;
  const families = FAMILY_ORDER.filter((f) => record.series[f]);
  cell.querySelectorAll('.hit').forEach((hit) => {
    hit.addEventListener('mouseenter', () => {
      const i = Number(hit.getAttribute('data-i'));
      const runId = record.series[families[0]][i].run_id;
      const rows = families.map((f) => {
        const p = record.series[f][i];
        const share = p.share == null ? '—' : p.share.toFixed(2);
        return `<div><span style="color:${familyColor(f)}">${f.toUpperCase()}</span> ${share} <span class="dim">${esc(p.model_id || 'not seated')}</span></div>`;
      }).join('');
      tip.innerHTML = `<div class="dim">${esc(runDate(runId))}</div>${rows}`;
      tip.hidden = false;
      const r = hit.getBoundingClientRect();
      const c = cell.getBoundingClientRect();
      const left = Math.min(Math.max(0, r.left - c.left + r.width / 2 - 90), c.width - 180);
      tip.style.left = `${left}px`;
    });
    hit.addEventListener('mouseleave', () => { tip.hidden = true; });
  });
}

function pairsTable(record) {
  if (!record.pairs.length) return '<p class="caption">No cross-readings yet.</p>';
  const rows = record.pairs.map((p) => `
    <tr class="${p.highlight ? 'hi' : ''}">
      <td><span style="color:${familyColor(p.reader)}">${p.reader.toUpperCase()}</span> → <span style="color:${familyColor(p.target)}">${p.target.toUpperCase()}</span></td>
      <td class="num dis">${p.disputed}</td><td class="num">${p.endorsed}</td>
    </tr>`).join('');
  return `<table class="mx"><thead><tr><th>reader → target</th><th class="num">disputed</th><th class="num">endorsed</th></tr></thead><tbody>${rows}</tbody></table>
    <p class="caption">Summed over every cross-reading. Marked pairs endorsed at least eight times as often as they disputed. Dialogue critiques are not counted here.</p>`;
}

function flagsCell(record, runCount) {
  const items = record.flags.map((f) => `
    <p class="flag"><span class="mono converge">${esc(runDate(f.run_id))}</span> · ${f.family[0].toUpperCase()}${f.family.slice(1)} · ${esc(DIMENSION_WORDS[f.dimension] || f.dimension)} · <span class="bright">${esc(READING_WORDS[f.reading] || f.reading)}</span>${f.recomputed ? ' <span class="dim mono small">recomputed</span>' : ''}<br><span class="dim">${esc(f.gloss)}</span></p>`).join('');
  const p = record.persistent;
  const verdict = p.present
    ? `Persistent flagging is present: ${p.family} (${p.rule}). The protocol may be collapsing toward routing.`
    : `No persistent flagging (${p.rule}).`;
  return `<div class="hero mono">${record.flags.length} <span class="dim small">in ${runCount} runs</span></div>${items}<p class="caption">${esc(verdict)}</p>`;
}

function parityCell(run) {
  const rec = run.recomputed ? ' <span class="dimmer">· recomputed</span>' : '';
  if (run.parity === 'undefined') return `<span class="dim">undefined</span>${rec}`;
  if (run.parity === 'flag') return `<span class="converge">flag · ${esc(READING_WORDS[run.flag_reading] || run.flag_reading)}</span>${rec}`;
  return `<span class="present">holds</span>${rec}`;
}

function rowsHtml(runs) {
  return runs.map((r) => `
    <a class="r" href="/session/${encodeURIComponent(r.id)}" data-prompt="${esc((r.prompt || '').toLowerCase())}">
      <span class="d">${esc(runDate(r.id))}</span>
      <span class="t" title="${esc(r.prompt)}">${esc(r.prompt)}</span>
      <span class="n">${r.claims}</span>
      <span class="n critique">${r.disputed}</span>
      <span class="n">${r.both}</span>
      <span class="n ${r.elevated ? 'converge' : ''}">${r.elevated}</span>
      <span class="p">${parityCell(r)}</span>
    </a>`).join('');
}

function unloadableHtml(unloadable) {
  if (!unloadable.length) return '<p class="caption foot">0 files could not be loaded.</p>';
  const list = unloadable.map((u) => `<div class="mono small"><span class="bright">${esc(u.id)}</span> · <span class="dim">${esc(u.error)}</span></div>`).join('');
  return `<details class="foot"><summary class="caption">${unloadable.length} file${unloadable.length === 1 ? '' : 's'} could not be loaded</summary>${list}</details>`;
}

// Search, sort and scroll survive a trip to a session and back (spec §12).
const NAV_KEY = 'gl-record-nav';
function readNav() {
  try { return JSON.parse(sessionStorage.getItem(NAV_KEY) || '{}'); } catch (e) { return {}; }
}
function writeNav(patch) {
  try { sessionStorage.setItem(NAV_KEY, JSON.stringify({ ...readNav(), ...patch })); } catch (e) { /* private mode */ }
}

const SORTS = [['newest', 'Newest'], ['disputed', 'Most disputed'], ['flagged', 'Flagged']];

export function renderRecord(root, bundle) {
  const record = bundle.record;
  const n = record.runs.length;
  const chart = chartSvg(record);
  const nav = readNav();
  const sort = SORTS.some(([k]) => k === nav.sort) ? nav.sort : 'newest';
  const stripOpen = lsGet('gl-strip-open', '0') === '1';
  const sortBtns = SORTS.map(([k, label]) => `<button class="sort-btn ${k === sort ? 'on' : ''}" data-sort="${k}">${label}</button>`).join('');
  root.innerHTML = `
  <div class="record-page">
    <header class="top">
      <span class="k wide">Golden Lattice · the record</span>
      <input class="search" type="search" placeholder="Search prompts…" aria-label="Search prompts">
      <a class="nav-link" href="/explainer">Explainer</a>
      <a class="nav-link converge" href="/session/live">Go live</a>
    </header>
    <section class="overview">
      <div class="summary-line"><span class="summary">${esc(recordSummary(record))}</span>
        <button class="details-btn" data-strip="1" aria-expanded="${stripOpen}">${stripOpen ? 'Hide details' : 'Show details'}</button></div>
      <div class="strip" ${stripOpen ? '' : 'hidden'}>
        <div class="cell cell-chart">
          <h4>Distinct-claim share by seat, per run</h4>
          ${chart.svg}
          <div class="tip" hidden></div>
          <p class="caption">${esc(chart.caption)}</p>
        </div>
        <div class="cell"><h4>Who pushes back on whom</h4>${pairsTable(record)}</div>
        <div class="cell"><h4>Flags</h4>${flagsCell(record, n)}</div>
      </div>
    </section>
    <div class="list">
      <div class="list-tools"><span class="k">Sort</span>${sortBtns}</div>
      <div class="hdr"><span>when</span><span>prompt</span><span>claims</span><span>disputed</span><span title="claims challenged by two or more readers">2+ readers</span><span title="claims elevated by agreement">elevated</span><span>parity</span></div>
      <div id="gl-live-row" hidden></div>
      <div class="rows">${n ? rowsHtml(sortRuns(record.runs, sort)) : '<p class="caption">No completed runs to replay yet.</p>'}</div>
      ${unloadableHtml(bundle.unloadable)}
    </div>
  </div>`;
  wireChartTooltip(root, record);
  watchInFlight(root, bundle);

  const search = root.querySelector('.search');
  const applySearch = () => {
    const q = search.value.trim().toLowerCase();
    root.querySelectorAll('.rows .r').forEach((row) => {
      row.hidden = q && !row.getAttribute('data-prompt').includes(q);
    });
  };
  search.value = nav.q || '';
  applySearch();
  search.addEventListener('input', () => { applySearch(); writeNav({ q: search.value }); });

  root.onclick = (e) => {
    const sb = e.target.closest('[data-sort]');
    if (sb) { writeNav({ sort: sb.getAttribute('data-sort'), y: window.scrollY }); renderRecord(root, bundle); return; }
    const st = e.target.closest('[data-strip]');
    if (st) {
      const open = lsGet('gl-strip-open', '0') !== '1';
      lsSet('gl-strip-open', open ? '1' : '0');
      root.querySelector('.strip').hidden = !open;
      st.setAttribute('aria-expanded', String(open));
      st.textContent = open ? 'Hide details' : 'Show details';
      return;
    }
    if (e.target.closest('.rows .r')) writeNav({ y: window.scrollY });
  };
  if (nav.y) {
    // Re-apply until the page has finished loading (fonts, late layout), then stop.
    const y = nav.y;
    const restore = () => { if (Math.abs(window.scrollY - y) > 2) window.scrollTo(0, y); };
    requestAnimationFrame(restore);
    [120, 400].forEach((ms) => setTimeout(restore, ms));
    if (document.readyState !== 'complete') window.addEventListener('load', restore, { once: true });
  }
}

// The in-flight row: while a run is live on this host, it sits at the top of
// the list in the live color and becomes an ordinary row when it completes.
const PHASE_OF = {
  phase_0_datetime_grounding: '00 · investigation', phase_0_proposal_submitted: '00 · investigation',
  phase_1_response_started: '01 · independent', phase_1_claim: '01 · independent', model_stream_delta: null,
  phase_2_cross_reading: '02 · cross-reading', phase_3_turn: '03 · dialogue', phase_4_artifact: '04 · synthesis',
};
let recordConn = null;
function watchInFlight(root, bundle) {
  if (recordConn) recordConn.close();
  const known = new Set((bundle.sessions || []).map((s) => s.id));
  let current = null;
  const row = () => root.querySelector('#gl-live-row');
  const paint = () => {
    const el = row();
    if (!el) return;
    if (!current) { el.hidden = true; el.innerHTML = ''; return; }
    el.hidden = false;
    el.innerHTML = `<a class="r live-row" href="/session/live"><span class="d critique">now · ${esc(current.phase.slice(0, 2))}</span><span class="t">${esc(current.prompt)}</span><span class="n"></span><span class="n"></span><span class="n"></span><span class="n"></span><span class="p critique">${esc(current.phase)}</span></a>`;
  };
  recordConn = connectLive({
    onStatus: () => {},
    onEvent: async (ev) => {
      if (ev.event_type === 'session_started') { current = { id: ev.session_id, prompt: ev.prompt, phase: '00 · investigation' }; paint(); return; }
      if (!current) return;
      const ph = PHASE_OF[ev.event_type];
      if (ph && ph !== current.phase) { current.phase = ph; paint(); }
      if (ev.event_type === 'session_error') { current = null; paint(); }
      if (ev.event_type === 'session_completed') {
        const id = current.id; current = null; paint();
        if (!known.has(id)) {
          const { fetchBundle } = await import('../app.js');
          setTimeout(async () => { renderRecord(root, await fetchBundle(true)); }, 1200);
        }
      }
    },
  });
}
