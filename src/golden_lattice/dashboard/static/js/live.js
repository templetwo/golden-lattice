// Live mode (spec §6.4): the same Session page, fed by the /ws event stream.
// Each event updates a session object shaped like the normalized one and
// names the sections it changed, so only those re-render. Stream deltas
// append to a text node. On completion the page hydrates from disk.
import { esc, splitAccountability } from './util.js';
import { familyOf, labelOf } from './seats.js';
import { renderSession, refreshLive, updateWriting, clearWriting, removeWriting } from './session.js';
import { readableStream, streamText } from './stream.js';

const HYDRATE_TRIES = 5;
const HYDRATE_GAP_MS = 1000;
const RETRY_MS = 2800;
const TAG_RE = /^\[([A-Z])\]\s*/;
// The dashboard runs no questions: the prompt box stays disabled and points
// at the CLI. The live view itself (the /ws stream) keeps working.
export const PROMPT_OFF = 'questions are not run from the dashboard; use "golden-lattice run" in a terminal';

// ---------- the WebSocket ----------
export function connectLive({ onEvent, onStatus }) {
  let ws = null;
  let closed = false;
  let retry = null;
  const open = () => {
    if (closed) return;
    try {
      ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`);
    } catch (e) { onStatus && onStatus('live transport unavailable'); return; }
    ws.onopen = () => onStatus && onStatus('connected · waiting for a run');
    ws.onmessage = (e) => { try { onEvent(JSON.parse(e.data)); } catch (err) { console.error(err); } };
    ws.onclose = () => {
      ws = null;
      if (closed) return;
      onStatus && onStatus('no lattice detected on this host · retrying');
      retry = setTimeout(open, RETRY_MS);
    };
  };
  open();
  return {
    send: (obj) => { if (ws && ws.readyState === WebSocket.OPEN) { ws.send(JSON.stringify(obj)); return true; } return false; },
    isOpen: () => !!ws && ws.readyState === WebSocket.OPEN,
    close: () => { closed = true; if (retry) clearTimeout(retry); if (ws) { ws.onclose = null; ws.close(); } },
  };
}

// ---------- event → live session ----------
function freshSession(ev) {
  const models = ev.models_invited || [];
  return {
    live: true, artArrived: false, metricsArrived: false, inFlight: true, error: null,
    id: ev.session_id, title: 'LIVE', prompt: ev.prompt, models,
    seats: models.map((m) => ({ id: m, family: familyOf(m), label: labelOf(m), legacy: false, focus: '', conf: 0, claim_count: 0 })),
    phase0: null, phase1: Object.fromEntries(models.map((m) => [m, { response: '' }])), reflections: {},
    streams: {},          // one buffer per streaming request (stream_id)
    seatStage: {},        // the protocol stage each seat is streaming in
    claims: [], synthesis_lines: [], pairs: [], turns: [], interpretations: [], metrics: null,
    elevations: [], synthesis: { output: '', trace: [] }, stats: {}, metrics_recomputed: false,
    unattached_critiques: [], accountability: '',
  };
}

// The readable text a seat has written in its current phase.
export function writingText(w) {
  if (!w) return '';
  const fromTool = streamText(readableStream(w.raw || ''));
  return [w.prose || '', fromTool].filter(Boolean).join('\n\n');
}

// When a seat's Phase 1 stream ends, keep its answer for section 01.
export function initialAnswerText(w) {
  const segs = readableStream((w && w.raw) || '');
  return segs.filter((s) => s.key === 'response').map((s) => s.text).join('\n\n') || ((w && w.prose) || '');
}

// Phases that run concurrently share a stage: a seat's cross-readings and
// its tagging are all Disagreements work (review of 34be2a7, finding 1).
const STAGE = {
  phase_0_proposal: '00', phase_1: '01', self_reflection: '01',
  phase_2_cross_reading: '02', phase_2_tagging: '02', phase_3: '03',
};
const stageOf = (phase) => STAGE[phase] || phase;

function retire(state, keys) {
  const L = state.L;
  keys.forEach((k) => { if (L.streams[k]) L.streams[k].done = true; });
  state.retired = (state.retired || []).concat(keys.filter((k) => L.streams[k]));
}

export function streamKey(ev) {
  return ev.stream_id || `${ev.phase}:${ev.model_id}`;
}

function keepInitialAnswer(L, model) {
  const w = L.streams[`phase_1:${model}`];
  if (!w || w.phase !== 'phase_1') return;
  const answer = initialAnswerText(w);
  if (answer) L.phase1[model] = { ...(L.phase1[model] || {}), response: answer };
}

function ensureP0(L) {
  L.phase0 = L.phase0 || { grounding: '', proposals: [], unique: 0, frozen: 0, results: [], failed: [] };
  return L.phase0;
}

function invitedOrder(L, ids) {
  const set = new Set(ids);
  return L.models.filter((m) => set.has(m));
}

function restat(L) {
  const ch = (c) => L.turns.filter((t) => t.channel === c).length;
  L.stats = {
    claims: L.claims.length,
    disputed: L.claims.filter((c) => c.disputed_by.length).length,
    both: L.claims.filter((c) => c.disputed_by.length >= 2).length,
    elevated: L.claims.filter((c) => c.elevated).length,
    omitted: L.claims.filter((c) => c.disposition === 'omitted').length,
    modified: L.claims.filter((c) => c.disposition === 'modified').length,
    critiques: ch('critique'), critiques_attached: ch('critique') - (L.unattached_critiques || []).length,
    augments: ch('augment'), converges: ch('converge'),
    missing: L.pairs.reduce((a, p) => a + (p.missing || 0), 0),
  };
}

function applyArtifact(L, ev) {
  L.artArrived = true;
  const trace = ev.claim_trace || [];
  L.synthesis = { output: ev.output, trace: trace.map((e) => ({ id: e.claim_id, disp: e.disposition })) };
  L.elevations = ev.elevations || [];
  const elevated = new Set(L.elevations.flatMap((e) => e.claim_ids || []));
  const byId = Object.fromEntries(L.claims.map((c) => [c.id, c]));
  trace.forEach((e) => {
    const c = byId[e.claim_id];
    if (!c) return;
    c.disposition = e.disposition; c.modified_text = e.modified_text || null; c.omission_reason = e.omission_reason || null;
  });
  L.claims.forEach((c) => { c.elevated = elevated.has(c.id); });
  (ev.surfaced_disagreements || []).forEach((sd) => (sd.claim_ids || []).forEach((id) => {
    if (byId[id] && !byId[id].surfaced_note) byId[id].surfaced_note = sd.note;
  }));
  // Lines: same parse as normalize.py; exact text first, then modified text.
  // The M3 accountability footer is not answer lines; it is kept verbatim.
  const [body, footer] = splitAccountability(ev.output);
  L.accountability = footer;
  L.synthesis_lines = [];
  body.split('\n').filter((l) => l.trim()).forEach((raw) => {
    const m = TAG_RE.exec(raw);
    const body = m ? raw.slice(m[0].length) : raw;
    const i = body.indexOf('[DISPUTED');
    L.synthesis_lines.push({ index: L.synthesis_lines.length, tag: m ? m[1] : '', text: (i < 0 ? body : body.slice(0, i)).trim(), hedge: i < 0 ? null : body.slice(i).trim(), claim_id: null });
  });
  const used = new Set();
  L.synthesis_lines.forEach((line) => {
    const fam = { F: 'fable', O: 'opus', S: 'sonnet', H: 'haiku' }[line.tag];
    const free = (x) => !used.has(x.id) && x.disposition !== 'omitted';
    const byText = L.claims.filter((x) => free(x) && x.text === line.text);
    const byMod = L.claims.filter((x) => free(x) && x.modified_text && x.modified_text.split('[DISPUTED')[0].trim() === line.text);
    const pick = (list) => list.find((x) => x.author_family === fam) || list[0];
    const c = pick(byText) || pick(byMod);
    if (!c) return;
    used.add(c.id); c.synthesis_line = line.index; line.claim_id = c.id;
    c.disputed_hedge = line.hedge || (c.modified_text && c.modified_text.includes('[DISPUTED') ? c.modified_text.slice(c.modified_text.indexOf('[DISPUTED')).trim() : null);
  });
}

// Returns the sections that changed, 'all' for a full render, or 'stream'.
const STATUS_OF = {
  phase_0_datetime_grounding: 'running · 00 investigation', phase_0_proposal_submitted: 'running · 00 investigation',
  phase_1_response_started: 'running · 01 independent generation', phase_1_claim: 'running · 01 independent generation',
  self_reflection: 'running · 01 self-reflection', phase_2_cross_reading: 'running · 02 cross-reading',
  phase_3_turn: 'running · 03 dialogue', phase_4_artifact: 'running · 04 synthesis',
};

export function applyEvent(state, ev) {
  const t = ev.event_type;
  if (STATUS_OF[t]) state.status = STATUS_OF[t];
  if (t === 'session_started') { state.L = freshSession(ev); state.status = 'running · session started'; return 'all'; }
  const L = state.L;
  if (!L) return null;
  switch (t) {
    case 'phase_0_datetime_grounding': ensureP0(L).grounding = ev.formatted_text; return ['00'];
    case 'phase_0_proposal_submitted': {
      retire(state, [`phase_0_proposal:${ev.model_id}`]);
      const p0 = ensureP0(L);
      p0.proposals.push({ model: ev.model_id, count: (ev.queries || []).length, queries: ev.queries || [] });
      p0.unique = new Set(p0.proposals.flatMap((p) => p.queries)).size;
      return ['00'];
    }
    case 'phase_0_search_result': ensureP0(L).results.push({ query: ev.query, preview: ev.result_text_preview }); return ['00'];
    case 'phase_0_failed_search': ensureP0(L).failed.push({ query: ev.query, reason: ev.reason }); return ['00'];
    case 'phase_0_feed_frozen': ensureP0(L).frozen = ev.entry_count; return ['00'];
    case 'model_stream_delta': {
      // Requests stream concurrently (Phase 2 runs a cross-reading per peer
      // plus tagging), so each request keeps its own buffer.
      const key = streamKey(ev);
      let w = L.streams[key];
      if (!w) {
        w = L.streams[key] = { key, model: ev.model_id, phase: ev.phase, target: ev.target_model || null, raw: '', prose: '' };
      }
      const stage = stageOf(ev.phase);
      if (L.seatStage[ev.model_id] !== stage) {
        keepInitialAnswer(L, ev.model_id);
        retire(state, Object.values(L.streams)
          .filter((x) => x.model === ev.model_id && !x.done && stageOf(x.phase) !== stage).map((x) => x.key));
        L.seatStage[ev.model_id] = stage;
      }
      if (ev.delta_kind === 'text') w.prose += ev.delta; else w.raw += ev.delta;
      state.status = `streaming · ${labelOf(ev.model_id)}`;
      state.streamKey = key;
      return 'stream';
    }
    case 'phase_1_claim': {
      L.claims.push({
        id: ev.claim_id, author: ev.model_id, author_family: familyOf(ev.model_id), text: ev.text, index: L.claims.length,
        agreed_by: [], disagreements: [], critiques: [], disputed_by: [], disposition: null, modified_text: null,
        omission_reason: null, synthesis_line: null, disputed_hedge: null, surfaced_note: null, elevated: false,
      });
      const seat = L.seats.find((s) => s.id === ev.model_id);
      if (seat) seat.claim_count += 1;
      restat(L); return ['01', '02'];
    }
    case 'phase_1_response_completed': {
      keepInitialAnswer(L, ev.model_id);
      retire(state, [`phase_1:${ev.model_id}`]);
      const seat = L.seats.find((s) => s.id === ev.model_id);
      if (seat) { seat.focus = ev.focus_tag; seat.conf = ev.confidence; }
      return ['01'];
    }
    case 'self_reflection':
      retire(state, [`self_reflection:${ev.model_id}`]);
      L.reflections[ev.model_id] = { strong: ev.strongest_claim_id, weak: ev.weakest_claim_id, just: ev.tag_justification || '' };
      return ['01'];
    case 'phase_2_cross_reading':
      retire(state, [`phase_2_cross_reading:${ev.reader_model}>${ev.target_model}`]);
      L.pairs.push({ reader: ev.reader_model, target: ev.target_model, disputed: ev.disagreements_count, endorsed: ev.agreements_count, missing: ev.missing_count });
      restat(L); return ['02'];
    case 'phase_2_tagging': retire(state, [`phase_2_tagging:${ev.tagger_model}`]); return [];
    case 'phase_3_turn': {
      retire(state, [`phase_3:${ev.speaker_model}`]);
      L.turns.push({ id: ev.turn_id, speaker: ev.speaker_model, channel: ev.channel, target: ev.target_model, content: ev.content });
      if (ev.channel === 'critique') {
        let attached = 0;
        (ev.target_claim_ids || []).forEach((cid) => {
          const c = L.claims.find((x) => x.id === cid);
          if (!c) return;
          attached += 1;
          c.critiques.push({ speaker: ev.speaker_model, turn_id: ev.turn_id, content: ev.content });
          c.disputed_by = invitedOrder(L, c.disputed_by.concat([ev.speaker_model]));
        });
        // Aimed only at claims with no row (e.g. Phase 2 missing claims): 03 shows it.
        if (!attached) {
          L.unattached_critiques.push({ speaker: ev.speaker_model, turn_id: ev.turn_id, content: ev.content,
            target: ev.target_model, target_claim_ids: ev.target_claim_ids || [] });
        }
      }
      restat(L); return ['02', '03'];
    }
    case 'phase_4_artifact': applyArtifact(L, ev); restat(L); return ['02', '04'];
    case 'phase_4_metrics': L.metricsArrived = true; L.metrics = ev.metrics; return ['04'];
    case 'phase_4_flag_interpretations':
      // Glosses arrive with hydration; the reading and share are shown now.
      L.interpretations = (ev.interpretations || []).map((f) => ({
        model: f.source_model, family: familyOf(f.source_model), dimension: f.dimension_label, reading: f.reading, gloss: '', share: f.share,
      }));
      return ['04'];
    case 'session_error':
      L.error = { message: ev.message, phase: ev.phase, model_id: ev.model_id }; L.inFlight = false;
      state.status = 'error · provider run stopped; dashboard remains available';
      return [];
    case 'session_completed': L.inFlight = false; return 'completed';
    default: return null;
  }
}

// ---------- hydration ----------
// A completion fetch is applied only if the run it was fetched for is still
// the one on screen, and the response is that run (review of PR #2, finding 1).
export function acceptHydration(state, captured, full) {
  return !!full && state.L === captured && full.id === captured.id;
}

async function hydrate(id) {
  for (let i = 0; i < HYDRATE_TRIES; i++) {
    try {
      const r = await fetch(`/sessions/${encodeURIComponent(id)}`, { cache: 'no-store' });
      if (r.ok) return await r.json();
    } catch (e) { /* retry */ }
    await new Promise((res) => setTimeout(res, HYDRATE_GAP_MS));
  }
  return null;
}

// ---------- the live page ----------
export function shellHtml(status) {
  return `<div class="empty live-shell">
    <div class="k critique"><span class="live-dot"></span> Live</div>
    <div class="empty-msg">Listening for a lattice run on this host.</div>
    <div class="mono small dim" id="gl-livestatus">${esc(status)}</div>
    <div class="live-input"><input id="gl-prompt" type="text" placeholder="${esc(PROMPT_OFF)}" title="${esc(PROMPT_OFF)}" autocomplete="off" disabled><button class="send" data-live="send" title="${esc(PROMPT_OFF)}" disabled>send</button></div>
    <a class="k link" href="/">← record</a></div>`;
}

export function liveBarHtml(status) {
  return `<span class="live-dot"></span><span class="k critique">Live</span>
    <span class="mono small dim" id="gl-livestatus">${esc(status)}</span>
    <input id="gl-prompt" type="text" placeholder="${esc(PROMPT_OFF)}" title="${esc(PROMPT_OFF)}" autocomplete="off" disabled>
    <button class="send" data-live="send" title="${esc(PROMPT_OFF)}" disabled>send</button>
    <a class="k link" href="/">exit live</a>`;
}

export function startLive(root) {
  const state = { L: null, status: 'connecting…', hydrated: false };
  root.innerHTML = shellHtml(state.status);

  const syncInput = () => {
    const input = root.querySelector('#gl-prompt');
    const btn = root.querySelector('[data-live="send"]');
    if (!input) return;
    input.disabled = true; if (btn) btn.disabled = true;
    input.placeholder = PROMPT_OFF; input.title = PROMPT_OFF;
    const s = root.querySelector('#gl-livestatus');
    if (s) s.textContent = state.status;
  };
  const renderBar = () => {
    const bar = root.querySelector('#gl-transport');
    if (bar) { bar.innerHTML = liveBarHtml(state.status); bar.classList.add('live'); }
    if (state.L && state.L.error) {
      const panes = root.querySelector('.mast-seats');
      if (panes && !root.querySelector('.live-error')) {
        panes.insertAdjacentHTML('afterend', `<div class="live-error mono small">session error${state.L.error.phase ? ` · ${esc(state.L.error.phase)}` : ''}${state.L.error.model_id ? ` · ${esc(labelOf(state.L.error.model_id))}` : ''}<br>${esc(state.L.error.message)}</div>`);
      }
    }
    syncInput();
  };

  // At most one repaint per seat per frame, however fast deltas arrive.
  const pendingWriting = new Set();
  const flushRetired = () => { if (state.retired && state.retired.length) { removeWriting(state.retired); state.retired = []; } };
  const scheduleWriting = (key) => {
    flushRetired();
    if (pendingWriting.has(key)) return;
    pendingWriting.add(key);
    requestAnimationFrame(() => {
      pendingWriting.delete(key);
      const w = state.L && state.L.streams[key];
      if (w && !w.done) updateWriting(w, writingText(w));
      if (w && w.phase === 'phase_1') {
        w.answer = initialAnswerText(w);
        refreshLive(state.L, ['01']);
      }
    });
  };

  const conn = connectLive({
    onStatus: (s) => { state.status = s; syncInput(); },
    onEvent: async (ev) => {
      const changed = applyEvent(state, ev);
      flushRetired();
      if (changed == null) return;
      if (changed === 'all') { state.hydrated = false; renderSession(root, state.L, { mode: 'live' }); renderBar(); return; }
      if (changed === 'stream') { scheduleWriting(state.streamKey); syncInput(); return; }
      if (changed === 'completed') {
        clearWriting();
        state.status = 'completed · loading the persisted record';
        syncInput();
        const captured = state.L;
        const full = await hydrate(captured.id);
        if (state.L !== captured) return;   // a newer run started while we waited
        if (acceptHydration(state, captured, full)) {
          state.hydrated = true;
          state.status = `completed · ${state.L.id}`;
          renderSession(root, full, { mode: 'live' });
        } else {
          state.L.hydrationNote = 'the persisted file for this run was not found; reasons are unavailable';
          state.status = 'completed · not persisted';
          refreshLive(state.L, ['02']);
        }
        renderBar();
        return;
      }
      if (changed.length) refreshLive(state.L, changed);
      if (t_needsBar(ev)) renderBar(); else syncInput();
    },
  });

  root.addEventListener('click', (e) => {
    if (e.target.closest('[data-live="send"]')) send();
  });
  root.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && e.target && e.target.id === 'gl-prompt') { e.preventDefault(); send(); }
  });
  function send() {
    const input = root.querySelector('#gl-prompt');
    if (!input || input.disabled || !input.value.trim()) return;
    if (conn.send({ type: 'prompt', text: input.value })) {
      input.value = '';
      state.status = 'prompt sent · waiting for the run to start';
      syncInput();
    }
  }
}

function t_needsBar(ev) { return ev.event_type === 'session_error'; }
