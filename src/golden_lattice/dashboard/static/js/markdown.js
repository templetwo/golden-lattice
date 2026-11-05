// Prose renderer for model-written Markdown. Escape first, then format:
// every character of the input is HTML-escaped before any Markdown rule
// runs, so nothing a model writes can become markup. Only http(s) links
// become links. Covers what the answers actually use: paragraphs, line
// breaks, headings, lists, quotes, rules, fenced and inline code, bold,
// italics. Pure; tested with `node --test tests/js`.
import { esc } from './util.js';

const HOLD = '\u0001';

function inline(text) {
  const held = [];
  const hold = (html) => { held.push(html); return `${HOLD}${held.length - 1}${HOLD}`; };
  const restore = (str) => str.replace(new RegExp(`${HOLD}(\\d+)${HOLD}`, 'g'), (_, i) => held[Number(i)] ?? '');
  // The marker never comes from the input: model text cannot smuggle a
  // placeholder in (security review of 0c28c0d).
  let s = esc(String(text).split(HOLD).join(''));
  s = s.replace(/`([^`]+)`/g, (_, code) => hold(`<code>${code}</code>`));
  s = s.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (m, label, url) => {
    // An address must be a plain web URL: no quotes or brackets, and no
    // placeholder (a code span inside the parentheses).
    if (!/^https?:\/\//i.test(url) || /&(quot|lt|gt|#39);/.test(url) || url.includes(HOLD)) return restore(m);
    // Resolve the label's own pieces now, so the held link is self-contained
    // and restoration below is a single pass that never re-scans its output.
    return hold(`<a href="${url}" target="_blank" rel="noopener noreferrer">${restore(label)}</a>`);
  });
  s = s.replace(/\*\*(?=\S)(.+?)(?<=\S)\*\*/g, '<strong>$1</strong>');
  s = s.replace(/(^|[^\w])__(?=\S)(.+?)(?<=\S)__(?!\w)/g, '$1<strong>$2</strong>');
  s = s.replace(/(^|[^\w*])\*(?=\S)([^*]+?)(?<=\S)\*(?![\w*])/g, '$1<em>$2</em>');
  s = s.replace(/(^|[^\w])_(?=\S)([^_]+?)(?<=\S)_(?!\w)/g, '$1<em>$2</em>');
  return restore(s);
}

// ---------- lists: indentation nests, blank lines between items keep one list ----------
const ITEM = /^(\s*)([-*+]|\d+[.)])\s+(.*)$/;
const indentOf = (ws) => ws.replace(/\t/g, '    ').length;

function collectList(lines, i) {
  const items = [];
  let j = i;
  while (j < lines.length) {
    const line = lines[j];
    const m = ITEM.exec(line);
    if (m && !/^\s*(-{3,}|\*{3,}|_{3,})\s*$/.test(line)) {
      items.push({ indent: indentOf(m[1]), ordered: /\d/.test(m[2]), num: parseInt(m[2], 10), lines: [m[3]] });
      j += 1;
      continue;
    }
    // A fence inside an item: keep its lines verbatim (blank lines and
    // indentation included), dedented by the fence's own indent.
    const fence = /^(\s{2,})```/.exec(line);
    if (items.length && fence) {
      const cut = fence[1].length;
      const code = [];
      j += 1;
      while (j < lines.length && !/^\s*```/.test(lines[j])) {
        const l = lines[j];
        code.push(l.slice(0, cut).trim() === '' ? l.slice(cut) : l.trimStart());
        j += 1;
      }
      if (j < lines.length) j += 1;   // the closing fence
      items[items.length - 1].lines.push({ code: code.join('\n') });
      continue;
    }
    if (!line.trim()) {
      let k = j + 1;
      while (k < lines.length && !lines[k].trim()) k += 1;
      if (k < lines.length && items.length && (ITEM.test(lines[k]) || /^\s{2,}\S/.test(lines[k]))) { j = k; continue; }
      break;
    }
    if (items.length && /^\s{2,}\S/.test(line)) { items[items.length - 1].lines.push(line.trim()); j += 1; continue; }
    break;
  }
  return { items, next: j };
}

function renderItems(items, start, indent) {
  const ordered = items[start].ordered;
  const first = items[start].num;
  const lis = [];
  let k = start;
  while (k < items.length && items[k].indent >= indent) {
    const it = items[k];
    if (it.indent > indent) {
      const sub = renderItems(items, k, it.indent);
      if (lis.length) lis[lis.length - 1] += sub.html; else lis.push(sub.html);
      k = sub.next;
      continue;
    }
    if (it.ordered !== ordered) break;
    let li = '';
    it.lines.forEach((ln) => {
      if (typeof ln === 'object') { li += `<pre><code>${esc(ln.code)}</code></pre>`; return; }
      li += (li && !li.endsWith('</pre>') ? '<br>' : '') + inline(ln);
    });
    lis.push(li);
    k += 1;
  }
  const tag = ordered ? 'ol' : 'ul';
  const startAttr = ordered && Number.isFinite(first) && first !== 1 ? ` start="${first}"` : '';
  return { html: `<${tag}${startAttr}>${lis.map((li) => `<li>${li}</li>`).join('')}</${tag}>`, next: k };
}


export function renderMarkdown(src) {
  if (!src) return '';
  const lines = String(src).replace(/\r\n?/g, '\n').split('\n');
  const out = [];
  let para = [];
  const flush = () => {
    if (para.length) out.push(`<p>${para.map(inline).join('<br>')}</p>`);
    para = [];
  };
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (/^\s*```/.test(line)) {
      flush();
      const code = [];
      i += 1;
      while (i < lines.length && !/^\s*```/.test(lines[i])) { code.push(lines[i]); i += 1; }
      out.push(`<pre><code>${esc(code.join('\n'))}</code></pre>`);
      continue;
    }
    if (!line.trim()) { flush(); continue; }
    const h = /^\s*#{1,6}\s+(.*)$/.exec(line);
    if (h) { flush(); out.push(`<h4 class="md-h">${inline(h[1])}</h4>`); continue; }
    if (/^\s*(-{3,}|\*{3,}|_{3,})\s*$/.test(line)) { flush(); out.push('<hr>'); continue; }
    if (ITEM.test(line)) {
      flush();
      const { items, next } = collectList(lines, i);
      let k = 0;
      while (k < items.length) { const r = renderItems(items, k, items[k].indent); out.push(r.html); k = r.next; }
      i = next - 1;
      continue;
    }
    if (/^\s*>/.test(line)) {
      flush();
      const quoted = [];
      while (i < lines.length && /^\s*>/.test(lines[i])) { quoted.push(lines[i].replace(/^\s*>\s?/, '')); i += 1; }
      i -= 1;
      out.push(`<blockquote>${renderMarkdown(quoted.join('\n'))}</blockquote>`);
      continue;
    }
    para.push(line);
  }
  flush();
  return out.join('');
}
