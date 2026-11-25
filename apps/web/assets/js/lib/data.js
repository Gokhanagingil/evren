// Ekranların ortak veri erişimi. API (F0-09) gelene kadar kaynak: /content/demo.json (sentetik, evren-core hattından üretilir).
import { h, loadJSON } from './dom.js';

export const demo = () => loadJSON('/content/demo.json');

export const ROLE_TR = {
  Discovery: 'Keşif', Virtualization: 'Sanallaştırma', Network: 'Ağ keşfi', Monitoring: 'İzleme',
  EndpointSecurity: 'Uç nokta güvenliği', ITSM: 'ITSM', ChangeManagement: 'Değişiklik kayıtları',
  Manual: 'Elle giriş', Platform: 'Platform',
};

export const LIFECYCLE = {
  active: ['Etkin', 'good'], shadow: ['Gölge', 'purple'], retiring: ['Devrediliyor', 'warn'], retired: ['Emekli', 'neutral'],
};

export const BREAKER = {
  ok: ['Sağlıklı', 'good'], warn: ['Uyarı', 'warn'], open: ['Karantina', 'bad'],
};

export const CONTROL = {
  pass: ['Başarılı', 'good'], fail: ['Başarısız', 'bad'], inconclusive: ['Kesin değil', 'warn'],
};

export const OUTCOME = {
  linked: 'Bağlı', rule_match: 'Kuralla eşleşti', score_match: 'Skorla eşleşti', new: 'Yeni CI', review: 'İncelemede',
  insufficient: 'Kimlik yetersiz',
};

/** Kaynak rozeti: renk rolü, metin veri kaynağını gösterir (ürün değişse de renk aynı kalır). */
export function srcChip(source, role, title) {
  return h('span', { class: `src r-${role || 'Platform'}`, title: title || `${source} · ${ROLE_TR[role] || role || ''}` }, source);
}

export function roleOf(data, source) {
  return data.sources.find((s) => s.name === source)?.role;
}
