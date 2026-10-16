/** Incremental Server-Sent Events parser; safe for split UTF-8/text chunks and batched events. */
export function parseSseBlock(block) {
  let event = "message";
  const data = [];
  let id;
  for (const line of block.split(/\r?\n/)) {
    if (!line || line.startsWith(":")) continue;
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "event") event = value || "message";
    else if (field === "data") data.push(value);
    else if (field === "id") id = value;
  }
  if (!data.length) return null;
  const raw = data.join("\n");
  let parsed = raw;
  try { parsed = JSON.parse(raw); } catch { /* plain-text SSE data remains readable */ }
  return { event, data: parsed, id };
}

export class SseParser {
  constructor() { this.buffer = ""; }
  push(chunk) {
    this.buffer += chunk;
    const emitted = [];
    while (true) {
      const match = /\r?\n\r?\n/.exec(this.buffer);
      if (!match) break;
      const block = this.buffer.slice(0, match.index);
      this.buffer = this.buffer.slice(match.index + match[0].length);
      const event = parseSseBlock(block);
      if (event) emitted.push(event);
    }
    return emitted;
  }
  finish() {
    const tail = this.buffer;
    this.buffer = "";
    const event = tail ? parseSseBlock(tail) : null;
    return event ? [event] : [];
  }
}
