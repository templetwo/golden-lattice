// The prose renderer: Markdown the models write, rendered safely.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { renderMarkdown } from '../../src/golden_lattice/dashboard/static/js/markdown.js';

test('raw HTML from a model is shown as text, never as markup', () => {
  const out = renderMarkdown('<script>alert(1)</script> and <img src=x onerror=alert(1)>');
  assert.doesNotMatch(out, /<script|<img/);
  assert.match(out, /&lt;script&gt;/);
});

test('only web links become links', () => {
  assert.match(renderMarkdown('[docs](https://example.com/a?b=1)'),
    /<a href="https:\/\/example\.com\/a\?b=1" target="_blank" rel="noopener noreferrer">docs<\/a>/);
  const js = renderMarkdown('[x](javascript:alert(1))');
  assert.doesNotMatch(js, /<a /);
  const injected = renderMarkdown('[x](https://e.com" onclick="alert(1))');
  assert.doesNotMatch(injected, /<a /);
  assert.doesNotMatch(injected, /"\s*onclick/);   // any onclick stays escaped text, never an attribute
});

test('bold, italics and inline code', () => {
  assert.equal(renderMarkdown('**TTL** first, *then* `lru_cache`'),
    '<p><strong>TTL</strong> first, <em>then</em> <code>lru_cache</code></p>');
  assert.equal(renderMarkdown('__strong__ and _em_'), '<p><strong>strong</strong> and <em>em</em></p>');
});

test('snake_case identifiers and lone asterisks are left alone', () => {
  assert.equal(renderMarkdown('use session_cache_ttl and 2 * 3'), '<p>use session_cache_ttl and 2 * 3</p>');
});

test('markup inside inline code is not interpreted', () => {
  assert.equal(renderMarkdown('`**not bold**`'), '<p><code>**not bold**</code></p>');
});

test('paragraphs split on blank lines; single newlines are line breaks', () => {
  assert.equal(renderMarkdown('one\ntwo\n\nthree'), '<p>one<br>two</p><p>three</p>');
});

test('headings, lists, quotes, rules and fenced code', () => {
  assert.equal(renderMarkdown('## Why'), '<h4 class="md-h">Why</h4>');
  assert.equal(renderMarkdown('- a\n- **b**'), '<ul><li>a</li><li><strong>b</strong></li></ul>');
  assert.equal(renderMarkdown('1. first\n2. second'), '<ol><li>first</li><li>second</li></ol>');
  assert.equal(renderMarkdown('> quoted'), '<blockquote><p>quoted</p></blockquote>');
  assert.equal(renderMarkdown('---'), '<hr>');
  assert.equal(renderMarkdown('```py\nx = 1 < 2\n```'), '<pre><code>x = 1 &lt; 2</code></pre>');
});

test('a list that follows a lead-in line without a blank line still renders', () => {
  assert.equal(renderMarkdown('Options:\n- a\n- b'), '<p>Options:</p><ul><li>a</li><li>b</li></ul>');
});

test('empty and missing input render nothing', () => {
  assert.equal(renderMarkdown(''), '');
  assert.equal(renderMarkdown(null), '');
});
