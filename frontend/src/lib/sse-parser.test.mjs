import test from "node:test";
import assert from "node:assert/strict";
import { SseParser } from "./sse-parser.mjs";

test("parses an event split across arbitrary network chunks", () => {
  const p = new SseParser();
  assert.deepEqual(p.push('event: step\ndata: {"hop":'), []);
  const firstChunk = p.push('1,"finding":"a graph path"}\n\nevent: done');
  assert.equal(firstChunk.length, 1);
  const result = [...firstChunk, ...p.push('\ndata: {}\n\n')];
  assert.equal(result.length, 2);
  assert.equal(result[0].event, "step");
  assert.deepEqual(result[0].data, { hop: 1, finding: "a graph path" });
  assert.deepEqual(result[1].data, {});
});

test("joins multi-line SSE data fields and handles CRLF/comments", () => {
  const p = new SseParser();
  const result = p.push('event: final\r\ndata: {\"ok\":\r\ndata: true}\r\n\r\n: heartbeat\r\n\r\n');
  assert.equal(result.length, 1);
  assert.deepEqual(result[0].data, { ok: true });
});

test("handles CRLF, comments, and an unterminated final block", () => {
  const p = new SseParser();
  const result = p.push(': heartbeat\r\nevent: final\r\ndata: {"ok":true}\r\n\r\nevent: done\ndata: {}');
  assert.equal(result.length, 1);
  assert.equal(result[0].event, "final");
  assert.deepEqual(result[0].data, { ok: true });
  assert.equal(p.finish()[0].event, "done");
});
