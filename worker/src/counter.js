// Anonymous usage counts for the website: daily totals per page, event, plan and language. Nothing else is kept:
// no cookies, no IDs, no IP addresses, no figures. The page sends a beacon on a page view and when a feature is
// used; this adds one to that day's total. GET /stats returns the totals, which contain nothing about anyone.

export const PAGES = ['tool', 'advisors', 'portfolio'];
// view: page opened · own: own figures entered · pdf: report downloaded · datev: accounts imported
// client: client saved (Business) · ask: question to the assistant (Business)
export const EVENTS = ['view', 'own', 'pdf', 'datev', 'client', 'ask'];
export const PLANS = ['free', 'pro', 'business', '-'];
export const LANGS = ['en', 'de', 'es', 'fr', 'it', 'pt', 'tr', 'pl', 'uk', 'ru', 'ar', 'hi', 'zh'];
const MAX_BODY = 512;

// a beacon's body -> the key it counts under, or null when anything is off (unknown values are dropped, not stored)
export function hitKey(raw, day) {
  if (typeof raw !== 'string' || raw.length > MAX_BODY) return null;
  let b;
  try { b = JSON.parse(raw); } catch { return null; }
  if (!b || typeof b !== 'object') return null;
  const {page, event, plan = '-', lang} = b;
  if (!PAGES.includes(page) || !EVENTS.includes(event) || !PLANS.includes(plan) || !LANGS.includes(lang)) return null;
  return `d:${day}|${page}|${event}|${plan}|${lang}`;
}

export const today = () => new Date().toISOString().slice(0, 10);

// keys -> totals by day, page, event, plan and language
export function summarize(entries, {days = 90, now = today()} = {}) {
  const from = new Date(Date.parse(now) - (days - 1) * 864e5).toISOString().slice(0, 10);
  const out = {from, to: now, totals: {}, byDay: {}, byPage: {}, byPlan: {}, byLang: {}};
  const add = (o, k, ev, n) => { o[k] = o[k] || {}; o[k][ev] = (o[k][ev] || 0) + n; };
  for (const [key, n] of entries) {
    const [day, page, ev, plan, lang] = key.slice(2).split('|');
    if (day < from || day > now) continue;
    out.totals[ev] = (out.totals[ev] || 0) + n;
    add(out.byDay, day, ev, n); add(out.byPage, page, ev, n); add(out.byPlan, plan, ev, n); add(out.byLang, lang, ev, n);
  }
  return out;
}

// One Durable Object holds all totals. Requests to it run one at a time, so an increment never loses a count.
export class Counter {
  constructor(state) { this.storage = state.storage; }
  async fetch(request) {
    const url = new URL(request.url);
    if (request.method === 'POST' && url.pathname === '/add') {
      const key = await request.text();
      if (!key.startsWith('d:')) return new Response(null, {status: 400});
      await this.storage.put(key, ((await this.storage.get(key)) || 0) + 1);
      return new Response(null, {status: 204});
    }
    if (url.pathname === '/stats') {
      const days = Math.min(366, Math.max(1, Number(url.searchParams.get('days')) || 90));
      const entries = await this.storage.list({prefix: 'd:'});
      return Response.json(summarize(entries, {days}));
    }
    return new Response(null, {status: 404});
  }
}
