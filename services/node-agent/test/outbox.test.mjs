import assert from 'node:assert/strict';
import { test } from 'node:test';

import { Outbox, isCritical } from '../outbox.mjs';

test('关键回执标记为 critical', () => {
  for (const t of ['worker.created', 'worker.create_failed', 'ack', 'nack', 'result']) {
    assert.equal(isCritical({ t }), true, t);
  }
  assert.equal(isCritical({ t: 'progress' }), false);
  assert.equal(isCritical({ t: 'node.heartbeat' }), false);
  assert.equal(isCritical(null), false);
});

test('断线时入队，重连后按序补发', () => {
  const box = new Outbox();
  let connected = false;
  const written = [];
  const write = (msg) => {
    if (!connected) return false;
    written.push(msg);
    return true;
  };
  box.push({ t: 'worker.created', id: 1 });
  box.push({ t: 'result', id: 2 });
  assert.equal(box.flush(write), false);
  assert.equal(box.size, 2);
  connected = true;
  assert.equal(box.flush(write), true);
  assert.deepEqual(written.map((m) => m.id), [1, 2]);
  assert.equal(box.size, 0);
});

test('队列满时丢最旧，保留最新', () => {
  const dropped = [];
  const box = new Outbox({ limit: 2, log: (line) => dropped.push(line) });
  box.push({ t: 'result', id: 1 });
  box.push({ t: 'result', id: 2 });
  box.push({ t: 'result', id: 3 });
  assert.equal(box.size, 2);
  assert.equal(dropped.length, 1);
  const written = [];
  box.flush((m) => {
    written.push(m.id);
    return true;
  });
  assert.deepEqual(written, [2, 3]);
});