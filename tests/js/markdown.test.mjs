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

// Review of PR #2 at a297b4a, finding 3: faithful structure.
test('numbered items separated by blank lines stay one list', () => {
  assert.equal(renderMarkdown('1. a\n\n2. b\n\n3. c'), '<ol><li>a</li><li>b</li><li>c</li></ol>');
});

test('a list that starts at 3 keeps its numbers', () => {
  assert.equal(renderMarkdown('3. c\n4. d'), '<ol start="3"><li>c</li><li>d</li></ol>');
});

test('nested bullets nest under their parent', () => {
  assert.equal(renderMarkdown('- parent\n  - child\n  - child two\n- next'),
    '<ul><li>parent<ul><li>child</li><li>child two</li></ul></li><li>next</li></ul>');
  assert.equal(renderMarkdown('1. step\n   - detail\n2. step two'),
    '<ol><li>step<ul><li>detail</li></ul></li><li>step two</li></ol>');
});

test('inline code inside a link label renders, with no placeholder left', () => {
  const out = renderMarkdown('[see `x_y`](https://e.com)');
  assert.equal(out, '<p><a href="https://e.com" target="_blank" rel="noopener noreferrer">see <code>x_y</code></a></p>');
  assert.doesNotMatch(out, /\u0001/);
});

test('a paragraph after a list is not swallowed into it', () => {
  assert.equal(renderMarkdown('- a\n- b\n\nAfter.'), '<ul><li>a</li><li>b</li></ul><p>After.</p>');
});

// Security review of 0c28c0d: placeholder markers smuggled in model text.
test('placeholder markers in model text are never expanded', () => {
  const H = '\u0001';
  const attacks = [
    `\`"x"\` [a](https://e.com/${H}0${H})`,
    `[first](https://a.com) [second](https://b.com/${H}0${H}onmouseover=alert(1))`,
    `${H}0${H} plain \`code\``,
    `[lbl ${H}0${H}](https://c.com) \`z\``,
  ];
  for (const src of attacks) {
    const out = renderMarkdown(src);
    assert.doesNotMatch(out, /\u0001/, src);
    for (const m of out.matchAll(/href="([^"]*)"/g)) {
      assert.doesNotMatch(m[1], /[<>"]/, `href stays a plain URL: ${src}`);
    }
    assert.equal((out.match(/<a /g) || []).length, (out.match(/<\/a>/g) || []).length, src);
    assert.doesNotMatch(out, /<a [^>]*<a /, `no anchor inside an anchor: ${src}`);
  }
});

// Review of 34be2a7, finding 4: fenced code inside a list item.
test('fenced code inside a list item keeps its indentation', () => {
  const src = '1. Example:\n   ```py\n   def f():\n       call()\n\n       return 1\n   ```\n2. next';
  assert.equal(renderMarkdown(src),
    '<ol><li>Example:<pre><code>def f():\n    call()\n\n    return 1</code></pre></li><li>next</li></ol>');
});
