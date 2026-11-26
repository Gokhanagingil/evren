// Evren arayüzü — derlemesiz ES modülleri, hash tabanlı yönlendirme. Kabuk GRC Platformu (tedarik) düzenindedir.
import { h, append, clear, errorBox, fold } from './lib/dom.js';
import { icon } from './lib/icons.js';
import { backend, globalSearch, overview, queueCounts, queueView, sourcesView } from './lib/data.js';
import { closeDetail, closeOverlay, dialog, isDetailOpen, isOverlayOpen, palette, setHeader } from './lib/shell.js';
import { renderOverview } from './pages/overview.js';
import { renderQueue } from './pages/queue.js';
import { renderCis } from './pages/cis.js';
import { renderControls } from './pages/controls.js';
import { renderAttestation } from './pages/attestation.js';
import { renderStaleness } from './pages/staleness.js';
import { renderShadow } from './pages/shadow.js';
import { renderSchema } from './pages/schema.js';
import { renderModel } from './pages/model.js';
import { renderSources } from './pages/sources.js';
import { renderRules } from './pages/rules.js';
import { renderBigPicture } from './pages/bigpicture.js';
import { renderArchitecture } from './pages/architecture.js';
import { renderRoadmap } from './pages/roadmap.js';

const ROUTES = {
  'genel-bakis': { title: 'Genel Bakış', icon: 'home', render: renderOverview,
    help: 'Karar bekleyen işler, kaynak kapsamı ve kaynak sağlığı tek ekranda. Kartlar ilgili listeyi filtreli açar.' },
  kuyruk: { title: 'Eşleştirme Kuyruğu', icon: 'inbox', render: renderQueue,
    help: 'Otomatik birleştirilmeyen kaynak kayıtları. Bir kayda tıklayın: kaynak kaydı ve adaylar yan yana, her kanıt ağırlığıyla açıklanır. Kararlar geri alınabilir.' },
  ci: { title: 'Konfigürasyon Öğeleri', icon: 'server', render: renderCis,
    help: 'Golden record. Bir CI açıldığında her özniteliğin hangi kaynaktan, hangi kuralla geldiği ve hangi değerlerin ezildiği görünür.' },
  'veri-modeli': { title: 'Veri Modeli', icon: 'tree', render: renderModel,
    help: 'ServiceNow uyumlu sınıf ağacı. Bir sınıfa tıklayın veya Tab ile gelip Enter\'a basın: alanları (kalıtılanlar dahil), kimlik kuralı, izinli ilişkiler ve SQL görünümü açılır. Değişiklik yalnızca değişiklik setiyle yapılır.' },
  'sahiplik-onayi': { title: 'Sahiplik Onayı', icon: 'check', render: renderAttestation,
    help: 'CI sahibi kaydı dönemsel olarak onaylar veya gerekçeyle reddeder. "Bende" görünümünde toplu onay; sahipsiz ve reddedilen CI\'lar Veri sorumlusu içindir. Onay uzlaştırıcıdan geçer.' },
  eskime: { title: 'Eskime', icon: 'clock', render: renderStaleness,
    help: 'Gözlemci kaynakların (keşif, sanallaştırma, ağ, izleme, uç nokta) hiçbirinin uzun süredir görmediği CI\'lar. Kaynakların son çalışması sağlıklı değilse öneri ilerlemez. Emekliye ayırma veya koruma Veri sorumlusunun gerekçeli kararıdır; CI silinmez.' },
  kontroller: { title: 'GRC Kontrolleri', icon: 'shield', render: renderControls,
    help: 'GRC Platformu\'na giden CMDB tabanlı kontrol sonuçları. Kaynak verisi kısmi toplandıysa sonuç "kesin değil" olur.' },
  'golge-mod': { title: 'Gölge Mod Raporu', icon: 'layers', render: renderShadow,
    help: 'Mevcut CMDB (ServiceNow) ile Evren gözlemlerinin karşılaştırması: keşfin görmediği kayıtlar, CMDB\'de olmayan keşif kayıtları, mükerrer adayları ve değer farkları. Rapor GRC Platformu için değiştirilemez kanıt olarak kaydedilebilir.' },
  kaynaklar: { title: 'Veri Kaynakları', icon: 'plug', render: renderSources,
    help: 'Her veri kaynağı bir rolü doldurur. Kurallar role bağlı olduğu için ürün değiştiğinde kurallar değişmez. Devre kesici kısmi çalışmada yıkıcı işlemleri durdurur.' },
  sema: { title: 'Şema ve Değişiklikler', icon: 'key', render: renderSchema,
    help: 'Sınıf, alan, seçim listesi ve veri kaynağı yaşam döngüsü değişiklikleri taslak → onay → yayın akışıyla yapılır. Hazırlayan onaylayamaz. Yayınlanan set metadata/changes/ altına değişiklik dosyası olarak PR ile eklenir.' },
  kurallar: { title: 'Kurallar', icon: 'sliders', render: renderRules,
    help: 'Uzlaştırma (rol önceliği), tanımlama (IRE), veri tazeleme ve eskime kuralları. Kaynak: metadata/*.yaml.' },
  'buyuk-resim': { title: 'Büyük Resim', icon: 'orbit', render: renderBigPicture,
    help: 'Tek sayfalık mimari özet: kaynak rolleri, alım, ara katman, kayıt sistemi, tüketim, ServiceNow geçişi, güvenlik ve fazlar.' },
  mimari: { title: 'Mimari Harita', icon: 'map', render: renderArchitecture,
    help: 'Bileşen düzeyinde harita. Bir kutuya tıklayın veya Tab ile gelip Enter\'a basın; ayrıntı ve yol haritası bağlantısı açılır. Esc kapatır.' },
  'yol-haritasi': { title: 'Yol Haritası', icon: 'flag', render: renderRoadmap,
    help: 'Fazlar, adımlar, çıkış kriterleri ve sizden gereken karar/erişimler. Aynı plan docs/PROJE-PLANI.md dosyasındadır.' },
};

const NAV = [
  { group: 'Genel', items: ['genel-bakis', 'kuyruk'] },
  { group: 'CMDB', items: ['ci', 'sahiplik-onayi', 'eskime', 'veri-modeli', 'kontroller', 'golge-mod'] },
  { group: 'Kaynaklar ve kurallar', items: ['kaynaklar', 'kurallar', 'sema'] },
  { group: 'Platform', items: ['buyuk-resim', 'mimari', 'yol-haritasi'] },
];
const SOON = [['CMDB sağlığı', 'F1'], ['Rol–yetki matrisi', 'F1'], ['ServiceNow eşitleme', 'F2']];

function readClosed() {
  try { return new Set(JSON.parse(localStorage.getItem('evren.nav.closed') || '[]')); } catch { return new Set(); }
}
function writeClosed(set) {
  try { localStorage.setItem('evren.nav.closed', JSON.stringify([...set])); } catch { /* yalnızca kolaylık */ }
}

function buildNav(queueCount) {
  const closed = readClosed();
  const nav = clear(document.getElementById('nav'));
  const groups = [...NAV.map((g) => ({ ...g, soon: false })), { group: 'Yol haritasıyla gelecek', items: [], soon: true }];
  for (const g of groups) {
    const list = h('ul', { class: 'nav-items' });
    const wrap = h('div', { class: `nav-group${g.soon ? ' nav-soon' : ''}${closed.has(g.group) ? ' closed' : ''}` });
    const title = h('button', {
      class: 'nav-title', type: 'button', 'aria-expanded': closed.has(g.group) ? 'false' : 'true',
      onclick: () => {
        const isClosed = wrap.classList.toggle('closed');
        title.setAttribute('aria-expanded', isClosed ? 'false' : 'true');
        if (isClosed) closed.add(g.group); else closed.delete(g.group);
        writeClosed(closed);
      },
    }, g.group, icon('chev', 'icon chev'));
    if (g.soon) {
      for (const [label, phase] of SOON) {
        list.append(h('li', {}, h('span', { class: 'nav-button', 'aria-disabled': 'true' }, label, h('em', {}, phase))));
      }
    } else {
      for (const id of g.items) {
        const r = ROUTES[id];
        list.append(h('li', {}, h('a', { class: 'nav-button', href: `#/${id}`, dataset: { route: id } },
          h('span', { class: 'nav-icon' }, icon(r.icon)), r.title,
          id === 'kuyruk' && queueCount ? h('span', { class: 'nav-count', 'aria-label': `${queueCount} bekleyen` }, String(queueCount)) : null)));
      }
    }
    wrap.append(title, list);
    nav.append(wrap);
  }
}

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
  document.body.classList.remove('nav-open');
  document.getElementById('menuToggle').setAttribute('aria-expanded', 'false');
  if (current && current.route === route && current.update) {
    current.update(param);
    return;
  }
  closeDetail({ silent: true });
  clear(view);
  setHeader({ title: ROUTES[route].title, crumbs: [[ROUTES[route].title]] });
  try {
    const instance = (await ROUTES[route].render(view, param)) || {};
    current = { route, update: instance.update };
  } catch (err) {
    current = null;
    clear(view).append(errorBox(`Sayfa yüklenemedi: ${err.message}`));
    console.error(err);
  }
  // Sayfa bir paneli veya pencereyi açtıysa (ör. #/ci/<sys_id> doğrudan bağlantısı) odak orada kalır; geç biten
  // bir istekten sonra odağı ana alana çekmek, kullanıcının o arada odakladığı düğmeyi elinden alırdı.
  if (!isDetailOpen() && !isOverlayOpen()) document.getElementById('main').focus({ preventScroll: true });
}

async function searchItems(query = '') {
  const items = Object.entries(ROUTES).map(([id, r]) => ({ group: 'Sayfalar', label: r.title, kind: 'Sayfa', href: `#/${id}` }));
  try {
    const [cis, src, queue] = await Promise.all([
      query ? globalSearch(query, 25) : Promise.resolve({ items: [] }), sourcesView(), queueView('open')]);
    const group = cis.degraded ? 'Konfigürasyon öğeleri (yedek arama)' : 'Konfigürasyon öğeleri';
    for (const c of cis.items) {
      items.push({ group, label: c.name, sub: [c.class_label, c.ip_address, c.fqdn].filter(Boolean).join(' · '),
        kind: c.class_label, href: `#/ci/${c.sys_id}`, keywords: `${c.sys_id} ${c.os || ''} ${c.sources.join(' ')}`,
        matched: cis.engine === 'opensearch' });
    }
    for (const s of src.sources) {
      items.push({ group: 'Veri kaynakları', label: s.label_tr, sub: `${s.name} · ${s.role_label}`, kind: 'Kaynak', href: `#/kaynaklar/${s.name}`,
        keywords: `${s.product?.name || ''} ${s.product?.vendor || ''} ${s.connector || ''}` });
    }
    for (const q of queue.items) {
      items.push({ group: 'Eşleştirme kuyruğu', label: `${q.code || q.id} · ${q.record.name || q.source_key}`, sub: q.summary, kind: q.data_source,
        href: `#/kuyruk/${q.id}` });
    }
  } catch { /* veri alınamazsa yalnızca sayfalar */ }
  return items;
}

function openHelp() {
  const { route } = parseHash();
  const r = ROUTES[route];
  dialog({
    title: `${r.title} · yardım`,
    body: [
      h('p', {}, r.help),
      h('p', {}, 'Klavye: Ctrl+K hızlı arama · ? bu yardım · Esc paneli veya pencereyi kapatır · Tab ile satırlara gelip Enter ile açın.'),
      h('p', { class: 'muted' }, document.body.dataset.mode === 'api'
        ? 'Veriler Evren API\'sinden gelir; yazma işlemleri yetkinize göre açılır.'
        : 'Demo modu: CI, çalışma ve kuyruk kayıtları sentetiktir; kurallar, roller ve eşleme dosyaları gerçek metadata\'dır.'),
    ],
  });
}

function setupShell() {
  append(document.getElementById('brandMark'), icon('orbit'));
  append(document.getElementById('menuToggle'), icon('menu'));
  append(document.getElementById('helpOpen'), icon('help'));
  document.getElementById('searchOpen').prepend(icon('search'));
  document.getElementById('searchOpen').addEventListener('click', () => palette(searchItems));
  document.getElementById('helpOpen').addEventListener('click', openHelp);
  document.getElementById('closeDetail').addEventListener('click', () => closeDetail());
  document.getElementById('menuToggle').addEventListener('click', (ev) => {
    const open = document.body.classList.toggle('nav-open');
    ev.currentTarget.setAttribute('aria-expanded', open ? 'true' : 'false');
  });
  document.addEventListener('keydown', (ev) => {
    const typing = /^(INPUT|SELECT|TEXTAREA)$/.test(document.activeElement?.tagName || '');
    if ((ev.ctrlKey || ev.metaKey) && fold(ev.key) === 'k') { ev.preventDefault(); palette(searchItems); return; }
    if (ev.key === 'Escape') {
      if (closeOverlay()) { ev.preventDefault(); return; }
      if (isDetailOpen()) { ev.preventDefault(); closeDetail(); return; }
      if (document.body.classList.contains('nav-open')) document.body.classList.remove('nav-open');
    }
    if (ev.key === '?' && !typing && !isOverlayOpen()) { ev.preventDefault(); openHelp(); }
  });
}

async function setupIdentity() {
  const { mode, me } = await backend();
  document.body.dataset.mode = mode;
  const flag = document.querySelector('.demo-flag');
  const box = document.querySelector('.identity');
  if (mode === 'api') {
    const s = (await overview()).summary;
    flag.hidden = !s.synthetic;
    clear(box).append(h('strong', {}, me.name), h('span', {}, me.roles.map((r) => r.label_tr).join(', ') || 'Rol yok'));
    document.querySelector('.avatar').textContent = (me.name || '?').trim().charAt(0).toLocaleUpperCase('tr');
    if (me.mode === 'entra') {
      box.append(h('button', { class: 'btn ghost small', type: 'button', id: 'signOut', onclick: async () => {
        await fetch('/auth/logout', { method: 'POST', credentials: 'same-origin', headers: { 'X-Evren-Request': '1' } });
        location.assign('/auth/login');
      } }, 'Çıkış'));
    }
  }
}

setupShell();
buildNav(0);
setupIdentity().catch(() => {});
queueCounts().then((n) => buildNav(n)).then(() => {
  const { route } = parseHash();
  document.querySelector(`#nav a[data-route="${route}"]`)?.setAttribute('aria-current', 'page');
}).catch(() => {});
window.addEventListener('hashchange', navigate);
if (!location.hash) history.replaceState(null, '', '#/genel-bakis');
navigate();
