// Seat identity by family (spec §5.2). Mirrors memory_graph.base.seat_family.

export const FAMILY_ORDER = ['fable', 'opus', 'sonnet', 'haiku'];

export function familyOf(modelId) {
  const text = String(modelId || '');
  for (const f of FAMILY_ORDER) if (text.includes(f)) return f;
  throw new Error(`No seat family in model id ${text}`);
}

export function labelOf(modelId) { return familyOf(modelId).toUpperCase(); }
export function colorOf(modelId) { return `var(--seat-${familyOf(modelId)})`; }
export function familyColor(family) { return `var(--seat-${family})`; }
