// Yönetim paneli (U8.2, ADR-0033): bütün yönetim işlerinin tek başlangıç noktası. Görev odaklıdır ("Ne yapmak
// istiyorsunuz?"): her görev ilgili akışı açar (pencere veya sayfa), kılavuzdaki konusuna bağlanır. Yetki yoksa görev
// gri görünür ve hangi rolün gerektiği yazar; hazırlanmakta olan görev açıkça işaretlidir. Üstte kişiye düşen
// bekleyen işler. Derin bağlantı: #/yonetim/<görev> görevi bulur, odaklar ve yetki varsa başlatır.
import { h, clear, fold, pill } from '../lib/dom.js';
import { icon } from '../lib/icons.js';
import { closeOverlay, dialog, setHeader, toast } from '../lib/shell.js';
import { adminAttention, backend, ciDetail, ciSearch } from '../lib/data.js';
import { openCreateDialog } from './ci-create.js';
import { openImportDialog } from './ci-import.js';
import { openAddRelationDialog } from './ci-relations.js';
import { openChangeSetFor } from './schema.js';
import { openClassWizard, openFieldWizard } from './schema-wizard.js';
import { openIntegrationWizard } from './integration-wizard.js';

/** Yetenek → bu işi yapabilen rol (yetkisiz kullanıcıya yol gösterir). */
const ROLE_FOR = {
  create_ci: 'Konfigürasyon analisti', relate: 'Konfigürasyon analisti', review_match: 'Eşleştirme analisti',
  schema_draft: 'Şema tasarımcısı', schema_approve: 'Şema onaylayıcı', connector_admin: 'Veri sorumlusu',
  retire: 'Veri sorumlusu', anomaly_review: 'Veri sorumlusu', attest: 'CI sahibi', system: 'Platform operatörü',
  iam_admin: 'Yetki yöneticisi', audit_read: 'Denetçi', iam_read: 'Yetki yöneticisi veya Denetçi',
  task_admin: 'Konfigürasyon yöneticisi',
};

/** CI seçici: ilişki eklemek gibi bir CI üzerinde yapılan işler için. */
function pickCi(title, onPick) {
  const input = h('input', { type: 'search', id: 'pickCiSearch', class: 'searchbar', placeholder: 'Ad, IP veya FQDN yazın',
    'aria-label': 'CI ara', autocomplete: 'off', 'aria-controls': 'pickCiResults' });
  const list = h('ul', { class: 'pick-list', id: 'pickCiResults', 'aria-label': 'Sonuçlar', 'aria-live': 'polite' });
  let timer = null;
  input.addEventListener('input', () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const q = input.value.trim();
      if (q.length < 2) { clear(list); return; }
      const res = await ciSearch({ q, limit: 8 });
      clear(list).append(...res.items.map((c) => h('li', {}, h('button', { class: 'pick-item', type: 'button',
        dataset: { id: c.sys_id }, onclick: () => { closeOverlay(); onPick(c.sys_id); } },
      h('strong', {}, c.name), h('span', { class: 'cell-sub' }, c.class_label)))));
      if (!res.items.length) list.append(h('li', { class: 'muted small' }, 'Eşleşen CI yok.'));
    }, 250);
  });
  dialog({ title, body: [h('p', { class: 'muted small' }, 'Önce işlem yapılacak CI\'ı seçin.'),
    h('label', { class: 'field', for: 'pickCiSearch' }, 'CI'), input, list], focus: input });
}

/** Görevler: grup → [{ id, title, desc, icon, cap?, run?|href?, soon?, guide }]. */
function tasks(goCi) {
  return [
    { group: 'CMDB kayıtları', desc: 'Kayıt eklemek, bulmak, düzeltmek ve ilişkilendirmek.', items: [
      { id: 'yeni-ci', title: 'Yeni CI ekle', icon: 'server', cap: 'create_ci', guide: 'ci-ekleme',
        desc: 'Tek bir kaydı elle girin; Evren önce var olan bir kayıtla eşleşip eşleşmediğine bakar.',
        run: () => openCreateDialog(async (id) => { location.hash = `#/ci/${id}`; }) },
      { id: 'csv', title: 'CSV ile toplu içe aktar', icon: 'layers', cap: 'create_ci', guide: 'ci-ekleme',
        desc: 'Bir dosyadaki kayıtları önce önizleyin, sonra onaylayın. Önizleme hiçbir şey yazmaz.',
        run: () => openImportDialog(() => { location.hash = '#/ci'; }) },
      { id: 'ci-bul', title: 'CI bul ve düzenle', icon: 'search', guide: 'ci-bulma', href: '#/ci',
        desc: 'Ara, süz, bir CI açıp değerlerinin nereden geldiğini gör ve düzelt.' },
      { id: 'iliski', title: 'İlişki ekle', icon: 'graph', cap: 'relate', guide: 'ci-ekleme',
        desc: 'Bir CI seçin ve başka bir CI ile ilişkilendirin; izin verilmeyen ilişki reddedilir.',
        run: () => pickCi('İlişki ekle: CI seçin', async (id) => {
          const c = await ciDetail(id);
          if (c) openAddRelationDialog(c, () => goCi(id));
        }) },
      { id: 'harita', title: 'İlişki haritasını görüntüle', icon: 'graph', guide: 'ci-bulma', href: '#/iliski-haritasi',
        desc: 'Bir CI\'ın çevresi: ona bağımlı olanlar ve bağımlılıkları, istenirse geçmiş bir anda.' },
      { id: 'kuyruk', title: 'Eşleştirme kararlarını ver', icon: 'inbox', cap: 'review_match', guide: 'eslestirme',
        href: '#/kuyruk', desc: 'Evren\'in emin olamadığı eşleşmeleri karara bağlayın.' },
    ] },
    { group: 'Veri modeli', desc: 'Sınıf ve alanlar değişiklik setiyle eklenir: taslak, başka birinin onayı, yayın.', items: [
      { id: 'sinif', title: 'Yeni sınıf ekle', icon: 'tree', cap: 'schema_draft', guide: 'veri-modeli',
        desc: 'Adım adım: üst sınıf, ad ve öznitelikler; doğrulanır ve onaya gönderilir (ör. yeni bir cihaz tipi).',
        run: () => openClassWizard() },
      { id: 'alan', title: 'Sınıfa alan ekle', icon: 'tree', cap: 'schema_draft', guide: 'veri-modeli',
        desc: 'Adım adım: sınıf, alanlar (seçim değerleriyle); alt sınıflar devralır.',
        run: () => openFieldWizard() },
      { id: 'secim', title: 'Seçim listesine değer ekle', icon: 'sliders', cap: 'schema_draft', guide: 'veri-modeli',
        desc: 'Bir açılır listeye yeni bir değer ekleyin.', run: (opener) => openChangeSetFor({ op: 'add_choice' }, opener) },
      { id: 'degisiklik-seti', title: 'Değişiklik seti hazırla (gelişmiş)', icon: 'layers', cap: 'schema_draft', guide: 'veri-modeli',
        desc: 'Birden çok işlemi (alan değiştirme, kullanımdan kaldırma…) tek sette elle hazırlayın.',
        run: (opener) => openChangeSetFor({ op: 'modify_field' }, opener) },
      { id: 'sema-onay', title: 'Değişiklikleri onayla ve yayımla', icon: 'check', cap: 'schema_approve', guide: 'veri-modeli',
        href: '#/sema', desc: 'Başkasının hazırladığı değişiklik setini inceleyin, onaylayın veya reddedin.' },
      { id: 'model', title: 'Veri modelini incele', icon: 'tree', guide: 'veri-modeli', href: '#/veri-modeli',
        desc: 'Sınıf ağacı, alanlar, kimlik kuralları ve izinli ilişkiler.' },
    ] },
    { group: 'Kullanıcılar ve yetkiler', desc: 'Kim ne yapabilir; roller ve görevler ayrılığı.', items: [
      { id: 'kullanici', title: 'Kişi ekle', icon: 'key', cap: 'iam_admin', guide: 'kullanicilar', href: '#/kullanicilar/yeni',
        desc: 'ServiceNow veya Entra eşitlemesinde henüz olmayan bir kişiyi kaydedin; ardından rol verin.' },
      { id: 'rol-ver', title: 'Rol ver veya geri al', icon: 'check', cap: 'iam_admin', guide: 'kullanicilar', href: '#/kullanicilar',
        desc: 'Kişiyi veya grubu seçin; rol gerekçeyle, yetkili roller süreli verilir. Hemen geçerli olur.' },
      { id: 'matris', title: 'Rol–yetki matrisini incele', icon: 'shield', cap: 'iam_read', guide: 'kullanicilar', href: '#/kullanicilar/roller',
        desc: 'Hangi rol hangi işlemi yapabilir; birleşemeyen roller ve rolü taşıyanlar.' },
      { id: 'yetki-gecmisi', title: 'Yetki geçmişini incele', icon: 'clock', cap: 'iam_read', guide: 'kullanicilar', href: '#/kullanicilar/gecmis',
        desc: 'Kim kime hangi rolü ne zaman, hangi gerekçeyle verdi veya geri aldı.' },
    ] },
    { group: 'Entegrasyonlar', desc: 'Veri kaynakları, bağlayıcılar ve çalışmalar.', items: [
      { id: 'yeni-entegrasyon', title: 'Yeni entegrasyon kur', icon: 'plug', cap: 'connector_admin', guide: 'entegrasyonlar',
        desc: 'Adım adım: kaynak türü, bağlantı, zamanlama, geçit kimliği, kurulum paketi ve deneme çalışması.',
        run: () => openIntegrationWizard() },
      { id: 'kaynaklar', title: 'Veri kaynaklarını görüntüle', icon: 'plug', guide: 'entegrasyonlar', href: '#/kaynaklar',
        desc: 'Her kaynağın rolü, son çalışması, devre kesici durumu ve kapsamı.' },
      { id: 'kimlik', title: 'Bağlayıcıya kimlik ver veya iptal et', icon: 'key', cap: 'connector_admin',
        guide: 'entegrasyonlar', href: '#/kaynaklar', desc: 'Kaynağı seçin, panelinde "Kimlik ver" deyin; belirteç bir kez gösterilir.' },
      { id: 'calistir', title: 'Bir kaynağı şimdi çalıştır', icon: 'clock', cap: 'connector_admin', guide: 'entegrasyonlar',
        href: '#/kaynaklar', desc: 'Tam liste veya yalnızca değişenler için çalışma başlatın.' },
      { id: 'yeni-kaynak', title: 'Yeni veri kaynağı ekle', icon: 'plug', cap: 'schema_draft', guide: 'entegrasyonlar',
        desc: 'Yeni bir aracı bir role bağlayın; önce gölge modda çalışır.',
        run: (opener) => openChangeSetFor({ op: 'add_data_source' }, opener) },
      { id: 'kaynak-yasam', title: 'Kaynağı gölgeye al, etkinleştir veya emekliye ayır', icon: 'layers', cap: 'schema_draft',
        guide: 'entegrasyonlar', desc: 'Ürün değişiminde yeni kaynak gölgeden etkine, eskisi emekliye geçer.',
        run: (opener) => openChangeSetFor({ op: 'set_source_lifecycle' }, opener) },
      { id: 'kurallar', title: 'Kuralları incele', icon: 'sliders', guide: 'entegrasyonlar', href: '#/kurallar',
        desc: 'Uzlaştırma, tanımlama, veri tazeleme ve eskime kuralları.' },
    ] },
    { group: 'Yönetişim görevleri', desc: 'Sorumlulara zamanlanmış ve kurala bağlı görevler; şimdilik ServiceNow, ileride Evren üzerinden.', items: [
      { id: 'zamanli-gorev', title: 'Zamanlanmış görev tanımla', icon: 'clock', cap: 'task_admin', guide: 'gorevler',
        href: '#/gorevler/kampanyalar/yeni',
        desc: 'Ör. uygulama sahiplerine her çeyrek veri güncelliği teyidi veya erişim gözden geçirme.' },
      { id: 'kampanyalar', title: 'Kampanyaları izle', icon: 'calendar', cap: 'task_admin', guide: 'gorevler',
        href: '#/gorevler/kampanyalar', desc: 'Etkin, duraklatılmış ve taslak kampanyalar; çalışmalar ve açık görev sayıları.' },
      { id: 'kural-gorevi', title: 'Kuraldan görev aç', icon: 'sliders', cap: 'task_admin', guide: 'saglik',
        href: '#/saglik/kurallar/yeni',
        desc: 'Ör. hiçbir uygulama veya sunucuyla ilişkisi olmayan veritabanı bulununca sahibine görev.' },
      { id: 'gorev-ac', title: 'Tek seferlik görev aç', icon: 'check', cap: 'task_admin', guide: 'gorevler', href: '#/gorevler/yeni',
        desc: 'Bir CI\'ın sorumlusuna (sahip → yöneten → destek grubu) veya seçtiğiniz kişiye görev açın.' },
      { id: 'gorev-izle', title: 'Görevleri izle', icon: 'inbox', guide: 'gorevler', href: '#/gorevler',
        desc: 'Size atanan görevler ve kararı; Konfigürasyon yöneticisi için bütün, atanmamış ve dikkat isteyen görevler.' },
      { id: 'gorev-kanali', title: 'Görev kanalını yönet', icon: 'plug', cap: 'task_admin', guide: 'gorevler', href: '#/gorevler/kanallar',
        desc: 'Görevlerin ServiceNow\'da mı Evren\'de mi açılacağı; hedef tablo, kapanış durumları ve varsayılan grup.' },
    ] },
    { group: 'Veri kalitesi', desc: 'Kayıtların güncel ve doğru kalması.', items: [
      { id: 'sahiplik', title: 'Sahiplik onaylarını yönet', icon: 'check', guide: 'yasam-dongusu', href: '#/sahiplik-onayi',
        desc: 'Sorumlu olduğunuz CI\'ları onaylayın; sahipsiz ve reddedilenleri izleyin.' },
      { id: 'eskime', title: 'Eskime önerilerini karara bağla', icon: 'clock', cap: 'retire', guide: 'yasam-dongusu',
        href: '#/eskime', desc: 'Uzun süredir görülmeyen CI\'ları emekliye ayırın veya koruyun.' },
      { id: 'yetkisiz', title: 'Yetkisiz değişiklikleri incele', icon: 'shield', guide: 'raporlar',
        href: '#/ci/kapsam=yetkisiz-degisiklik', desc: 'Onaylı değişiklik kaydı olmadan yapılan değişiklikler.' },
      { id: 'golge', title: 'Gölge mod raporu', icon: 'layers', guide: 'raporlar', href: '#/golge-mod',
        desc: 'Mevcut CMDB ile Evren\'in gözlemlerinin karşılaştırması.' },
      { id: 'saglik', title: 'CMDB sağlık panosu', icon: 'heart', guide: 'saglik', href: '#/saglik',
        desc: 'Tamlık, doğruluk, uyum ve kapsam puanları; kural bulguları ve eğilim.' },
      { id: 'bulgular', title: 'Bulguları izle', icon: 'inbox', guide: 'saglik', href: '#/saglik/bulgular',
        desc: 'Kuralların bulduğu CI\'lar, nedeni, sorumlusu ve görevi; istisnalar.' },
    ] },
    { group: 'İşletim', desc: 'Platformun sağlığı ve izlenebilirliği.', items: [
      { id: 'durum', title: 'Sistem durumunu izle', icon: 'heart', cap: 'system', guide: 'isletim', href: '#/sistem-durumu',
        desc: 'Veritabanı, Kafka, arama, servisler ve veri akışı tek ekranda.' },
      { id: 'uyarilar', title: 'Platform uyarılarını izle', icon: 'bell', cap: 'system', guide: 'bildirimler',
        href: '#/sistem-durumu/uyarilar', desc: 'Servis, Kafka, veri akışı ve denetim izi uyarıları; gerekçeli onay.' },
      { id: 'bildirim-yonlendirme', title: 'Bildirim kanallarını ayarla', icon: 'bell', cap: 'system', guide: 'bildirimler',
        href: '#/sistem-durumu/bildirimler', desc: 'Rol ve gruplara gelen bildirimleri e-postaya veya Teams\'e yönlendir; sına.' },
      { id: 'denetim', title: 'Denetim izini incele', icon: 'shield', cap: 'audit_read', guide: 'denetim-izi', href: '#/denetim',
        desc: 'Kim, ne zaman, neyi, nereden yaptı; ret olayları, dışa aktarma.' },
      { id: 'denetim-butunluk', title: 'Denetim izinin bütünlüğünü doğrula', icon: 'check', cap: 'audit_read', guide: 'denetim-izi',
        href: '#/denetim/butunluk', desc: 'Saatlik zincirli mühürler; kayıtların sonradan değişmediğini kanıtla.' },
      { id: 'denetim-inceleme', title: 'Haftalık incelemeyi kaydet', icon: 'calendar', cap: 'audit_read', guide: 'denetim-izi',
        href: '#/denetim/incelemeler', desc: 'Dönemin olaylarını gözden geçir; karar ve not kanıt olarak kalır.' },
    ] },
  ];
}

const TODO_FIRST = 4;

function attentionBox(items, mode) {
  const box = h('section', { class: 'panel todo-panel', id: 'adminAttention', 'aria-labelledby': 'attH' },
    h('h2', { id: 'attH' }, 'Size düşen işler'));
  if (mode !== 'api') {
    box.append(h('p', { class: 'muted small' }, 'Demo modunda bekleyen işler gösterilmez; canlı API ile size ait işler burada listelenir.'));
    return box;
  }
  if (!items.length) {
    box.append(h('p', { class: 'todo-empty' }, icon('check'), 'Şu an sizi bekleyen bir iş yok.'));
    return box;
  }
  // Önce en acil işler; ilk bakışta dört iş, gerisi isteyince (aşamalı açıklama, ADR-0041).
  const rank = { bad: 0, warn: 1, info: 2, good: 3 };
  const sorted = [...items].sort((a, b) => (rank[a.tone] ?? 9) - (rank[b.tone] ?? 9));
  const list = h('ul', { class: 'todo-list', id: 'adminTodo' }, sorted.map((i, n) => h('li', { hidden: n >= TODO_FIRST },
    h('a', { href: i.href, class: `todo-item tone-${i.tone}`, dataset: { id: i.id }, title: i.hint },
      h('span', { class: 'todo-count' }, String(i.count)),
      h('span', { class: 'todo-text' }, h('strong', {}, i.label)),
      h('span', { class: 'todo-go', 'aria-hidden': 'true' }, '→')))));
  box.append(list);
  if (sorted.length > TODO_FIRST) {
    const more = h('button', { type: 'button', class: 'btn ghost small', id: 'adminTodoMore', 'aria-controls': 'adminTodo', 'aria-expanded': 'false' },
      `Tümünü göster (${sorted.length})`);
    more.addEventListener('click', () => {
      const open = more.getAttribute('aria-expanded') !== 'true';
      list.querySelectorAll('li').forEach((li, n) => { li.hidden = !open && n >= TODO_FIRST; });
      more.setAttribute('aria-expanded', String(open));
      more.textContent = open ? 'Daha az göster' : `Tümünü göster (${sorted.length})`;
    });
    box.append(h('div', { class: 'todo-more' }, more));
  }
  return box;
}

export async function renderAdmin(view, param) {
  const { mode, me } = await backend();
  const can = (mode === 'api' && me?.can) || {};
  setHeader({
    title: 'Yönetim Paneli',
    sub: 'Ne yapmak istiyorsunuz? Bir görev seçin.',
    crumbs: [['Yönetim Paneli']],
    actions: [h('a', { class: 'btn secondary', href: '#/kilavuz/yonetim-paneli', id: 'adminGuide' }, icon('book'), 'Kullanım kılavuzu')],
  });
  const goCi = (id) => { location.hash = `#/ci/${id}`; };
  const groups = tasks(goCi);
  const search = h('input', { type: 'search', class: 'searchbar', id: 'adminSearch', autocomplete: 'off',
    placeholder: 'Görev ara: kullanıcı, alan, kaynak, CSV…', 'aria-label': 'Görev ara', 'aria-controls': 'adminTasks' });
  const count = h('span', { class: 'count', id: 'adminCount', 'aria-live': 'polite' });
  const body = h('div', { id: 'adminTasks' });
  let attention = [];
  try {
    attention = (await adminAttention()) || [];
  } catch {
    attention = [];
  }

  // Görevi başlatır; pencere açılamazsa (yetki, ağ) kullanıcı nedenini görür
  function start(t, opener) {
    Promise.resolve().then(() => t.run(opener)).catch((err) => toast(`Açılamadı: ${err.message}`));
  }

  function card(t) {
    const allowed = !t.cap || (mode === 'api' && can[t.cap]);
    const id = `task-${t.id}`;
    let action;
    if (t.soon) {
      action = h('span', { class: 'task-soon' }, pill('Hazırlanıyor', 'neutral'),
        h('span', { class: 'muted small' }, ' Bir sonraki adımda geliyor.'));
    } else if (!allowed) {
      action = h('p', { class: 'task-locked small' }, icon('key'),
        mode === 'api' ? h('span', {}, 'Bu iş için ', h('strong', {}, ROLE_FOR[t.cap] || 'ek yetki'), ' rolü gerekir.')
          : h('span', {}, 'Canlı API ile çalışır.'));
    } else if (t.run) {
      action = h('button', { class: 'btn small', type: 'button', id: `${id}-go`,
        onclick: (ev) => start(t, ev.currentTarget) }, 'Başlat');
    } else {
      action = h('a', { class: 'btn small secondary', href: t.href, id: `${id}-go` }, 'Aç');
    }
    return h('article', { class: `task-card${allowed && !t.soon ? '' : ' muted-card'}`, id, tabindex: '-1',
      dataset: { search: fold(`${t.title} ${t.desc}`) }, 'aria-labelledby': `${id}-h` },
    h('div', { class: 'task-icon', 'aria-hidden': 'true' }, icon(t.icon)),
    h('div', { class: 'task-body' },
      h('h3', { id: `${id}-h` }, t.title),
      h('p', { class: 'small' }, t.desc),
      h('div', { class: 'task-actions' }, action,
        h('a', { class: 'task-help small', href: `#/kilavuz/${t.guide}` }, 'Nasıl yapılır?'))));
  }

  const sections = groups.map((g) => h('section', { class: 'task-group', 'aria-labelledby': `grp-${fold(g.group).replace(/\W+/g, '-')}` },
    h('h2', { id: `grp-${fold(g.group).replace(/\W+/g, '-')}` }, g.group), h('p', { class: 'muted small' }, g.desc),
    h('div', { class: 'task-grid' }, g.items.map(card))));
  const empty = h('p', { class: 'notice', id: 'adminEmpty', hidden: true }, 'Eşleşen görev yok. ',
    h('a', { href: '#/kilavuz' }, 'Kullanım kılavuzunda arayın'), '.');
  body.append(...sections, empty);

  function filter() {
    const q = fold(search.value.trim());
    let shown = 0;
    for (const s of sections) {
      let any = false;
      for (const c of s.querySelectorAll('.task-card')) {
        const ok = !q || q.split(/\s+/).every((w) => c.dataset.search.includes(w));
        c.hidden = !ok;
        if (ok) { any = true; shown += 1; }
      }
      s.hidden = !any;
    }
    empty.hidden = shown > 0;
    count.textContent = q ? `${shown} görev` : '';
  }
  search.addEventListener('input', filter);
  search.addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter') {
      const first = body.querySelector('.task-card:not([hidden]) .task-actions .btn');
      if (first) { ev.preventDefault(); first.focus(); }
    }
  });

  view.append(attentionBox(attention, mode), h('div', { class: 'toolbar' }, search, count), body);

  async function update(p) {
    if (!p) return;
    const all = groups.flatMap((g) => g.items);
    const t = all.find((x) => x.id === p);
    const el = t && document.getElementById(`task-${t.id}`);
    if (!el) return;
    search.value = '';
    filter();
    el.scrollIntoView({ block: 'center' });
    el.focus({ preventScroll: true });
    const allowed = !t.cap || (mode === 'api' && can[t.cap]);
    if (allowed && !t.soon && t.run) start(t, el.querySelector('.btn'));
  }
  await update(param);
  return { update };
}
