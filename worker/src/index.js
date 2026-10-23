// Entry point. The instructions and tool definitions are shared with the page (build_page.py embeds the same files).
import PROMPT from '../../assistant/prompt.txt';
import TOOLS from '../../assistant/tools.json';
import {handle} from './handler.js';

const SPEC = {prompt: PROMPT.trim(), tools: TOOLS};

export default {
  fetch: (request, env) => handle(request, env, SPEC),
};
