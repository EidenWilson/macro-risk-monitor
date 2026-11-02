// Anonymous usage counts: what a beacon may count, how totals add up, and that /hit and /stats behave.
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {Counter, hitKey, summarize} from '../src/counter.js';
import {handle} from '../src/handler.js';

const SITE = 'https://eidenwilson.github.io';
const SPEC = {prompt: 'x', tools: []};

// the Durable Object with an in-memory storage in place of Cloudflare's
function counterEnv(extra = {}) {
  const data = new Map();
  const storage = {
    get: async k => data.get(k),
    put: async (k, v) => { data.set(k, v); },
    list: async ({prefix}) => new Map([...data].filter(([k]) => k.startsWith(prefix)).sort()),
  };
  const obj = new Counter({storage});
  const COUNTER = {idFromName: name => name, get: () => ({fetch: (url, init) => obj.fetch(new Request(url, init))})};
  return {data, env: {ALLOWED_ORIGINS: SITE, COUNTER, ...extra}};
}
const beacon = (body, {origin = SITE, method = 'POST'} = {}) =>
  new Request('https://mrm-assistant.example.workers.dev/hit', {method, headers: {'content-type': 'text/plain;charset=UTF-8', ...(origin ? {Origin: origin} : {})},
    body: method === 'POST' ? (typeof body === 'string' ? body : JSON.stringify(body)) : undefined});
const view = {page: 'tool', event: 'view', plan: 'free', lang: 'de'};

test('a beacon counts only known pages, events, plans and languages', () => {
  assert.equal(hitKey(JSON.stringify(view), '2026-11-02'), 'd:2026-11-02|tool|view|free|de');
  assert.equal(hitKey(JSON.stringify({page: 'advisors', event: 'view', lang: 'en'}), '2026-11-02'), 'd:2026-11-02|advisors|view|-|en', 'no plan on the pages around the tool');
  for (const bad of [{...view, page: 'admin'}, {...view, event: 'click'}, {...view, plan: 'gold'}, {...view, lang: 'xx'}, {...view, page: undefined}])
    assert.equal(hitKey(JSON.stringify(bad), '2026-11-02'), null, JSON.stringify(bad));
  assert.equal(hitKey('not json', '2026-11-02'), null);
  assert.equal(hitKey(JSON.stringify({...view, pad: 'x'.repeat(600)}), '2026-11-02'), null, 'oversized body');
});

test('totals add up by day, page, event, plan and language, within the asked window', () => {
  const s = summarize(new Map([
    ['d:2026-11-01|tool|view|free|de', 3], ['d:2026-11-02|tool|view|business|en', 2], ['d:2026-11-02|tool|own|free|de', 1],
    ['d:2026-11-02|advisors|view|-|de', 4], ['d:2026-07-01|tool|view|free|de', 9],
  ]), {days: 30, now: '2026-11-02'});
  assert.deepEqual(s.totals, {view: 9, own: 1});
  assert.deepEqual(s.byDay['2026-11-02'], {view: 6, own: 1});
  assert.deepEqual(s.byPage.advisors, {view: 4});
  assert.equal(s.byLang.de.view, 7);
  assert.equal(s.from, '2026-10-04');
  assert.equal(s.byDay['2026-07-01'], undefined, 'older than the window');
});

test('/hit from the site adds one to the day\'s total and answers 204 with nothing in it', async () => {
  const {data, env} = counterEnv();
  for (let i = 0; i < 3; i++) {
    const r = await handle(beacon(view), env, SPEC);
    assert.equal(r.status, 204);
    assert.equal(await r.text(), '');
  }
  const [[key, n]] = [...data];
  assert.match(key, /^d:\d{4}-\d{2}-\d{2}\|tool\|view\|free\|de$/);
  assert.equal(n, 3);
});

test('/hit refuses other sites and other methods, and drops bad or rate-limited beacons without counting', async () => {
  const {data, env} = counterEnv();
  assert.equal((await handle(beacon(view, {origin: 'https://evil.example'}), env, SPEC)).status, 403);
  assert.equal((await handle(beacon(view, {origin: null}), env, SPEC)).status, 403);
  assert.equal((await handle(beacon(null, {method: 'GET'}), env, SPEC)).status, 405);
  assert.equal((await handle(beacon({...view, event: 'hack'}), env, SPEC)).status, 204);
  const limited = counterEnv({RATE_LIMITER: {limit: async ({key}) => ({success: !key.startsWith('hit:')})}});
  assert.equal((await handle(beacon(view), limited.env, SPEC)).status, 204);
  assert.equal(data.size + limited.data.size, 0, 'nothing was counted');
});

test('/stats returns the totals and nothing about anyone', async () => {
  const {env} = counterEnv();
  await handle(beacon(view), env, SPEC);
  await handle(beacon({page: 'tool', event: 'own', plan: 'free', lang: 'de'}), env, SPEC);
  const r = await handle(new Request('https://mrm-assistant.example.workers.dev/stats?days=7'), env, SPEC);
  assert.equal(r.status, 200);
  assert.equal(r.headers.get('cache-control'), 'no-store');
  const s = await r.json();
  assert.deepEqual(s.totals, {view: 1, own: 1});
  assert.deepEqual(Object.keys(s).sort(), ['byDay', 'byLang', 'byPage', 'byPlan', 'from', 'to', 'totals']);
});

test('without the counter configured, /hit is a quiet no-op and /stats says so', async () => {
  const env = {ALLOWED_ORIGINS: SITE};
  assert.equal((await handle(beacon(view), env, SPEC)).status, 204);
  assert.equal((await handle(new Request('https://x.workers.dev/stats'), env, SPEC)).status, 404);
});
