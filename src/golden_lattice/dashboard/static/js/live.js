// Live mode (spec §6.4): the same Session page, fed by the /ws event stream.
// Each event updates a session object shaped like the normalized one and
// names the sections it changed, so only those re-render. Stream deltas
// append to a text node. On completion the page hydrates from disk.
import { esc } from './util.js';
import { familyOf, labelOf } from './seats.js';
import { renderSession, refreshLive, appendStream } from './session.js';

const HYDRATE_TRIES = 5;
const HYDRATE_GAP_MS = 1000;
const RETRY_MS = 2800;
const TAG_RE = /^\[([A-Z])\]\s*/;

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
    phase0: null, phase1: Object.fromEntries(models.map((m) => [m, { stream: '' }])), reflections: {},
    claims: [], synthesis_lines: [], pairs: [], turns: [], interpretations: [], metrics: null,
    elevations: [], synthesis: { output: '', trace: [] }, stats: {}, metrics_recomputed: false,
  };
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
    critiques: ch('critique'), augments: ch('augment'), converges: ch('converge'),
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
  L.synthesis_lines = [];
  (ev.output || '').split('\n').filter((l) => l.trim()).forEach((raw) => {
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
      const p0 = ensureP0(L);
      p0.proposals.push({ model: ev.model_id, count: (ev.queries || []).length, queries: ev.queries || [] });
      p0.unique = new Set(p0.proposals.flatMap((p) => p.queries)).size;
      return ['00'];
    }
    case 'phase_0_search_result': ensureP0(L).results.push({ query: ev.query, preview: ev.result_text_preview }); return ['00'];
    case 'phase_0_failed_search': ensureP0(L).failed.push({ query: ev.query, reason: ev.reason }); return ['00'];
    case 'phase_0_feed_frozen': ensureP0(L).frozen = ev.entry_count; return ['00'];
    case 'model_stream_delta':
      L.phase1[ev.model_id] = L.phase1[ev.model_id] || { stream: '' };
      L.phase1[ev.model_id].stream += ev.delta;
      state.status = `streaming · ${labelOf(ev.model_id)} · ${ev.phase}`;
      return 'stream';
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
      const seat = L.seats.find((s) => s.id === ev.model_id);
      if (seat) { seat.focus = ev.focus_tag; seat.conf = ev.confidence; }
      return ['01'];
    }
    case 'self_reflection':
      L.reflections[ev.model_id] = { strong: ev.strongest_claim_id, weak: ev.weakest_claim_id, just: ev.tag_justification || '' };
      return ['01'];
    case 'phase_2_cross_reading':
      L.pairs.push({ reader: ev.reader_model, target: ev.target_model, disputed: ev.disagreements_count, endorsed: ev.agreements_count, missing: ev.missing_count });
      restat(L); return ['02'];
    case 'phase_3_turn': {
      L.turns.push({ id: ev.turn_id, speaker: ev.speaker_model, channel: ev.channel, target: ev.target_model, content: ev.content });
      if (ev.channel === 'critique') {
        (ev.target_claim_ids || []).forEach((cid) => {
          const c = L.claims.find((x) => x.id === cid);
          if (!c) return;
          c.critiques.push({ speaker: ev.speaker_model, turn_id: ev.turn_id, content: ev.content });
          c.disputed_by = invitedOrder(L, c.disputed_by.concat([ev.speaker_model]));
        });
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
function shellHtml(status) {
  return `<div class="empty live-shell">
    <div class="k critique"><span class="live-dot"></span> Live</div>
    <div class="empty-msg">Listening for a lattice run on this host.</div>
    <div class="mono small dim" id="gl-livestatus">${esc(status)}</div>
    <div class="live-input"><input id="gl-prompt" type="text" placeholder="question for the lattice — Enter to submit" autocomplete="off"><button class="send" data-live="send">send</button></div>
    <a class="k link" href="/">← record</a></div>`;
}

function liveBarHtml(status) {
  return `<span class="live-dot"></span><span class="k critique">Live</span>
    <span class="mono small dim" id="gl-livestatus">${esc(status)}</span>
    <input id="gl-prompt" type="text" placeholder="question for the lattice — Enter to submit" autocomplete="off">
    <button class="send" data-live="send">send</button>
    <a class="k link" href="/">exit live</a>`;
}

export function startLive(root) {
  const state = { L: null, status: 'connecting…', hydrated: false };
  root.innerHTML = shellHtml(state.status);

  const syncInput = () => {
    const input = root.querySelector('#gl-prompt');
    const btn = root.querySelector('[data-live="send"]');
    if (!input) return;
    const disabled = (state.L && state.L.inFlight) || !conn.isOpen();
    input.disabled = disabled; if (btn) btn.disabled = disabled;
    input.placeholder = state.L && state.L.inFlight ? 'session in flight…' : (conn.isOpen() ? 'question for the lattice — Enter to submit' : 'connecting…');
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

  const conn = connectLive({
    onStatus: (s) => { state.status = s; syncInput(); },
    onEvent: async (ev) => {
      const changed = applyEvent(state, ev);
      if (changed == null) return;
      if (changed === 'all') { state.hydrated = false; renderSession(root, state.L, { mode: 'live' }); renderBar(); return; }
      if (changed === 'stream') { appendStream(ev.model_id, ev.delta); syncInput(); return; }
      if (changed === 'completed') {
        state.status = 'completed · loading the persisted record';
        syncInput();
        const full = await hydrate(state.L.id);
        if (full) {
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
