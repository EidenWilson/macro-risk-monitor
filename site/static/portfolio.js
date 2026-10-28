/* Portfolio stress test. Everything runs in the browser: the file is read with FileReader and never sent anywhere.

   Bonds: the yield is solved from the clean price plus accrued interest over the bond's own coupon dates; duration
   and convexity are measured by moving that yield 1 basis point either way. A scenario moves yields (a core shift
   plus a spread for the issuer's country group, plus an add-on for covered and other bonds), breakeven inflation
   (inflation-linked bonds move with real yields), equities and gold. The price change of a bond is
   -D·Δy + ½·C·Δy², split into rates, spreads and inflation by moving one factor at a time, so the parts add up.
   Funds and shares move with an assumed duration or beta, which the file can set per holding. */
(() => {
'use strict';
const P = window.PT;
const t = (k, ...a) => String(P.text[k] ?? k).replace(/\{(\d)\}/g, (m, i) => a[i] ?? '');
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));

/* ---------- numbers in the page language ---------- */
const NF = {};
const nf = o => NF[JSON.stringify(o)] ??= new Intl.NumberFormat(P.locale, o);
const minus = s => s.replace(/-/g, '−');
const eur = (x, d = 0) => minus(nf({style: 'currency', currency: 'EUR', currencyDisplay: 'narrowSymbol', minimumFractionDigits: d, maximumFractionDigits: d}).format(x));
const seur = x => minus(nf({style: 'currency', currency: 'EUR', currencyDisplay: 'narrowSymbol', maximumFractionDigits: 0, signDisplay: 'exceptZero'}).format(Math.round(x) || 0));
const num = (x, d = 0) => minus(nf({minimumFractionDigits: d, maximumFractionDigits: d}).format(x));
const snum = (x, d = 0) => minus(nf({minimumFractionDigits: d, maximumFractionDigits: d, signDisplay: 'exceptZero'}).format(x));
const pct = (x, d = 1) => minus(nf({style: 'percent', minimumFractionDigits: d, maximumFractionDigits: d}).format(x));
const spct = (x, d = 1) => minus(nf({style: 'percent', minimumFractionDigits: d, maximumFractionDigits: d, signDisplay: 'exceptZero'}).format(x));
const dateStr = d => d.toLocaleDateString(P.locale, {day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC'});

/* ---------- reading the file ---------- */
function parseCSV(text) {
  if (text.charCodeAt(0) === 0xFEFF) text = text.slice(1);
  const first = text.split(/\r?\n/, 1)[0], count = ch => first.split(ch).length - 1;
  const delim = count(';') > count(',') ? ';' : count('\t') > count(',') ? '\t' : ',';
  const rows = []; let row = [], cell = '', q = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (q) { if (c === '"') { if (text[i + 1] === '"') { cell += '"'; i++; } else q = false; } else cell += c; }
    else if (c === '"') q = true;
    else if (c === delim) { row.push(cell); cell = ''; }
    else if (c === '\n' || c === '\r') { if (c === '\r' && text[i + 1] === '\n') i++; row.push(cell); rows.push(row); row = []; cell = ''; }
    else cell += c;
  }
  if (cell !== '' || row.length) { row.push(cell); rows.push(row); }
  return {rows: rows.filter(r => r.some(x => x.trim() !== '')), delim};
}
const ALIASES = {
  name: ['name', 'gattungsbezeichnung', 'bezeichnung', 'security', 'description', 'wertpapier', 'titel', 'instrument'],
  isin: ['isin'],
  type: ['type', 'asset_class', 'asset class', 'typ', 'art', 'anlageklasse'],
  unit: ['quantity_unit', 'unit', 'einheit', 'stück bzw. anteile bzw. whg. in 1.000', 'stück bzw. anteile bzw. whg. in 1000'],
  holding: ['holding', 'bestand', 'nominal', 'quantity', 'menge', 'stückzahl', 'bestand 31.03.2026'],
  pcur: ['price_currency', 'währung', 'waehrung', 'kurswährung', 'price currency'],
  price: ['price', 'kurs', 'clean price', 'preis'],
  mv: ['market_value_eur', 'market value', 'market_value', 'kurswert in eur', 'kurswert', 'marktwert', 'value', 'wert'],
  maturity: ['maturity', 'fälligkeit', 'faelligkeit', 'maturity date', 'maturity_date'],
  coupon: ['coupon', 'kupon', 'zinssatz', 'zins'],
  duration: ['duration', 'mod. duration', 'modified duration', 'modified_duration'],
  beta: ['beta'],
  country: ['country', 'land'],
  date: ['report_date', 'stichtag', 'date', 'datum', 'as_of', 'as of'],
};
const fieldOf = h => { const k = h.trim().toLowerCase().replace(/\s+/g, ' '); return Object.keys(ALIASES).find(f => ALIASES[f].includes(k)) || null; };
function toNum(s, delim) {
  if (s == null) return null;
  s = String(s).replace(/[€%\s]/g, '').replace(/[−–]/g, '-');
  if (!s || s === '-') return null;
  const lc = s.lastIndexOf(','), ld = s.lastIndexOf('.');
  if (lc > -1 && ld > -1) s = lc > ld ? s.replace(/\./g, '').replace(',', '.') : s.replace(/,/g, '');
  else if (lc > -1) s = delim !== ';' && /^-?\d{1,3}(,\d{3})+$/.test(s) ? s.replace(/,/g, '') : s.replace(',', '.');
  else if (ld > -1 && delim === ';' && /^-?\d{1,3}(\.\d{3})+$/.test(s)) s = s.replace(/\./g, '');
  const v = Number(s);
  return Number.isFinite(v) ? v : null;
}
const utc = (y, m, d) => new Date(Date.UTC(y, m, d));
function parseDate(s) {
  s = String(s ?? '').trim(); let m;
  if ((m = s.match(/^(\d{4})-(\d{1,2})-(\d{1,2})$/))) return {d: utc(+m[1], +m[2] - 1, +m[3]), exact: true};
  if ((m = s.match(/^(\d{1,2})[./](\d{1,2})[./](\d{4})$/))) return {d: utc(+m[3], +m[2] - 1, +m[1]), exact: true};
  if ((m = s.match(/^(\d{4})$/))) return {d: utc(+m[1], 5, 30), exact: false};
  return null;
}
function maturityFromName(n) {
  let m = n.match(/(\d{4})\s*\((\d{2})\)/);                     // 2025(30), 1997(29): issued, then the maturity year
  if (m) { const iy = +m[1]; let y = Math.floor(iy / 100) * 100 + +m[2]; if (y < iy) y += 100; return {d: utc(y, 5, 30), exact: false}; }
  m = n.match(/\((\d{4})\)/);                                    // Anl.v.2021 (2052)
  return m ? {d: utc(+m[1], 5, 30), exact: false} : null;
}
const couponFromName = n => { const m = n.match(/^\s*(\d+(?:[.,]\d+)?)\s*%/); return m ? +m[1].replace(',', '.') : null; };

/* ---------- what each holding is ---------- */
const RX = {
  infl: /infl|index[- .]?l(?:in)?k|\boat ?€?i\b|oatei|linker|inflation/i,
  covered: /cov(?:ered)?\.? ?b|cov\.bds|mortg|obl\.? ?fonc|pfandbrief|hyp\.? ?pfandbr|covered/i,
  sov: /bundesrep|deutschland|republi[ck]|k(?:ö|oe)nigreich|kingdom|frankreich|france|italien|italy|spanien|spain|portugal|niederlande|netherlands|belgien|belgium|(?:ö|oe)sterreich|austria|slowakei|slovak|slowenien|slovenia|finnland|finland|irland|ireland|griechenland|greece|luxemburg|luxembourg|lettland|latvia|litauen|lithuania|estland|estonia|kroatien|croatia|zypern|cyprus|b\.?t\.?p\b|\boat\b|obligaciones|bonos del estado|european union|\besm\b|\befsf\b|\bkfw\b/i,
  gold: /\bgold\b|\bxau\b/i,
  etc: /\betc\b|\betp\b|silver|silber|commodit|rohstoff/i,
  fund: /fonds|fund|\betf\b|sicav|ucits|act\.port|inhaber-anteile|anteile/i,
  bondFund: /bond|renten|anleihe|fixed income|reserve|geldmarkt|money market|treasury/i,
  equityFund: /equity|aktien|stock|shares|infrastructure|dividend/i,
};
const TYPES = [[/infl|linker|ilb/, 'ilb'], [/cover|pfand/, 'covered'], [/gov|sovereign|staat|öffentl/, 'gov'], [/corp|unternehm|credit/, 'corp'],
  [/gold/, 'gold'], [/commod|rohstoff/, 'commodity'], [/bond ?fund|rentenfonds/, 'bondfund'], [/equity ?fund|aktienfonds/, 'equityfund'],
  [/fund|fonds/, 'fund'], [/equit|aktie|stock|share/, 'equity'], [/cash|liquid|geld|konto/, 'cash'], [/bond|anleihe/, 'gov']];
function kindOf(r) {
  const ty = (r.type || '').toLowerCase().trim();
  if (ty) { const hit = TYPES.find(([re]) => re.test(ty)); if (hit) return hit[1]; }
  const n = r.name || '', unit = (r.unit || '').toLowerCase(), pcur = (r.pcur || '').trim();
  if (pcur === '%' || /'000|1\.000|1000|nominal/.test(unit)) return RX.infl.test(n) ? 'ilb' : RX.covered.test(n) ? 'covered' : RX.sov.test(n) ? 'gov' : 'corp';
  if (RX.gold.test(n)) return 'gold';
  if (RX.etc.test(n)) return 'commodity';
  if (/ant|unit|anteil/.test(unit) || RX.fund.test(n)) return RX.equityFund.test(n) ? 'equityfund' : RX.bondFund.test(n) ? 'bondfund' : 'fund';
  if (/stk|piece|share|stück/.test(unit)) return 'equity';
  return 'other';
}
const BONDS = ['gov', 'covered', 'corp', 'ilb'];
const GROUP = {DE: 'core', NL: 'near', AT: 'near', FI: 'near', LU: 'near', FR: 'semi', BE: 'semi', SK: 'semi', SI: 'semi', LT: 'semi', LV: 'semi',
  EE: 'semi', HR: 'semi', CY: 'semi', MT: 'semi', IT: 'periph', ES: 'periph', PT: 'periph', GR: 'periph', IE: 'periph'};

/* ---------- bond arithmetic ---------- */
const DAY = 864e5;
function addMonths(d, m) {
  const y = d.getUTCFullYear(), mo = d.getUTCMonth() + m, day = d.getUTCDate();
  const last = new Date(Date.UTC(y, mo + 1, 0)).getUTCDate();
  return new Date(Date.UTC(y, mo, Math.min(day, last)));
}
function cashflows(cpn, mat, settle, f) {
  const step = 12 / f, dates = []; let k = 0, d = mat;
  while (d > settle && k < 1000) { dates.unshift(d); k++; d = addMonths(mat, -step * k); }
  if (!dates.length) return null;
  const c = cpn / f, prev = d, next = dates[0];
  return {accr: c * (settle - prev) / (next - prev), cfs: dates.map((x, i) => ({t: (x - settle) / DAY / 365.25, cf: c + (i === dates.length - 1 ? 100 : 0)}))};
}
const pv = (y, cfs, f) => cfs.reduce((s, x) => s + x.cf / Math.pow(1 + y / f, f * x.t), 0);
function bondMath(cpn, mat, settle, clean, f) {
  const b = cashflows(cpn, mat, settle, f);
  if (!b) return null;
  const dirty = clean + b.accr;
  let lo = -0.1, hi = 1;
  for (let i = 0; i < 200; i++) { const mid = (lo + hi) / 2; if (pv(mid, b.cfs, f) > dirty) lo = mid; else hi = mid; }
  const y = (lo + hi) / 2, h = 1e-4, p0 = pv(y, b.cfs, f), pu = pv(y + h, b.cfs, f), pd = pv(y - h, b.cfs, f);
  return {y, dur: (pd - pu) / (2 * p0 * h), conv: (pu + pd - 2 * p0) / (p0 * h * h), years: b.cfs[b.cfs.length - 1].t};
}

/* ---------- from rows to holdings ---------- */
function load(text) {
  const {rows, delim} = parseCSV(text);
  if (rows.length < 2) throw new Error(t('errEmpty'));
  const fields = rows[0].map(fieldOf);
  if (!fields.includes('name') && !fields.includes('isin')) throw new Error(t('errCols'));
  if (!fields.includes('mv') && !(fields.includes('holding') && fields.includes('price'))) throw new Error(t('errCols'));
  const recs = rows.slice(1).map(r => { const o = {}; fields.forEach((f, i) => { if (f && o[f] === undefined) o[f] = (r[i] ?? '').trim(); }); return o; });
  const settle = (recs.map(r => parseDate(r.date)).find(Boolean) || {d: new Date(Date.UTC(new Date().getFullYear(), new Date().getMonth(), new Date().getDate()))}).d;
  const out = [];
  for (const r of recs) {
    if (!r.name && !r.isin) continue;
    const h = {name: r.name || r.isin, isin: (r.isin || '').toUpperCase(), flags: []};
    h.kind = kindOf(r);
    const cc = (r.country || '').toUpperCase().slice(0, 2) || (/^[A-Z]{2}[A-Z0-9]{9}\d$/.test(h.isin) && !/^(XS|EU|QS)/.test(h.isin) ? h.isin.slice(0, 2) : '');
    h.country = cc; h.group = cc === 'DE' ? 'core' : GROUP[cc] || 'semi';
    const hold = toNum(r.holding, delim), price = toNum(r.price, delim), unit = (r.unit || '').toLowerCase();
    const nominal = BONDS.includes(h.kind) && hold != null ? hold * (/'000|1\.000|1000|tsd|thousand/.test(unit) || unit === 'eur' ? 1000 : 1) : null;   // reports give bonds in thousands of euros
    h.mv = toNum(r.mv, delim);
    if (h.mv == null && hold != null && price != null) h.mv = BONDS.includes(h.kind) ? nominal * price / 100 : hold * price;
    if (h.mv == null) continue;
    if (BONDS.includes(h.kind)) {
      const cpn = toNum(r.coupon, delim) ?? couponFromName(h.name);
      let mat = parseDate(r.maturity) || maturityFromName(h.name);
      const clean = price != null && ((r.pcur || '').trim() === '%' || price < 400) ? price : nominal ? h.mv / nominal * 100 : null;   // clean price in % of nominal
      const f = /^IT/.test(h.isin) && /b\.?t\.?p/i.test(h.name) ? 2 : 1;
      if (mat && !mat.exact) h.flags.push(t('flagMat'));
      if (cpn == null || !mat || clean == null) { h.flags.push(t('flagNoBond')); h.noMath = true; }
      else {
        const a = bondMath(cpn, mat.d, settle, clean, f);
        if (!a) { h.flags.push(t('flagMatured')); h.noMath = true; }
        else Object.assign(h, a, {cpn, mat: mat.d, f});
      }
    } else if (h.kind === 'bondfund') {
      h.dur = toNum(r.duration, delim);
      if (h.dur == null) { h.dur = /reserve|kurz|short|geldmarkt|money|liquid/i.test(h.name) ? 1 : 4; h.flags.push(t('flagDur', num(h.dur))); }
      h.conv = 0;
    } else if (h.kind === 'equityfund' || h.kind === 'equity') {
      h.beta = toNum(r.beta, delim) ?? 1;
    } else if (h.kind === 'fund' || h.kind === 'commodity' || h.kind === 'other') h.flags.push(t('flagNoShock'));
    out.push(h);
  }
  if (!out.length) throw new Error(t('errEmpty'));
  return {holdings: out, settle};
}

/* ---------- scenarios: yields and spreads in basis points, equities and gold in percent ---------- */
const SCEN = {
  up:     {core: 100, near: 0, semi: 0, periph: 0, covered: 0, be: 0, eq: 0, gold: 0},
  down:   {core: -100, near: 0, semi: 0, periph: 0, covered: 0, be: 0, eq: 0, gold: 0},
  infl:   {core: 250, near: 10, semi: 25, periph: 80, covered: 30, be: 100, eq: -15, gold: 5},
  crisis: {core: -50, near: 40, semi: 125, periph: 300, covered: 75, be: 0, eq: -25, gold: 20},
  crash:  {core: -75, near: 10, semi: 25, periph: 100, covered: 25, be: -25, eq: -30, gold: 10},
  custom: {core: 150, near: 0, semi: 50, periph: 150, covered: 25, be: 0, eq: -10, gold: 0},
};
const FACTORS = ['core', 'near', 'semi', 'periph', 'covered', 'be', 'eq', 'gold'];
const RANGE = {core: [-300, 300, 25], near: [0, 200, 10], semi: [0, 300, 10], periph: [0, 600, 25], covered: [0, 200, 10], be: [-200, 200, 25], eq: [-60, 40, 5], gold: [-40, 40, 5]};
const move = (h, dy) => -h.dur * dy + 0.5 * (h.conv || 0) * dy * dy;     // relative price change for a yield move dy
function impact(h, s) {
  const r = {rates: 0, spreads: 0, infl: 0, eq: 0, gold: 0};
  const core = s.core / 1e4, grp = h.group === 'core' ? 0 : s[h.group] / 1e4;
  if (BONDS.includes(h.kind) && !h.noMath) {
    const extra = h.kind === 'covered' ? s.covered / 1e4 : h.kind === 'corp' ? 2 * s.covered / 1e4 : 0;
    r.rates = move(h, core);
    r.spreads = move(h, core + grp + extra) - r.rates;
    if (h.kind === 'ilb') r.infl = move(h, core + grp + extra - s.be / 1e4) - move(h, core + grp + extra);
  } else if (h.kind === 'bondfund') {
    r.rates = move(h, core); r.spreads = move(h, core + s.semi / 1e4) - r.rates;
  } else if (h.kind === 'equityfund' || h.kind === 'equity') r.eq = h.beta * s.eq / 100;
  else if (h.kind === 'gold') r.gold = s.gold / 100;
  for (const k in r) r[k] *= h.mv;
  r.total = r.rates + r.spreads + r.infl + r.eq + r.gold;
  return r;
}

/* ---------- drawing ---------- */
let PF = null, cur = 'up';
const KINDS = ['gov', 'covered', 'corp', 'ilb', 'bondfund', 'equityfund', 'fund', 'equity', 'gold', 'commodity', 'cash', 'other'];
const sum = (a, f) => a.reduce((s, x) => s + f(x), 0);
const rankList = (items, total, over) => items.map(([label, v]) => `<li><span>${esc(label)}</span><i style="width:${Math.max(2, v / items[0][1] * 100)}%"${over && over(label) ? ' class="over"' : ''}></i><b>${pct(v / total)}</b></li>`).join('');

function renderSummary() {
  const H = PF.holdings, total = sum(H, h => h.mv);
  const bonds = H.filter(h => BONDS.includes(h.kind) && !h.noMath), bmv = sum(bonds, h => h.mv);
  const nominal = bonds.filter(h => h.kind !== 'ilb'), nmv = sum(nominal, h => h.mv);
  const durable = H.filter(h => h.dur != null && (BONDS.includes(h.kind) || h.kind === 'bondfund')), dmv = sum(durable, h => h.mv);
  const dv01 = -sum(durable, h => h.dur * h.mv) * 1e-4;
  $('figs').innerHTML =
    `<div><div class="k">${t('figValue')}</div><div class="v">${eur(total)}</div><span class="s">${t('figValueSub', num(H.length), dateStr(PF.settle))}</span></div>` +
    `<div><div class="k">${t('figYield')}</div><div class="v">${nmv ? pct(sum(nominal, h => h.y * h.mv) / nmv, 2) : '–'}</div><span class="s">${t('figYieldSub')}</span></div>` +
    `<div><div class="k">${t('figDur')}</div><div class="v">${dmv ? num(sum(durable, h => h.dur * h.mv) / dmv, 1) : '–'}</div><span class="s">${t('figDurSub')}</span></div>` +
    `<div><div class="k">${t('figDv01')}</div><div class="v">${seur(dv01)}</div><span class="s">${t('figDv01Sub')}</span></div>`;
  const mix = KINDS.map(k => [k, sum(H.filter(h => h.kind === k), h => h.mv)]).filter(([, v]) => v > 0);
  $('mix').innerHTML = `<thead><tr><td></td><th scope="col">€</th><th scope="col">%</th></tr></thead><tbody>` +
    mix.map(([k, v]) => `<tr><th scope="row">${t('k_' + k)}</th><td>${num(v)}</td><td>${num(v / total * 100, 1)}</td></tr>`).join('') +
    `<tr class="sum"><th scope="row">${t('mixTotal')}</th><td>${num(total)}</td><td>${num(100, 1)}</td></tr></tbody>`;
  const all = H.filter(h => BONDS.includes(h.kind)), amv = sum(all, h => h.mv), byC = {};
  all.forEach(h => { const c = h.country || '–'; byC[c] = (byC[c] || 0) + h.mv; });
  const cn = new Intl.DisplayNames([P.locale], {type: 'region'});
  const ctry = Object.entries(byC).sort((a, b) => b[1] - a[1]).map(([c, v]) => [c === '–' ? t('noCountry') : cn.of(c), v]);
  $('countries').innerHTML = amv ? rankList(ctry, amv) : `<li>${t('noBonds')}</li>`;
  const B = [[t('mat1'), 0, 3], [t('mat2'), 3, 7], [t('mat3'), 7, 15], [t('mat4'), 15, 1e9]];
  const mats = B.map(([l, a, b]) => [l, sum(bonds.filter(h => h.years >= a && h.years < b), h => h.mv)]);
  const mmax = Math.max(...mats.map(m => m[1]));
  $('mats').innerHTML = bmv ? mats.map(([l, v]) => `<li><span>${esc(l)}</span><i style="width:${mmax ? Math.max(v ? 2 : 0, v / mmax * 100) : 0}%"></i><b>${pct(v / bmv)}</b></li>`).join('') : `<li>${t('noBonds')}</li>`;
}

function renderTabs() {
  $('scenTabs').innerHTML = Object.keys(SCEN).map(k => `<button type="button" class="tab" data-s="${k}" aria-pressed="${k === cur}">${t('s_' + k)}</button>`).join('');
}
const fmtShock = (k, v) => k === 'eq' || k === 'gold' ? spct(v / 100, 0) : `${snum(v)} ${t('bp')}`;
function renderShock() {
  const s = SCEN[cur];
  $('scenDesc').textContent = t('d_' + cur);
  if (cur === 'custom') {
    $('shock').innerHTML = `<div class="sliders">${FACTORS.map(k => { const [a, b, st] = RANGE[k];
      return `<label class="slider"><span>${t('f_' + k)}</span><input type="range" min="${a}" max="${b}" step="${st}" value="${s[k]}" data-f="${k}" aria-describedby="o_${k}"><output id="o_${k}">${fmtShock(k, s[k])}</output></label>`; }).join('')}</div>`;
  } else {
    $('shock').innerHTML = `<table class="tbl"><caption class="sr">${t('shockCap')}</caption><tbody>${FACTORS.map(k =>
      `<tr><th scope="row">${t('f_' + k)}</th><td class="n">${s[k] ? fmtShock(k, s[k]) : '0'}</td></tr>`).join('')}</tbody></table>`;
  }
}
function renderImpact() {
  const H = PF.holdings, s = SCEN[cur], total = sum(H, h => h.mv);
  H.forEach(h => { h.imp = impact(h, s); });
  const parts = ['rates', 'spreads', 'infl', 'eq', 'gold'].map(k => [k, sum(H, h => h.imp[k])]);
  const change = sum(parts, p => p[1]);
  const row = (label, v, cls) => `<tr${cls ? ` class="${cls}"` : ''}><th scope="row">${label}</th><td>${cls === 'sum' || cls === 'start' ? num(v) : snum(Math.round(v))}</td><td>${cls === 'sum' || cls === 'start' ? num(v / total * 100, 1) : snum(v / total * 100, 1)}</td></tr>`;
  $('impact').innerHTML = `<thead><tr><td></td><th scope="col">€</th><th scope="col">%</th></tr></thead><tbody>` + row(t('valToday'), total, 'start') +
    parts.map(([k, v]) => row(t('p_' + k), v, v < -0.5 ? 'neg' : '')).join('') + row(t('valAfter'), total + change, 'sum') + `</tbody>`;
  $('impactLine').innerHTML = change < 0 ? t('lineLoss', `<span class="hl nw">${eur(-change)}</span>`, pct(-change / total)) : t('lineGain', `<span class="nw">${eur(change)}</span>`, pct(change / total));
  const worst = [...H].filter(h => h.imp.total < -0.5).sort((a, b) => a.imp.total - b.imp.total).slice(0, 5);
  $('losers').innerHTML = worst.length ? worst.map(h => `<li><span>${esc(h.name)}</span><b class="neg">${seur(h.imp.total)}</b><span class="small">${spct(h.imp.total / h.mv)}</span></li>`).join('') : `<li>${t('noLosers')}</li>`;
  renderHoldings();
}
function renderHoldings() {
  const H = [...PF.holdings].sort((a, b) => b.mv - a.mv), total = sum(H, h => h.mv);
  $('holdHead').innerHTML = `<tr><th scope="col">${t('c_name')}</th><th scope="col">${t('c_type')}</th><th scope="col">${t('c_mat')}</th><th scope="col" class="n">${t('c_value')}</th><th scope="col" class="n">${t('c_yield')}</th><th scope="col" class="n">${t('c_dur')}</th><th scope="col" class="n">${t('c_pnl', t('s_' + cur))}</th></tr>`;
  $('holdBody').innerHTML = H.map(h => `<tr><td>${esc(h.name)}<small>${esc([h.isin, h.country].filter(Boolean).join(', '))}</small>${h.flags.map(f => `<small class="flag">${esc(f)}</small>`).join('')}</td>` +
    `<td>${t('k_' + h.kind)}</td><td>${h.mat ? dateStr(h.mat) : '–'}</td><td class="n">${num(h.mv)}</td>` +
    `<td class="n">${h.y != null ? pct(h.y, 2) : '–'}</td><td class="n">${h.dur != null ? num(h.dur, 1) : '–'}</td>` +
    `<td class="n${h.imp.total < -0.5 ? ' neg' : ''}">${snum(Math.round(h.imp.total))}</td></tr>`).join('');
  $('holdFoot').innerHTML = `<tr><td colspan="3">${t('mixTotal')}</td><td class="n">${num(total)}</td><td></td><td></td><td class="n${sum(H, h => h.imp.total) < 0 ? ' neg' : ''}">${snum(Math.round(sum(H, h => h.imp.total)))}</td></tr>`;
}
function renderAll() {
  $('out').hidden = false;
  renderSummary(); renderTabs(); renderShock(); renderImpact();
}

/* ---------- export: every holding with its figures and its result in each scenario ---------- */
function exportCSV() {
  const q = v => { const s = String(v ?? ''); return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
  const ids = Object.keys(SCEN);
  const lines = [['name', 'isin', 'type', 'country', 'maturity', 'market_value_eur', 'yield', 'modified_duration', 'dv01_eur', ...ids.map(k => 'pnl_' + k + '_eur'), 'notes'].join(',')];
  for (const h of PF.holdings) {
    const res = ids.map(k => Math.round(impact(h, SCEN[k]).total));
    lines.push([h.name, h.isin, h.kind, h.country, h.mat ? h.mat.toISOString().slice(0, 10) : '', h.mv.toFixed(2), h.y != null ? (h.y * 100).toFixed(3) : '',
      h.dur != null ? h.dur.toFixed(2) : '', h.dur != null ? (-h.dur * h.mv * 1e-4).toFixed(2) : '', ...res, h.flags.join('; ')].map(q).join(','));
  }
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([lines.join('\n') + '\n'], {type: 'text/csv;charset=utf-8'}));
  a.download = 'portfolio-stress-test.csv'; document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

/* ---------- wiring ---------- */
const status = (msg, err) => { const el = $('status'); el.textContent = msg; el.classList.toggle('err', !!err); };
function use(text, label) {
  try {
    PF = load(text);
    renderAll();
    status(t('loaded', num(PF.holdings.length), label));
    $('sumSec').scrollIntoView({behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'start'});
  } catch (e) { status(e.message || t('errRead'), true); }
}
function readFile(file) {
  if (!file) return;
  if (file.size > 5e6) { status(t('errBig'), true); return; }
  const r = new FileReader();
  r.onload = () => use(String(r.result), file.name);
  r.onerror = () => status(t('errRead'), true);
  r.readAsText(file, 'utf-8');
}
$('file').addEventListener('change', e => readFile(e.target.files[0]));
$('example').addEventListener('click', () => use(P.example, t('exampleName')));
const drop = $('drop');
['dragenter', 'dragover'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.add('over'); }));
['dragleave', 'drop'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.remove('over'); }));
drop.addEventListener('drop', e => readFile(e.dataTransfer.files[0]));
$('scenTabs').addEventListener('click', e => { const b = e.target.closest('[data-s]'); if (!b) return; cur = b.dataset.s; renderTabs(); renderShock(); renderImpact(); });
$('shock').addEventListener('input', e => {
  const k = e.target.dataset.f; if (!k) return;
  SCEN.custom[k] = +e.target.value; $('o_' + k).textContent = fmtShock(k, SCEN.custom[k]); renderImpact();
});
$('exportBtn').addEventListener('click', exportCSV);
window.PT_TEST = {parseCSV, toNum, parseDate, maturityFromName, couponFromName, kindOf, bondMath, load, impact, SCEN};   // for the browser checks
})();
