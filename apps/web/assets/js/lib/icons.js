// Satır içi SVG ikonlar (dış kaynak yok). Her ikon 24×24 çizgi çizimidir; renk currentColor'dan gelir.
import { s } from './dom.js';

const PATHS = {
  orbit: ['M12 12m-3 0a3 3 0 1 0 6 0a3 3 0 1 0 -6 0', 'M3.5 9.5c-1.6 3 2.4 7 8.5 8.8s10.6.4 8.5-3', 'M20.5 14.5c1.6-3-2.4-7-8.5-8.8S1.4 5.3 3.5 8.5'],
  home: ['M3 11l9-7 9 7', 'M5 10v10h14V10', 'M10 20v-6h4v6'],
  inbox: ['M3 13l3-8h12l3 8', 'M3 13v6h18v-6', 'M3 13h5l1 3h6l1-3h5'],
  server: ['M4 4h16v6H4z', 'M4 14h16v6H4z', 'M8 7h.01', 'M8 17h.01'],
  shield: ['M12 3l8 4v5c0 5-3.5 8-8 9-4.5-1-8-4-8-9V7z', 'M9 12l2 2 4-4'],
  plug: ['M9 2v6', 'M15 2v6', 'M6 8h12v3a6 6 0 0 1-12 0z', 'M12 17v5'],
  sliders: ['M4 6h10', 'M18 6h2', 'M16 4v4', 'M4 12h4', 'M12 12h8', 'M10 10v4', 'M4 18h12', 'M20 18h0', 'M18 16v4'],
  map: ['M3 6l6-3 6 3 6-3v15l-6 3-6-3-6 3z', 'M9 3v15', 'M15 6v15'],
  layers: ['M12 3l9 5-9 5-9-5z', 'M3 13l9 5 9-5', 'M3 17.5l9 5 9-5'],
  flag: ['M5 21V4', 'M5 4h11l-2 4 2 4H5'],
  search: ['M11 11m-7 0a7 7 0 1 0 14 0a7 7 0 1 0 -14 0', 'M20 20l-3.5-3.5'],
  help: ['M12 12m-9 0a9 9 0 1 0 18 0a9 9 0 1 0 -18 0', 'M9.5 9a2.5 2.5 0 1 1 3.4 2.3c-.6.3-.9.8-.9 1.5V14', 'M12 17v.5'],
  menu: ['M4 6h16', 'M4 12h16', 'M4 18h16'],
  chev: ['M6 9l6 6 6-6'],
  heart: ['M3 12h4l3-7 4 14 3-7h4'],
  key: ['M15 7m-4 0a4 4 0 1 0 8 0a4 4 0 1 0 -8 0', 'M12 10L3 19', 'M6 16l2 2', 'M8 14l2 2'],
  check: ['M12 12m-9 0a9 9 0 1 0 18 0a9 9 0 1 0 -18 0', 'M8 12.5l2.7 2.7L16 10'],
  tree: ['M4 3h6v5H4z', 'M14 10h6v5h-6z', 'M14 17h6v5h-6z', 'M7 8v11.5h7', 'M7 12.5h7'],
};

export function icon(name, cls = 'icon') {
  return s('svg', { viewBox: '0 0 24 24', class: cls, 'aria-hidden': 'true', focusable: 'false' },
    (PATHS[name] || PATHS.help).map((d) => s('path', { d })));
}
