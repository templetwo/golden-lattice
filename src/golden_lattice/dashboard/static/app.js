// Golden Lattice dashboard: router and boot.
//   /                 the Record
//   /session/<id>     the Session (replay)
//   /session/live     the Session (live)
import { renderRecord } from './js/record.js';

let bundlePromise = null;
export function fetchBundle(force = false) {
  if (!bundlePromise || force) {
    bundlePromise = fetch('/sessions').then((r) => {
      if (!r.ok) throw new Error(`/sessions ${r.status}`);
      return r.json();
    });
  }
  return bundlePromise;
}

function errorState(root, message) {
  root.innerHTML = `<div class="empty"><div class="k">Golden Lattice</div><div class="empty-msg">${message}</div></div>`;
}

async function boot() {
  const root = document.getElementById('app');
  const m = /^\/session\/([^/]+)\/?$/.exec(location.pathname);
  try {
    if (!m) {
      renderRecord(root, await fetchBundle());
      return;
    }
    const { renderSessionRoute } = await import('./js/session.js');
    await renderSessionRoute(root, decodeURIComponent(m[1]), fetchBundle);
  } catch (err) {
    console.error(err);
    errorState(root, 'could not reach /sessions');
  }
}

boot();
