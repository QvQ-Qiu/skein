import assert from 'node:assert/strict';
import { test } from 'node:test';

import { Outbox, isCritical } from '../outbox.mjs';

test('回执类消息算关键消息', () => {
  for (const t of ['worker.created', 'worker.create_failed', 'ack', 'nack', 'result', 'worker.destroyed']) {
    assert.equal(isCritical({ t }), true, t);
  }
  for (const t of ['progress', 'node.heartbeat', 'room.post']) {
    assert.equal(isCritical({ t }), false, t);
  }
});

test('断线时入队，重连后按序补发', () => {
  const outbox = new Outbox();
  let online = false;
  const sent = [];
  const write = (msg) => {
    if (!online) return false;
    sent.push(msg);
    return true;
  };

  outbox.push({ t: 'ack', n: 1 });
  outbox.push({ t: 'result', n: 2 });
  assert.equal(outbox.flush(write), false);
  assert.deepEqual(sent, []);
  assert.equal(outbox.size, 2);

  online = true;
  assert.equal(outbox.flush(write), true);
  assert.deepEqual(sent.map((m) => m.n), [1, 2]);
  assert.equal(outbox.size, 0);
});

test('补发中途断开，未发出去的消息留在队列里', () => {
  const outbox = new Outbox();
  outbox.push({ t: 'ack', n: 1 });
  outbox.push({ t: 'result', n: 2 });
  const sent = [];
  outbox.flush((msg) => {
    if (sent.length === 1) return false;  // 写出一条后链路又断了
    sent.push(msg);
    return true;
  });
  assert.deepEqual(sent.map((m) => m.n), [1]);
  assert.deepEqual(outbox.queue.map((m) => m.n), [2]);
});

test('队列有上限，满了丢最旧并记日志', () => {
  const lines = [];
  const outbox = new Outbox({ limit: 2, log: (line) => lines.push(line) });
  outbox.push({ t: 'ack', n: 1 });
  outbox.push({ t: 'ack', n: 2 });
  outbox.push({ t: 'ack', n: 3 });
  assert.deepEqual(outbox.queue.map((m) => m.n), [2, 3]);
  assert.match(lines[0], /丢弃最旧/);
});