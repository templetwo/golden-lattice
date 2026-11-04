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
  let s = esc(text);
  s = s.replace(/`([^`]+)`/g, (_, code) => hold(`<code>${code}</code>`));
  s = s.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (m, label, url) => {
    if (!/^https?:\/\//i.test(url) || /&(quot|lt|gt|#39);/.test(url)) return m;
    return hold(`<a href="${url}" target="_blank" rel="noopener noreferrer">${label}</a>`);
  });
  s = s.replace(/\*\*(?=\S)(.+?)(?<=\S)\*\*/g, '<strong>$1</strong>');
  s = s.replace(/(^|[^\w])__(?=\S)(.+?)(?<=\S)__(?!\w)/g, '$1<strong>$2</strong>');
  s = s.replace(/(^|[^\w*])\*(?=\S)([^*]+?)(?<=\S)\*(?![\w*])/g, '$1<em>$2</em>');
  s = s.replace(/(^|[^\w])_(?=\S)([^_]+?)(?<=\S)_(?!\w)/g, '$1<em>$2</em>');
  return s.replace(new RegExp(`${HOLD}(\\d+)${HOLD}`, 'g'), (_, i) => held[Number(i)]);
}

const BULLET = /^\s*[-*+]\s+(.*)$/;
const NUMBERED = /^\s*\d+[.)]\s+(.*)$/;

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
    const kind = BULLET.test(line) ? 'ul' : NUMBERED.test(line) ? 'ol' : null;
    if (kind) {
      flush();
      const re = kind === 'ul' ? BULLET : NUMBERED;
      const items = [];
      while (i < lines.length && re.test(lines[i])) { items.push(re.exec(lines[i])[1]); i += 1; }
      i -= 1;
      out.push(`<${kind}>${items.map((it) => `<li>${inline(it)}</li>`).join('')}</${kind}>`);
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
