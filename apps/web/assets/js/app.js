// Evren arayüzü — derlemesiz ES modülleri, hash tabanlı yönlendirme.
import { clear, errorBox } from './lib/dom.js';
import { renderOverview } from './pages/overview.js';
import { renderArchitecture } from './pages/architecture.js';
import { renderRoadmap } from './pages/roadmap.js';

const ROUTES = {
  'genel-bakis': { title: 'Genel Bakış', render: renderOverview },
  mimari: { title: 'Mimari Harita', render: renderArchitecture },
  'yol-haritasi': { title: 'Yol Haritası', render: renderRoadmap },
};

function parseHash() {
  const raw = decodeURIComponent(location.hash.replace(/^#\/?/, ''));
  const [route = 'genel-bakis', ...rest] = raw.split('/').filter(Boolean);
  return { route: ROUTES[route] ? route : 'genel-bakis', param: rest.join('/') || null };
}

let current = null;

async function navigate() {
  const { route, param } = parseHash();
  const view = document.getElementById('view');
  document.querySelectorAll('#nav a').forEach((a) => {
    if (a.dataset.route === route) a.setAttribute('aria-current', 'page');
    else a.removeAttribute('aria-current');
  });
  document.title = `${ROUTES[route].title} · Evren CMDB`;
  if (current && current.route === route && current.update) {
    current.update(param);
    return;
  }
  clear(view);
  try {
    const instance = (await ROUTES[route].render(view, param)) || {};
    current = { route, update: instance.update };
  } catch (err) {
    current = null;
    clear(view).append(errorBox(`Sayfa yüklenemedi: ${err.message}`));
    console.error(err);
  }
  document.getElementById('main').focus({ preventScroll: true });
}

window.addEventListener('hashchange', navigate);
if (!location.hash) history.replaceState(null, '', '#/genel-bakis');
navigate();
