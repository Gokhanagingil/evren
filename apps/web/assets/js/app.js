// Evren arayüzü — derlemesiz ES modülleri, hash tabanlı yönlendirme. Kabuk GRC Platformu (tedarik) düzenindedir.
import { h, append, clear, errorBox, fold } from './lib/dom.js';
import { icon } from './lib/icons.js';
import { setupNotifications } from './lib/notifications.js';
import { backend, globalSearch, guide, overview, queueCounts, queueView, sourcesView } from './lib/data.js';
import { closeDetail, closeOverlay, dialog, isDetailOpen, isOverlayOpen, palette, setHeader } from './lib/shell.js';
import { renderOverview } from './pages/overview.js';
import { renderQueue } from './pages/queue.js';
import { renderCis } from './pages/cis.js';
import { renderControls } from './pages/controls.js';
import { renderAttestation } from './pages/attestation.js';
import { renderHealth } from './pages/health.js';
import { renderStaleness } from './pages/staleness.js';
import { renderGraph } from './pages/graph.js';
import { renderSystem } from './pages/system.js';
import { renderAdmin } from './pages/admin.js';
import { renderAudit } from './pages/audit.js';
import { renderUsers } from './pages/users.js';
import { renderTasks } from './pages/tasks.js';
import { renderDoc, renderGuide } from './pages/guide.js';
import { renderShadow } from './pages/shadow.js';
import { renderSchema } from './pages/schema.js';
import { renderModel } from './pages/model.js';
import { renderSources } from './pages/sources.js';
import { renderRules } from './pages/rules.js';
import { renderBigPicture } from './pages/bigpicture.js';
import { renderArchitecture } from './pages/architecture.js';
import { renderRoadmap } from './pages/roadmap.js';
import { renderReplica } from './pages/replica.js';

const ROUTES = {
  'genel-bakis': { title: 'Genel Bakış', icon: 'home', render: renderOverview,
    help: 'Karar bekleyen işler, kaynak kapsamı ve kaynak sağlığı tek ekranda. Kartlar ilgili listeyi filtreli açar.' },
  kuyruk: { title: 'Eşleştirme Kuyruğu', tab: 'Eşleştirme', icon: 'inbox', render: renderQueue,
    help: 'Otomatik birleştirilmeyen kaynak kayıtları. Bir kayda tıklayın: kaynak kaydı ve adaylar yan yana, her kanıt ağırlığıyla açıklanır. Kararlar geri alınabilir.' },
  ci: { title: 'Konfigürasyon Öğeleri', tab: 'Öğeler', icon: 'server', render: renderCis,
    help: 'Golden record. Bir CI açıldığında her özniteliğin hangi kaynaktan, hangi kuralla geldiği ve hangi değerlerin ezildiği görünür.' },
  'iliski-haritasi': { title: 'İlişki Haritası', tab: 'İlişki haritası', icon: 'graph', render: renderGraph,
    help: 'Bir CI\'ın çevresi: bu CI\'a bağımlı olanlar üstte, bağımlılıkları altta, fiziksel bağlantılar aynı düzeyde kesikli. Bir düğüme tıklayın veya Tab ile gelip Enter\'a basın: ilişkileri ve bildiren kaynaklar sağda görünür; "Merkeze al" haritayı o CI\'a taşır. "An" ile geçmiş bir tarihteki ilişkiler gösterilir.' },
  'veri-modeli': { title: 'Veri Modeli', tab: 'Veri modeli', icon: 'tree', render: renderModel,
    help: 'ServiceNow uyumlu sınıf ağacı. Bir sınıfa tıklayın veya Tab ile gelip Enter\'a basın: alanları (kalıtılanlar dahil), kimlik kuralı, izinli ilişkiler ve SQL görünümü açılır. Değişiklik yalnızca değişiklik setiyle yapılır.' },
  'sahiplik-onayi': { title: 'Sahiplik Onayı', tab: 'Sahiplik onayı', icon: 'check', render: renderAttestation,
    help: 'CI sahibi kaydı dönemsel olarak onaylar veya gerekçeyle reddeder. "Bende" görünümünde toplu onay; sahipsiz ve reddedilen CI\'lar Veri sorumlusu içindir. Onay uzlaştırıcıdan geçer.' },
  eskime: { title: 'Eskime', icon: 'clock', render: renderStaleness,
    help: 'Gözlemci kaynakların (keşif, sanallaştırma, ağ, izleme, uç nokta) hiçbirinin uzun süredir görmediği CI\'lar. Kaynakların son çalışması sağlıklı değilse öneri ilerlemez. Emekliye ayırma veya koruma Veri sorumlusunun gerekçeli kararıdır; CI silinmez.' },
  saglik: { title: 'CMDB Sağlığı', tab: 'Sağlık', icon: 'heart', render: renderHealth,
    help: 'Genel puan ve dört boyut: tamlık, doğruluk, uyum, kapsam. Kurallar sekmesinde Konfigürasyon yöneticisi kural tanımlar (şablondan başlayarak) ve bulguları önizler; görev çalışanı kuralı düzenli değerlendirir, bulgunun sorumlusuna görev açar, düzeltilince görev kendiliğinden kapanır. Bulgular sekmesinde neden, sorumlu ve istisna.' },
  kontroller: { title: 'GRC Kontrolleri', tab: 'GRC kontrolleri', icon: 'shield', render: renderControls,
    help: 'GRC Platformu\'na giden CMDB tabanlı kontrol sonuçları. Kaynak verisi kısmi toplandıysa sonuç "kesin değil" olur.' },
  'golge-mod': { title: 'Gölge Mod Raporu', tab: 'Gölge mod', icon: 'layers', render: renderShadow,
    help: 'Mevcut CMDB (ServiceNow) ile Evren gözlemlerinin karşılaştırması: keşfin görmediği kayıtlar, CMDB\'de olmayan keşif kayıtları, mükerrer adayları ve değer farkları. Rapor GRC Platformu için değiştirilemez kanıt olarak kaydedilebilir.' },
  replika: { title: 'ServiceNow Replikası', tab: 'SN replikası', icon: 'layers', render: renderReplica,
    help: 'Evren ana kaynaktır, ServiceNow CMDB replikadır. Kapsamdaki CI\'lar ServiceNow\'a kimlik ve uzlaştırma API\'siyle (IRE) yazılır. Önce gölge modda çalışır: ServiceNow\'a yazılmaz, kayıtların nasıl tanınacağı denenir. Etkin modda bir CI\'ın ilk itmesi yine önce denenir; ServiceNow beklenenden başka bir kayıt bulursa yazılmaz, "Karar bekleyenler"e düşer. Tur sınırını aşan toplu değişiklik onay bekler. Konfigürasyon yöneticisi ve Platform yöneticisi yönetir.' },
  kaynaklar: { title: 'Veri Kaynakları', tab: 'Veri kaynakları', icon: 'plug', render: renderSources,
    help: 'Her veri kaynağı bir rolü doldurur. Kurallar role bağlı olduğu için ürün değiştiğinde kurallar değişmez. Devre kesici kısmi çalışmada yıkıcı işlemleri durdurur.' },
  sema: { title: 'Şema ve Değişiklikler', tab: 'Şema', icon: 'key', render: renderSchema,
    help: 'Sınıf, alan, seçim listesi ve veri kaynağı yaşam döngüsü değişiklikleri taslak → onay → yayın akışıyla yapılır. Hazırlayan onaylayamaz. Yayınlanan set metadata/changes/ altına değişiklik dosyası olarak PR ile eklenir.' },
  kurallar: { title: 'Kurallar', icon: 'sliders', render: renderRules,
    help: 'Uzlaştırma (rol önceliği), tanımlama (IRE), veri tazeleme ve eskime kuralları. Kaynak: metadata/*.yaml.' },
  yonetim: { title: 'Yönetim Paneli', tab: 'Panel', icon: 'gear', render: renderAdmin,
    help: 'Bütün yönetim işlerinin başlangıç noktası. Bir görev seçin: Evren ilgili pencereyi veya sayfayı açar. Yetkiniz olmayan görev gri görünür ve hangi rolün gerektiğini söyler. "Size düşen işler" sizin karar vermeniz gereken bekleyen işlerdir.' },
  gorevler: { title: 'Görevler', icon: 'check', render: renderTasks,
    help: 'Size ve grubunuza atanan yönetişim görevleri. Bir göreve tıklayın: ne yapılacağı, CI, son tarih ve "Kararı ver". Görev ServiceNow\'da açıldıysa da karar burada verilir; Evren ServiceNow kaydını kararla kapatır. Konfigürasyon yöneticisi görev açar, iptal eder, atanmamış ve dikkat isteyen görevleri ve kanalları yönetir.' },
  kullanicilar: { title: 'Kullanıcılar ve Roller', tab: 'Kullanıcılar', icon: 'key', render: renderUsers,
    help: 'Kişiler, gruplar, rol–yetki matrisi ve yetki geçmişi. Bir kişiye tıklayın: rolleri, kimin ne zaman hangi gerekçeyle verdiği ve "Rol ver" / "Geri al". Yetkili roller süreli verilir; kimse kendine rol veremez; birleşemeyen roller (görevler ayrılığı) önceden uyarılır. Yetki yöneticisi yönetir, Denetçi görür.' },
  kilavuz: { title: 'Kullanım Kılavuzu', tab: 'Kılavuz', icon: 'book', render: renderGuide,
    help: 'Önce genel bilgi, ayrıntı konu sayfalarında, teknik belgeler bağlantıyla. Soldaki aramayla konu bulun; her sayfanın sonunda önceki ve sonraki konuya geçin.' },
  belge: { title: 'Teknik Belge', icon: 'book', render: renderDoc,
    help: 'Mimari, güvenlik, API ve karar kayıtları. Oturum açılmış canlı kurulumda uygulama içinde açılır.' },
  denetim: { title: 'Denetim İzi', tab: 'Denetim izi', icon: 'shield', render: renderAudit,
    help: 'Kim, ne zaman, neyi, nereden ve hangi sonuçla yaptı. Olaylar sekmesinde dönem, akış, kategori ve sonuçla süzün; bir olaya tıklayın: ayrıntı, kaynak IP, istek kimliği ve mührü. "Bu kişinin olayları" ve "Aynı istekteki olaylar" ile iz sürün; CSV veya NDJSON indirin (aktarma da kaydedilir). Bütünlük sekmesinde saatlik zincirli mühürler ve doğrulama; İncelemeler sekmesinde dönemi gözden geçirip kaydedin. Yalnızca Denetçi görür.' },
  'sistem-durumu': { title: 'Sistem Durumu', tab: 'Sistem durumu', icon: 'heart', render: renderSystem,
    help: 'Veritabanı, göçler, Kafka, OpenSearch, servis nabızları (uzlaştırıcı, alım tüketicisi, dizinleyici, bağlayıcı çalıştırıcı, alım geçidi), olay yayını, istek kuyrukları ve kaynak çalışmaları. "Bu kurulumda yok": servis veya bileşen bu ortamda çalışmıyor. Yalnızca Platform operatörü, Veri sorumlusu ve Denetçi görür. Uyarılar bölümünde görev çalışanının kurallarla açtığı platform uyarıları; Platform operatörü gerekçeyle onaylar, koşul kalkınca uyarı kendiliğinden çözülür. Bildirimler bölümünde e-posta ve Teams kanallarının durumu, rol ve grup yönlendirmeleri ve sınama bildirimi.' },
  'buyuk-resim': { title: 'Büyük Resim', tab: 'Büyük resim', icon: 'orbit', render: renderBigPicture,
    help: 'Tek sayfalık mimari özet: kaynak rolleri, alım, ara katman, kayıt sistemi, tüketim, ServiceNow geçişi, güvenlik ve fazlar.' },
  mimari: { title: 'Mimari Harita', tab: 'Mimari', icon: 'map', render: renderArchitecture,
    help: 'Bileşen düzeyinde harita. Bir kutuya tıklayın veya Tab ile gelip Enter\'a basın; ayrıntı ve yol haritası bağlantısı açılır. Esc kapatır.' },
  'yol-haritasi': { title: 'Yol Haritası', tab: 'Yol haritası', icon: 'flag', render: renderRoadmap,
    help: 'Fazlar, adımlar, çıkış kriterleri ve sizden gereken karar/erişimler. Aynı plan docs/PROJE-PLANI.md dosyasındadır.' },
};

// Gezinme (U8.10): altı bölüm uygulama çubuğunda (ikon + kısa ad); bölümün sayfaları üst çubukta sekme.
// Bir bölüme dönülünce o bölümde en son açılan sekme açılır.
const HUBS = [
  { id: 'ana', label: 'Ana sayfa', icon: 'home', routes: ['genel-bakis'] },
  { id: 'islerim', label: 'İşlerim', icon: 'inbox', routes: ['gorevler', 'kuyruk', 'sahiplik-onayi'] },
  { id: 'cmdb', label: 'CMDB', icon: 'server', routes: ['ci', 'iliski-haritasi', 'saglik', 'eskime', 'veri-modeli'] },
  { id: 'kaynaklar', label: 'Kaynaklar', icon: 'plug', routes: ['kaynaklar', 'kurallar', 'golge-mod', 'replika', 'kontroller'] },
  { id: 'yonetim', label: 'Yönetim', icon: 'gear', routes: ['yonetim', 'kullanicilar', 'sema', 'denetim', 'sistem-durumu'] },
  { id: 'kilavuz', label: 'Kılavuz', icon: 'book', routes: ['kilavuz', 'buyuk-resim', 'mimari', 'yol-haritasi'], also: ['belge'] },
];
const HUB_OF = Object.fromEntries(HUBS.flatMap((hb) => [...hb.routes, ...(hb.also || [])].map((r) => [r, hb])));
const lastRoute = {};
let queuePending = 0;

function buildNav(queueCount = queuePending) {
  queuePending = queueCount;
  const nav = clear(document.getElementById('nav'));
  for (const hub of HUBS) {
    nav.append(h('a', { class: 'hub', href: `#/${lastRoute[hub.id] || hub.routes[0]}`, dataset: { hub: hub.id } },
      h('span', { class: 'hub-icon' }, icon(hub.icon),
        hub.id === 'islerim' && queueCount ? h('span', { class: 'nav-count', 'aria-label': `${queueCount} bekleyen` }, String(queueCount)) : null),
      h('span', { class: 'hub-label' }, hub.label)));
  }
  markNav(parseHash().route);
}

function markNav(route) {
  const hub = HUB_OF[route];
  if (hub) lastRoute[hub.id] = hub.routes.includes(route) ? route : lastRoute[hub.id];
  document.querySelectorAll('#nav a.hub').forEach((a) => {
    if (a.dataset.hub === hub?.id) a.setAttribute('aria-current', 'page');
    else a.removeAttribute('aria-current');
    const h0 = HUBS.find((x) => x.id === a.dataset.hub);
    a.setAttribute('href', `#/${lastRoute[h0.id] || h0.routes[0]}`);
  });
  const sub = clear(document.getElementById('subnav'));
  sub.hidden = !hub || hub.routes.length < 2;
  if (sub.hidden) return;
  for (const r of hub.routes) {
    sub.append(h('a', { class: 'subtab', href: `#/${r}`, dataset: { route: r }, 'aria-current': r === route ? 'page' : null },
      ROUTES[r].tab || ROUTES[r].title,
      r === 'kuyruk' && queuePending ? h('span', { class: 'nav-count', 'aria-label': `${queuePending} bekleyen` }, String(queuePending)) : null));
  }
}

function setupAccount() {
  const button = document.getElementById('meButton');
  const panel = document.getElementById('mePanel');
  const close = () => { panel.hidden = true; button.setAttribute('aria-expanded', 'false'); };
  button.addEventListener('click', (ev) => {
    ev.stopPropagation();
    panel.hidden = !panel.hidden;
    button.setAttribute('aria-expanded', panel.hidden ? 'false' : 'true');
  });
  document.addEventListener('click', (ev) => { if (!panel.hidden && !panel.contains(ev.target)) close(); });
  document.addEventListener('keydown', (ev) => { if (ev.key === 'Escape' && !panel.hidden) { close(); button.focus(); } });
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
  markNav(route);
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
  // Sayfa kendi içinde bir öğeye odak verdiyse (ör. #/sistem-durumu/uyarilar bölümü) odak orada kalır.
  if (!isDetailOpen() && !isOverlayOpen() && !view.contains(document.activeElement)) {
    document.getElementById('main').focus({ preventScroll: true });
  }
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

async function openHelp() {
  const { route } = parseHash();
  const r = ROUTES[route];
  // Ekranın kılavuzdaki konusu (docs/kilavuz/kilavuz.yaml → routes); yoksa kılavuz başlangıcı
  const g = await guide().catch(() => null);
  const topicId = g?.route_topics?.[route];
  const topic = topicId && g.topics.find((x) => x.id === topicId);
  const guideLink = h('p', { class: 'help-guide' }, icon('book'), ' ', topic
    ? h('a', { href: `#/kilavuz/${topic.id}`, id: 'helpGuideLink', onclick: () => closeOverlay() }, `Kılavuzda aç: ${topic.title}`)
    : h('a', { href: '#/kilavuz', id: 'helpGuideLink', onclick: () => closeOverlay() }, 'Kullanım kılavuzunu aç'));
  dialog({
    title: `${r.title} · yardım`,
    body: [
      h('p', {}, r.help),
      guideLink,
      h('p', {}, 'Klavye: Ctrl+K hızlı arama · ? bu yardım · Esc paneli veya pencereyi kapatır · Tab ile satırlara gelip Enter ile açın.'),
      h('p', { class: 'muted' }, document.body.dataset.mode === 'api'
        ? 'Veriler Evren API\'sinden gelir; yazma işlemleri yetkinize göre açılır.'
        : 'Demo modu: CI, çalışma ve kuyruk kayıtları sentetiktir; kurallar, roller ve eşleme dosyaları gerçek metadata\'dır.'),
    ],
  });
}

function setupShell() {
  append(document.getElementById('brandMark'), icon('orbit'));
  append(document.getElementById('helpOpen'), icon('help'));
  document.getElementById('searchOpen').prepend(icon('search'));
  document.getElementById('searchOpen').addEventListener('click', () => palette(searchItems));
  document.getElementById('helpOpen').addEventListener('click', openHelp);
  document.getElementById('closeDetail').addEventListener('click', () => closeDetail());
  setupAccount();
  document.addEventListener('keydown', (ev) => {
    const typing = /^(INPUT|SELECT|TEXTAREA)$/.test(document.activeElement?.tagName || '');
    if ((ev.ctrlKey || ev.metaKey) && fold(ev.key) === 'k') { ev.preventDefault(); palette(searchItems); return; }
    if (ev.key === 'Escape') {
      if (closeOverlay()) { ev.preventDefault(); return; }
      if (isDetailOpen()) { ev.preventDefault(); closeDetail(); return; }
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
    const avatar = document.getElementById('meButton');
    avatar.textContent = (me.name || '?').trim().charAt(0).toLocaleUpperCase('tr');
    avatar.setAttribute('aria-label', `Hesabım: ${me.name}`);
    avatar.title = me.name;
    setupNotifications();
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
queueCounts().then((n) => buildNav(n)).catch(() => {});
window.addEventListener('hashchange', navigate);
if (!location.hash) history.replaceState(null, '', '#/genel-bakis');
navigate();
