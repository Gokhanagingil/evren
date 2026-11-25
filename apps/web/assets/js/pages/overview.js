// Genel Bakış: platformun amacı ve iki rehber sayfaya giriş.
import { h, loadJSON, formatDate } from '../lib/dom.js';

export async function renderOverview(view) {
  const [rm, arch] = await Promise.all([loadJSON('/content/roadmap.json'), loadJSON('/content/architecture.json')]);
  const items = rm.phases.flatMap((p) => p.items);
  const done = items.filter((i) => i.status === 'done').length;
  const current = rm.phases.find((p) => p.items.some((i) => i.status !== 'done')) || rm.phases.at(-1);

  view.append(
    h('section', { class: 'hero' },
      h('p', { class: 'eyebrow' }, 'Evren CMDB'),
      h('h1', {}, 'Konfigürasyon evreni — kaynaktan karara'),
      h('p', {},
        'vCenter, BMC, NNMi, Zabbix, Defender ve ServiceNow\'dan gelen gözlemler tek bir ara katmanda anlamlandırılır. ',
        'Kimlik çözülür, öznitelikler kurallarla uzlaştırılır ve ServiceNow veri deseniyle uyumlu bir CMDB oluşur. ',
        'Geçiş döneminde ServiceNow CMDB bu kaynaktan beslenen bir replika olarak çalışır.')),
    h('div', { class: 'grid cols-4', id: 'overviewStats' },
      h('div', { class: 'card stat' }, h('b', {}, String(Object.keys(arch.nodes).length)), h('span', {}, 'Mimari bileşen')),
      h('div', { class: 'card stat' }, h('b', {}, String(rm.phases.length)), h('span', {}, 'Faz')),
      h('div', { class: 'card stat' }, h('b', {}, `${done}/${items.length}`), h('span', {}, 'Tamamlanan iş kalemi')),
      h('div', { class: 'card stat' }, h('b', {}, current.id), h('span', {}, `Etkin faz: ${current.title}`))),
    h('div', { class: 'grid cols-2 cards-link' },
      h('a', { class: 'card', href: '#/mimari' },
        h('h2', {}, 'Mimari Harita →'),
        h('p', { class: 'muted' }, 'Altı bölümde kaynaklar, ara katman, kayıt sistemi, entegrasyon, ServiceNow geçişi ve güvenlik. Her bileşen ayrıntı ve yol haritası bağlantısıyla.')),
      h('a', { class: 'card', href: '#/yol-haritasi' },
        h('h2', {}, 'Yol Haritası →'),
        h('p', { class: 'muted' }, `Beş faz, çıkış kriterleri ve iş kalemlerinin durumu. Son güncelleme ${formatDate(rm.updated_at)}.`))),
    h('p', { class: 'notice' },
      'Bu bir geliştirme önizlemesidir. CMDB ekranları (CI listesi, eşleştirme kuyruğu, sağlık panosu) yol haritasındaki sıraya göre eklenecek.'),
  );
}
