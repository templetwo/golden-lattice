// Shared helpers for the dashboard modules.

export function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

// "session_20260516_204119_ed014de7" -> "05-16 20:41"
export function runDate(id) {
  const m = /^session_\d{4}(\d{2})(\d{2})_(\d{2})(\d{2})/.exec(id || '');
  return m ? `${m[1]}-${m[2]} ${m[3]}:${m[4]}` : '';
}

export function lsGet(k, d) { try { const v = localStorage.getItem(k); return v == null ? d : v; } catch (e) { return d; } }
export function lsSet(k, v) { try { localStorage.setItem(k, String(v)); } catch (e) { /* private mode */ } }

export const READING_WORDS = {
  not_recognized: 'not recognized',
  peer_divergence: 'peer divergence',
  vocabulary_failed: 'vocabulary failed',
  low_claim_volume: 'low claim volume',
  ambiguous: 'ambiguous',
};

export const DIMENSION_WORDS = {
  distinct_claim_share: 'distinct claims',
  edge_case_coverage_share: 'edge-case coverage',
  structural_pattern_share: 'structural pattern',
};
