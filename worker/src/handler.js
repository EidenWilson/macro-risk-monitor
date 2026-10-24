// Assistant server for Macro Risk Monitor: one model call per request.
//
// The page runs the tools itself (they need the in-browser risk model and the client data, which never
// leave the browser). This server adds the fixed instructions and tool definitions, calls Workers AI,
// and returns either text or tool calls in one shape, whatever format the chosen model answers in.
// It keeps nothing: no logs of questions, no storage. Only model errors are logged, for `wrangler tail`.
//
// Tool calling has two modes. "native" passes the tools to the model; "prompt" lists them in the instructions
// and reads the one-line JSON the model replies with, which works with any chat model. TOOL_MODE "auto" (the
// default) tries native first and, once a model has refused tools but answered without them, uses prompt.

export const LIMITS = {
  bodyChars: 64 * 1024,   // whole request
  messages: 30,           // turns sent by the page
  content: 4000,          // one user or assistant turn
  context: 16 * 1024,     // the page summary (DATA)
  toolArgs: 2 * 1024,
  toolResult: 8 * 1024,
  maxTokens: 900,         // Workers AI defaults to 256, too short for a drafted client note
};

export const DEFAULT_MODEL = '@cf/mistralai/mistral-small-3.1-24b-instruct';

const model = env => (env.MODEL || DEFAULT_MODEL).trim();
const origins = env => String(env.ALLOWED_ORIGINS || '').split(',').map(s => s.trim().replace(/\/$/, '')).filter(Boolean);
const toolMode = env => (env.TOOL_MODE === 'native' || env.TOOL_MODE === 'prompt') ? env.TOOL_MODE : 'auto';
let nativeRefused = false;                                   // per isolate: this model answers without tools but not with them
export const resetToolMemory = () => { nativeRefused = false; };

function json(body, status, headers = {}) {
  return new Response(JSON.stringify(body), {status, headers: {'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store', ...headers}});
}

// compares in time that does not depend on where the strings differ
function sameCode(a, b) {
  a = String(a); b = String(b);
  let diff = a.length ^ b.length;
  for (let i = 0; i < Math.max(a.length, b.length); i++) diff |= (a.charCodeAt(i) || 0) ^ (b.charCodeAt(i) || 0);
  return diff === 0;
}

const size = x => { try { return JSON.stringify(x).length; } catch { return Infinity; } };
const isObj = x => !!x && typeof x === 'object' && !Array.isArray(x);
const errText = e => String((e && e.message) || e).slice(0, 300);
const isQuota = e => /neuron|allocation|4006|daily free/i.test(errText(e));

export function validate(body, spec) {
  if (!isObj(body)) return {error: 'body must be a JSON object'};
  const names = new Set(spec.tools.map(t => t.name));
  const context = body.context ?? {};
  if (!isObj(context)) return {error: 'context must be an object'};
  if (size(context) > LIMITS.context) return {error: 'context too large'};
  const msgs = body.messages;
  if (!Array.isArray(msgs) || !msgs.length) return {error: 'messages must be a non-empty array'};
  if (msgs.length > LIMITS.messages) return {error: 'too many messages'};
  const messages = [];
  for (const m of msgs) {
    if (!isObj(m)) return {error: 'each message must be an object'};
    if (m.role === 'user' || m.role === 'assistant') {
      const c = typeof m.content === 'string' ? m.content.trim() : '';
      if (!c) return {error: 'empty message'};
      if (c.length > LIMITS.content) return {error: 'message too long'};
      messages.push({role: m.role, content: c});
    } else if (m.role === 'tool') {
      if (!names.has(m.name)) return {error: 'unknown tool'};
      const args = isObj(m.args) ? m.args : {};
      if (size(args) > LIMITS.toolArgs || size(m.result ?? null) > LIMITS.toolResult) return {error: 'tool data too large'};
      messages.push({role: 'tool', name: m.name, args, result: m.result ?? null});
    } else return {error: 'unknown role'};
  }
  if (messages[messages.length - 1].role === 'assistant') return {error: 'the last message must be from the user or a tool'};
  return {messages, context, final: body.final === true};
}

// prompt mode: the tools are described in the instructions and called with one line of JSON
export function toolInstructions(tools) {
  const list = tools.map(t => JSON.stringify({tool: t.name, description: t.description,
    arguments: Object.fromEntries(Object.entries((t.parameters && t.parameters.properties) || {}).map(([k, p]) => [k, `${p.type}: ${p.description}`]))}));
  return '\n\nCALLING A TOOL\nTo call a tool, reply with only one line of JSON and no other text: {"tool": "<name>", "arguments": {<argument>: <value>}}\n' +
    'Use only the tool names and argument names listed below. When you have the result, answer the user in plain text, not JSON.\n' + list.join('\n');
}

// Tool turns are written as plain text, so the conversation only ever uses system/user/assistant roles.
// That works with every chat template on Workers AI, including those that require strict alternation.
export function buildMessages(prompt, v, {tools = null} = {}) {
  const out = [{role: 'system', content: prompt + (tools ? toolInstructions(tools) : '') + '\n\nDATA (the page right now, JSON):\n' + JSON.stringify(v.context)}];
  const push = (role, content) => {
    const last = out[out.length - 1];
    if (last.role === role) last.content += '\n\n' + content;
    else out.push({role, content});
  };
  for (let i = 0; i < v.messages.length;) {
    if (v.messages[i].role !== 'tool') { push(v.messages[i].role, v.messages[i].content); i++; continue; }
    const group = [];
    while (i < v.messages.length && v.messages[i].role === 'tool') group.push(v.messages[i++]);
    push('assistant', group.map(g => tools ? JSON.stringify({tool: g.name, arguments: g.args}) : `Calling ${g.name} ${JSON.stringify(g.args)}`).join('\n'));
    push('user', group.map(g => `Result of ${g.name} (computed by the page, not typed by the user):\n${JSON.stringify(g.result)}`).join('\n\n')
      + (v.final ? '\n\nAnswer the question now with these results. Do not call more tools.' : ''));
  }
  while (out.length > 1 && out[1].role === 'assistant') out.splice(1, 1);   // a conversation starts with the user
  return out;
}

function toCall(c) {
  if (!isObj(c)) return null;
  const f = isObj(c.function) ? c.function : c;
  const name = typeof f.name === 'string' ? f.name : typeof f.tool === 'string' ? f.tool : null;
  let args = f.arguments ?? f.parameters ?? f.args ?? {};
  if (typeof args === 'string') { try { args = JSON.parse(args || '{}'); } catch { args = {}; } }
  if (!isObj(args)) args = {};
  return name ? {id: typeof c.id === 'string' ? c.id : null, name, args} : null;
}

// a tool call written into the text: prompt mode asks for exactly that, and some models do it anyway
function callFromText(text) {
  const s = text.trim().replace(/^<\|python_tag\|>/, '').replace(/^\[TOOL_CALLS\]/, '').replace(/^```(?:json)?\s*/i, '').replace(/\s*```$/, '').trim();
  const a = s.search(/[[{]/), b = Math.max(s.lastIndexOf('}'), s.lastIndexOf(']'));
  if (a < 0 || b <= a) return null;
  try { let j = JSON.parse(s.slice(a, b + 1)); if (Array.isArray(j)) j = j[0]; return toCall(j); } catch { return null; }
}

// Workers AI answers in three shapes depending on the model:
//   {response, tool_calls: [{name, arguments}]}                        (Llama 3.x, Mistral)
//   {response, tool_calls: [{id, type, function: {name, arguments}}]}   (Llama 4)
//   {choices: [{message: {content, tool_calls: [...]}}]}                (Qwen 3, gpt-oss: OpenAI style)
export function normalize(out, spec, {fromText = true} = {}) {
  const names = new Set(spec.tools.map(t => t.name));
  if (out == null) return {text: '', calls: []};
  if (typeof out === 'string') out = {response: out};
  const msg = Array.isArray(out.choices) && out.choices[0] && out.choices[0].message;
  let text = msg ? msg.content : out.response, calls = msg ? msg.tool_calls : out.tool_calls;
  if (text != null && typeof text !== 'string') text = JSON.stringify(text);
  text = String(text || '').replace(/<think>[\s\S]*?<\/think>/g, '').replace(/^[\s\S]*?<\/think>/, '');
  calls = (Array.isArray(calls) ? calls : []).map(toCall).filter(c => c && names.has(c.name));
  if (!calls.length && fromText) { const c = callFromText(text); if (c && names.has(c.name)) { calls = [c]; text = ''; } }
  return {text: text.trim(), calls: calls.slice(0, 3)};
}

function fail(e, cors) {
  const detail = errText(e), quota = isQuota(e);
  console.error('Workers AI error:', detail);
  return json({error: quota ? 'quota' : 'upstream_error', detail}, quota ? 429 : 502, cors);
}

export async function handle(request, env, spec) {
  const url = new URL(request.url);
  const origin = (request.headers.get('Origin') || '').replace(/\/$/, '');
  const allowed = origin && origins(env).includes(origin);
  const cors = allowed ? {'Access-Control-Allow-Origin': origin, 'Vary': 'Origin'} : {'Vary': 'Origin'};

  if (url.pathname === '/health') return json({ok: true, model: model(env), tools: toolMode(env) === 'auto' && nativeRefused ? 'prompt' : toolMode(env)}, 200, cors);
  if (request.method === 'OPTIONS') {
    if (!allowed) return new Response(null, {status: 403});
    return new Response(null, {status: 204, headers: {...cors, 'Access-Control-Allow-Methods': 'POST, OPTIONS', 'Access-Control-Allow-Headers': 'content-type, x-access-code', 'Access-Control-Max-Age': '86400'}});
  }
  if (url.pathname !== '/chat') return json({error: 'not_found'}, 404, cors);
  if (request.method !== 'POST') return json({error: 'method'}, 405, cors);
  if (!allowed) return json({error: 'origin'}, 403, cors);
  if (env.ACCESS_CODE && !sameCode(request.headers.get('x-access-code') || '', env.ACCESS_CODE)) return json({error: 'access_code'}, 401, cors);
  if (env.RATE_LIMITER) {
    const {success} = await env.RATE_LIMITER.limit({key: 'ip:' + (request.headers.get('CF-Connecting-IP') || 'unknown')});
    if (!success) return json({error: 'rate_limited'}, 429, cors);
  }

  const raw = await request.text();
  if (raw.length > LIMITS.bodyChars) return json({error: 'too_large'}, 413, cors);
  let body;
  try { body = JSON.parse(raw); } catch { return json({error: 'bad_request', message: 'invalid JSON'}, 400, cors); }
  const v = validate(body, spec);
  if (v.error) return json({error: 'bad_request', message: v.error}, 400, cors);

  const tm = toolMode(env);
  const mode = v.final ? 'none' : (tm === 'prompt' || (tm === 'auto' && nativeRefused)) ? 'prompt' : 'native';
  const run = m => env.AI.run(model(env), {
    messages: buildMessages(spec.prompt, v, {tools: m === 'prompt' ? spec.tools : null}),
    max_tokens: LIMITS.maxTokens, temperature: 0.2,
    ...(m === 'native' ? {tools: spec.tools} : {}),
  });
  let out, used = mode;
  try {
    out = await run(mode);
  } catch (e) {
    if (mode !== 'native' || tm !== 'auto' || isQuota(e)) return fail(e, cors);
    console.error('Native tool calling failed, trying prompted tools:', errText(e));
    try { out = await run('prompt'); used = 'prompt'; nativeRefused = true; } catch (e2) { return fail(e2, cors); }
  }
  const r = normalize(out, spec, {fromText: !v.final});
  return json({text: r.text, calls: v.final ? [] : r.calls, mode: used}, 200, cors);
}
