// Yeni entegrasyon sihirbazı (U8.7, ADR-0038). Kaynak türü → bağlantı → zamanlama → geçit kimliği → kurulum paketi →
// doğrulama. Sır değeri hiçbir adımda istenmez: yalnızca kurum sır kasasından çalıştırıcıya bağlanan dosyanın yolu.
// Örnek tanımı saklanmaz (ADR-0021): sihirbaz çalıştırıcının örnek dosyasına eklenecek bloğu üretir; çalıştırıcı örneği
// bildirince deneme çalıştırmasıyla doğrular.
import { h, clear, formatDateTime, formatNumber, pill } from '../lib/dom.js';
import { toast } from '../lib/shell.js';
import { integrationPlan, integrationStatus, integrationsCatalog, issueCredential, requestRun } from '../lib/data.js';
import { radioGroup, wfield, wizard } from '../lib/wizard.js';

const NAME = /^[A-Za-z0-9][A-Za-z0-9_.-]{1,63}$/;
const LIFE_TONE = { active: 'good', shadow: 'warn', retiring: 'warn', retired: 'neutral' };
const RUN_TR = { pending: 'Sırada', running: 'Çalışıyor', succeeded: 'Başarılı', failed: 'Başarısız', cancelled: 'İptal edildi',
  rejected: 'Reddedildi' };

function minuteOf(name) {
  let n = 0;
  for (const ch of name) n = (n * 31 + ch.charCodeAt(0)) % 997;
  return 5 + (n % 50); // zamanlamaları saatin başına yığmamak için örnek adından sabit bir dakika
}

function input(id, attrs = {}) {
  return h('input', { type: 'text', id, autocomplete: 'off', ...attrs });
}

function secretPath(name, key) {
  const root = key.replace(/_file$/, '');
  return `/run/secrets/${name.replace(/[^A-Za-z0-9_.-]/g, '_')}_${root}`;
}

function planBody(s) {
  const b = { name: s.name, connector: s.connector.id, gateway_token_file: s.gateway_token_file,
    schedule_choice: s.schedule_choice === 'preset' ? 'custom' : s.schedule_choice,
    schedule: s.schedule_choice === 'preset' ? s.preset : s.schedule_choice === 'custom' ? s.schedule : null,
    schedule_mode: s.schedule_mode, max_runtime_minutes: Number(s.max_runtime_minutes) || 120 };
  for (const k of ['url', 'ca_file', ...s.connector.auth_fields.map((f) => f.key)]) if (s[k]) b[k] = s[k];
  for (const o of s.connector.options) if (s[o.key]) b[o.key] = o.key === 'api_release' ? s[o.key] : Number(s[o.key]);
  return b;
}

export async function openIntegrationWizard({ resume = null } = {}) {
  const cat = await integrationsCatalog();
  const s = { connector: null, name: '', schedule_choice: 'default', schedule_mode: 'snapshot', max_runtime_minutes: '120',
    preset: '', expires_days: '365', credential: null, plan: null, timer: null };
  const stop = () => { if (s.timer) { clearInterval(s.timer); s.timer = null; } };
  if (resume) {
    const p = cat.pending.find((x) => x.name === resume) || { name: resume };
    s.name = resume;
    s.connector = cat.connectors.find((c) => c.data_source === p.data_source) || null;
  }

  const steps = [
    { id: 'kaynak', title: 'Kaynak türü', render: (st, a) => {
      const pending = cat.pending.length ? h('div', { class: 'notice', id: 'intPending' }, h('strong', {}, 'Yarım kalan kurulumlar. '),
        'Kimliği verilmiş ama çalıştırıcıda henüz görünmeyen örnekler: ',
        ...cat.pending.map((p, i) => [i ? ', ' : '', h('button', { class: 'btn ghost small', type: 'button', dataset: { resume: p.name },
          onclick: () => { st.name = p.name; st.connector = cat.connectors.find((c) => c.data_source === p.data_source) || st.connector; a.go(5); } }, p.name)])) : null;
      return h('div', {},
        h('p', {}, 'Hangi sistemden veri alınacak? Her sistem bir rol doldurur (keşif, izleme, sanallaştırma…). Kurallar role bağlıdır; aynı ürünün ikinci kurulumu (ör. ikinci veri merkezi) aynı veri kaynağına yeni bir örnek olarak eklenir.'),
        pending,
        radioGroup('intConnector', 'Kaynak türü', cat.connectors.map((c) => ({
          value: c.id, label: `${c.label} · ${c.role_label}`, disabled: !c.available,
          sub: `${c.auth_tr} · veri kaynağı ${c.lifecycle_tr.toLowerCase()}${c.instances.length ? ` · mevcut örnekler: ${c.instances.join(', ')}` : ''}`,
        })), st.connector?.id, (v) => { st.connector = cat.connectors.find((c) => c.id === v); st.name = ''; }),
        h('p', { class: 'muted small' }, 'Listede olmayan bir ürün için önce bağlayıcı geliştirilir (eşleme dosyası ve istemci); sonra bu sihirbazla kurulur.'));
    },
    next: (st, a) => {
      if (!st.connector) { a.error('Bir kaynak türü seçin.'); return false; }
      if (!st.name) {
        const base = st.connector.id.replace(/_/g, '-');
        let i = st.connector.instances.length + 1;
        while (st.connector.instances.includes(`${base}-${i}`)) i += 1;
        st.name = `${base}-${i}`;
      }
      return true;
    } },

    { id: 'baglanti', title: 'Bağlantı', render: (st) => {
      const c = st.connector;
      const fields = [];
      const name = input('intName', { value: st.name, maxlength: '64' });
      name.addEventListener('input', () => { st.name = name.value.trim(); });
      fields.push(wfield('Örnek adı', name, 'Geçmişte ve listelerde görünür. Ör. zabbix-dc2. Harf veya rakamla başlar; harf, rakam, nokta, tire, alt çizgi.'));
      const url = input('intUrl', { value: st.url || '', maxlength: '500', placeholder: c.url_example, class: 'mono' });
      url.addEventListener('input', () => { st.url = url.value.trim(); });
      fields.push(wfield(c.url_required ? 'Kaynak adresi' : 'Kaynak adresi (isteğe bağlı)', url,
        c.url_required ? 'https ile başlar.' : 'Boş bırakılırsa hizmetin bilinen adresi kullanılır.'));
      for (const f of c.auth_fields) {
        const el = input(`int-${f.key}`, { value: st[f.key] || (f.secret ? secretPath(st.name, f.key) : ''), maxlength: '500',
          class: f.secret ? 'mono' : '' });
        st[f.key] = el.value;
        el.addEventListener('input', () => { st[f.key] = el.value.trim(); });
        fields.push(wfield(f.secret ? `${f.label} (kasadaki dosya yolu)` : f.label, el,
          f.secret ? 'Sırrın kendisini yazmayın. Kurum sır kasasındaki değer çalıştırıcıya bu yolda dosya olarak bağlanır.' : null));
      }
      const ca = input('intCa', { value: st.ca_file || '', maxlength: '500', placeholder: '/etc/evren/kurum-ca.pem', class: 'mono' });
      ca.addEventListener('input', () => { st.ca_file = ca.value.trim(); });
      fields.push(wfield('Kurum CA sertifikası (isteğe bağlı)', ca, 'Kaynağın sertifikası kurum CA\'sıyla imzalıysa dosya yolu.'));
      for (const o of c.options) {
        const el = input(`int-${o.key}`, { value: st[o.key] || '', maxlength: '20', inputmode: o.key === 'api_release' ? 'text' : 'numeric' });
        el.addEventListener('input', () => { st[o.key] = el.value.trim(); });
        fields.push(wfield(`${o.label} (isteğe bağlı)`, el));
      }
      return h('div', {}, h('p', { class: 'notice', role: 'note' }, h('strong', {}, 'Sır bu ekrana yazılmaz. '),
        'Parola, belirteç veya istemci sırrı kurum sır kasasında durur; burada yalnızca çalıştırıcıda bağlanacağı dosyanın yolunu verirsiniz.'),
      h('div', { class: 'wiz-grid' }, fields));
    },
    next: (st, a) => {
      if (!NAME.test(st.name || '')) { a.error('Örnek adı geçersiz: harf veya rakamla başlar, 2–64 karakter (harf, rakam, . - _).'); document.getElementById('intName')?.focus(); return false; }
      if (st.connector.url_required && !(st.url || '').toLowerCase().startsWith('https://')) {
        a.error('Kaynak adresi https:// ile başlamalı.'); document.getElementById('intUrl')?.focus(); return false;
      }
      for (const f of st.connector.auth_fields) {
        if (!st[f.key]) { a.error(`${f.label} gerekli.`); document.getElementById(`int-${f.key}`)?.focus(); return false; }
        if (f.secret && !st[f.key].startsWith('/')) { a.error(`${f.label}: mutlak dosya yolu olmalı (/ ile başlar).`); document.getElementById(`int-${f.key}`)?.focus(); return false; }
      }
      return true;
    } },

    { id: 'zamanlama', title: 'Zamanlama', render: (st) => {
      const m = minuteOf(st.name);
      const presets = [[`${m} * * * *`, 'Her saat'], [`${m} */2 * * *`, 'İki saatte bir'], [`${m} 2 * * *`, 'Her gece (02:' + String(m).padStart(2, '0') + ')']];
      const def = st.connector.default_schedule;
      const cron = input('intCron', { value: st.schedule || '', maxlength: '100', class: 'mono', placeholder: 'dakika saat gün ay haftanın-günü', hidden: st.schedule_choice !== 'custom' });
      cron.addEventListener('input', () => { st.schedule = cron.value.trim(); });
      const choice = st.schedule_choice === 'preset' ? `p:${st.preset}` : st.schedule_choice;
      const runtime = h('input', { type: 'number', id: 'intRuntime', min: '1', max: '1440', value: st.max_runtime_minutes });
      runtime.addEventListener('input', () => { st.max_runtime_minutes = runtime.value; });
      return h('div', {},
        h('p', {}, 'Çalıştırıcı bu zamanlamayla tam liste çeker. Kaçırılan zamanlama telafi edilmez; bir sonraki çalışma yeterlidir.'),
        radioGroup('intSchedule', 'Ne sıklıkla?', [
          ...(def ? [{ value: 'default', label: 'Kaynağın varsayılanı', sub: def }] : []),
          ...presets.map(([expr, label]) => ({ value: `p:${expr}`, label, sub: expr })),
          { value: 'custom', label: 'Özel zamanlama', sub: '5 alanlı cron, İstanbul saati' },
          { value: 'manual', label: 'Yalnızca elle', sub: 'Veri Kaynakları sayfasından "Şimdi çalıştır"' },
        ], def || st.schedule_choice !== 'default' ? choice : `p:${presets[1][0]}`, (v) => {
          if (v.startsWith('p:')) { st.schedule_choice = 'preset'; st.preset = v.slice(2); } else st.schedule_choice = v;
          cron.hidden = st.schedule_choice !== 'custom';
          if (!cron.hidden) cron.focus();
        }),
        cron,
        h('label', { class: 'field' }, 'Çalışma türü'),
        radioGroup('intMode', 'Çalışma türü', [
          { value: 'snapshot', label: 'Tam liste', sub: 'Her çalışmada bütün kayıtlar; eskime ve "kaynakta yok" tespiti için gerekli.' },
          { value: 'delta', label: 'Yalnızca değişenler', sub: 'Son başarılı çalışmadan bu yana değişenler; ayrıca günlük tam liste önerilir.' },
        ], st.schedule_mode, (v) => { st.schedule_mode = v; }),
        wfield('En uzun çalışma süresi (dakika)', runtime, 'Aşılırsa çalışma durdurulur ve "başarısız" sayılır.'));
    },
    onLeave: (st) => { if (st.schedule_choice === 'default' && !st.connector.default_schedule) { st.schedule_choice = 'preset'; st.preset = `${minuteOf(st.name)} */2 * * *`; } },
    next: (st, a) => {
      if (st.schedule_choice === 'default' && !st.connector.default_schedule) { st.schedule_choice = 'preset'; st.preset = `${minuteOf(st.name)} */2 * * *`; }
      if (st.schedule_choice === 'custom' && !st.schedule) { a.error('Özel zamanlama ifadesini yazın.'); document.getElementById('intCron')?.focus(); return false; }
      const n = Number(st.max_runtime_minutes);
      if (!Number.isInteger(n) || n < 1 || n > 1440) { a.error('En uzun çalışma süresi 1–1440 dakika olmalı.'); return false; }
      st.gateway_token_file = st.gateway_token_file || `/run/secrets/evren_gateway_${st.name}`;
      return true;
    } },

    { id: 'kimlik', title: 'Geçit kimliği', render: async (st, a) => {
      const box = h('div', { id: 'intCredBox' });
      const path = input('intGatewayPath', { value: st.gateway_token_file, maxlength: '500', class: 'mono' });
      path.addEventListener('input', () => { st.gateway_token_file = path.value.trim(); });
      let existing = null;
      try { existing = (await integrationStatus(st.name)).steps.credential; } catch { existing = null; }
      const paint = () => {
        clear(box);
        if (st.credential?.token) {
          const token = h('input', { type: 'text', id: 'intToken', readonly: true, value: st.credential.token, class: 'mono', 'data-no-enter': '1' });
          box.append(h('p', { class: 'notice warn', role: 'status' }, h('strong', {}, 'Belirteç yalnızca şimdi görünür. '),
            `Kurum sır kasasına koyun; çalıştırıcıda ${st.gateway_token_file} yoluna bağlanacak. Günlüğe, bilete veya sohbete yazmayın.`),
          h('label', { class: 'field', for: 'intToken' }, 'Belirteç'), token,
          h('div', { class: 'link-actions' }, h('button', { class: 'btn secondary small', type: 'button', id: 'intTokenCopy', onclick: async () => {
            try { await navigator.clipboard.writeText(token.value); toast('Belirteç panoya kopyalandı.'); } catch { token.select(); toast('Kopyalanamadı; seçili metni kopyalayın.'); }
          } }, 'Kopyala')));
          return;
        }
        if (existing && existing.state === 'active') {
          box.append(h('p', { class: 'notice', id: 'intCredExists' }, `Bu ad için kimlik ${formatDateTime(existing.issued_at)} tarihinde verilmiş (…${existing.hint}). Belirteç bir daha gösterilmez; kaybedildiyse Veri Kaynakları sayfasından iptal edip yeniden verin.`));
          return;
        }
        const days = h('input', { type: 'number', id: 'intDays', min: '1', max: '730', value: st.expires_days });
        days.addEventListener('input', () => { st.expires_days = days.value; });
        const issue = h('button', { class: 'btn', type: 'button', id: 'intIssue' }, 'Kimlik ver');
        issue.addEventListener('click', async () => {
          issue.disabled = true;
          try {
            const res = await issueCredential(st.connector.data_source, { name: st.name, expiresDays: Number(days.value) || 365 });
            st.credential = { token: res.token, hint: res.token_hint, expires_at: res.expires_at };
            paint();
            document.getElementById('intToken')?.focus();
          } catch (err) {
            issue.disabled = false;
            a.error(err.message);
          }
        });
        box.append(wfield('Geçerlilik (gün)', days, 'En çok 730 gün. Süre dolmadan yenisi verilip eskisi iptal edilir.'), h('div', { class: 'link-actions' }, issue));
      };
      paint();
      return h('div', {},
        h('p', {}, 'Bağlayıcı topladığı veriyi Evren alım geçidine bu kimlikle gönderir. Kimlik veri kaynağına bağlıdır; başka kaynağın verisini gönderemez. Evren yalnızca belirtecin özetini saklar.'),
        wfield('Belirtecin çalıştırıcıdaki dosya yolu', path), box);
    },
    onLeave: (st) => { if (st.credential) st.credential.token = null; }, // belirteç pencereden silinir
    next: async (st, a) => {
      if (!(st.gateway_token_file || '').startsWith('/')) { a.error('Belirtecin dosya yolu / ile başlamalı.'); return false; }
      const cred = st.credential || (await integrationStatus(st.name).catch(() => null))?.steps.credential;
      if (!cred || (cred.state && cred.state !== 'active')) { a.error('Önce "Kimlik ver" deyin; kimlik olmadan bağlayıcı veri gönderemez.'); return false; }
      try {
        st.plan = await integrationPlan(planBody(st));
      } catch (err) { a.error(err.message); return false; }
      return true;
    } },

    { id: 'kurulum', title: 'Kurulum paketi', render: (st) => {
      const p = st.plan;
      const code = h('pre', { class: 'wiz-code', id: 'intYaml', tabindex: '0', 'aria-label': 'Çalıştırıcı örnek dosyasına eklenecek blok' }, p.yaml);
      const copy = h('button', { class: 'btn secondary small', type: 'button', id: 'intYamlCopy', onclick: async () => {
        try { await navigator.clipboard.writeText(p.yaml); toast('Blok panoya kopyalandı.'); } catch { toast('Kopyalanamadı; metni seçip kopyalayın.'); }
      } }, 'Kopyala');
      const download = h('button', { class: 'btn secondary small', type: 'button', id: 'intYamlDownload', onclick: () => {
        const url = URL.createObjectURL(new Blob([`connectors:\n${p.yaml}`], { type: 'application/yaml' }));
        const a = h('a', { href: url, download: `${p.name}.yaml` });
        document.body.append(a);
        a.click();
        a.remove();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
      } }, 'İndir (.yaml)');
      return h('div', {},
        ...p.warnings.map((w) => h('p', { class: 'notice warn' }, w)),
        h('ol', { class: 'small' },
          h('li', {}, 'Aşağıdaki sırları kurum sır kasasına koyun ve çalıştırıcıya belirtilen yollarda dosya olarak bağlayın.'),
          h('li', {}, 'Bloğu çalıştırıcının örnek dosyasına (EVREN_CONNECTORS_FILE) "connectors:" altına ekleyin.'),
          h('li', {}, 'Çalıştırıcıyı yeniden başlatın. Örnek birkaç saniye içinde bildirilir; sonraki adım bunu bekler.')),
        h('table', { class: 'mini-table', id: 'intSecrets' }, h('thead', {}, h('tr', {}, h('th', { scope: 'col' }, 'Dosya yolu'), h('th', { scope: 'col' }, 'Ne'))),
          h('tbody', {}, p.secrets.map((x) => h('tr', {}, h('td', { class: 'mono' }, x.path), h('td', {}, x.what))))),
        h('div', { class: 'link-actions' }, copy, download), code,
        h('p', { class: 'muted small' }, p.next_runs.length ? `Sonraki çalışmalar: ${p.next_runs.map(formatDateTime).join(', ')}.` : 'Zamanlama yok: yalnızca elle çalıştırılır.',
          ' Bu tanım Evren\'de saklanmaz; adres ve dosya yolları yalnızca çalıştırıcıda durur.'));
    } },

    { id: 'dogrulama', title: 'Doğrulama', render: (st) => {
      const list = h('ul', { class: 'wiz-check', id: 'intChecks', 'aria-live': 'polite' });
      const done = h('div', { id: 'intDone' });
      const item = (cls, title, sub, action = null) => h('li', { class: cls },
        h('span', { class: 'mark', 'aria-hidden': 'true' }, cls === 'ok' ? '✓' : cls === 'bad' ? '!' : '…'),
        h('div', {}, h('strong', {}, title), h('div', { class: 'cell-sub' }, sub)), action || h('span'));
      async function refresh() {
        let r;
        try { r = await integrationStatus(st.name); } catch (err) {
          clear(list).append(item('bad', 'Durum okunamadı', err.message));
          return;
        }
        const { data_source: ds, credential: cr, instance: ins, run } = r.steps;
        const trial = ins.available && (!run.status || ['succeeded', 'failed', 'cancelled', 'rejected'].includes(run.status))
          ? h('button', { class: 'btn small', type: 'button', id: 'intTrial', onclick: async (ev) => {
            ev.currentTarget.disabled = true;
            try {
              await requestRun(ds.name, { instance: st.name, mode: 'snapshot', note: 'Entegrasyon sihirbazı deneme çalışması' });
              toast('Deneme çalışması istendi.');
              refresh();
            } catch (err) { toast(err.message); ev.currentTarget.disabled = false; }
          } }, run.status ? 'Yeniden dene' : 'Deneme çalışması') : null;
        clear(list).append(
          item(ds.ok ? 'ok' : 'bad', `Veri kaynağı: ${ds.name || st.connector?.data_source || '—'}`, ds.ok ? `${ds.lifecycle_tr}${ds.lifecycle === 'shadow' ? ' — gölge modda değer yazmaz; sonuçlar Gölge Mod Raporu\'nda' : ''}` : 'Kaynak emekli veya tanımsız'),
          item(cr.ok ? 'ok' : 'bad', 'Geçit kimliği', cr.ok ? `Verildi (…${cr.hint}), geçerlilik sonu ${formatDateTime(cr.expires_at)}${cr.last_used_at ? `, son kullanım ${formatDateTime(cr.last_used_at)}` : ''}` : 'Verilmedi veya iptal/süre dolmuş'),
          item(ins.available ? 'ok' : 'wait', 'Çalıştırıcı örneği bildirdi', ins.reported ? (ins.available ? `Son görülme ${formatDateTime(ins.seen_at)}${ins.next_run_at ? ` · sonraki çalışma ${formatDateTime(ins.next_run_at)}` : ''}` : `Son görülme ${formatDateTime(ins.seen_at)}; çalıştırıcı şu an yanıt vermiyor`) : 'Bekleniyor: blok eklenip çalıştırıcı yeniden başlatılınca görünür.'),
          item(run.ok ? 'ok' : run.status && ['failed', 'rejected', 'cancelled'].includes(run.status) ? 'bad' : 'wait', 'Deneme çalışması',
            run.status ? `${RUN_TR[run.status] || run.status}${run.summary?.sent !== undefined ? ` · ${formatNumber(run.summary.sent)} kayıt gönderildi` : ''}${run.note ? ` · ${run.note}` : ''}` : (ins.available ? 'Hazır: "Deneme çalışması" ile başlatın.' : 'Örnek bildirilince başlatılabilir.'),
            trial));
        clear(done).append(r.complete ? h('p', { class: 'notice success', role: 'status' }, h('strong', {}, 'Entegrasyon çalışıyor. '),
          ds.lifecycle === 'shadow' ? 'Kaynak gölge modda: sonuçları Gölge Mod Raporu\'nda inceleyin, sonra etkinleştirmeyi değişiklik setiyle önerin. ' : '',
          h('a', { href: `#/kaynaklar/${ds.name}` }, 'Veri kaynağını aç')) : null);
      }
      refresh();
      stop();
      s.timer = setInterval(refresh, 5000);
      return h('div', {}, h('p', {}, 'Bu adım kendiliğinden yenilenir (5 saniyede bir). Pencereyi kapatıp sonra "Yeni entegrasyon kur" → "Yarım kalan kurulumlar" ile dönebilirsiniz.'), list, done);
    },
    onLeave: () => stop() },
  ];
  return wizard({ title: 'Yeni entegrasyon kur', steps, state: s, start: resume ? 5 : 0, finishLabel: 'Kapat', onClose: stop });
}
