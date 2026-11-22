// 控制面通道的发送队列。
//
// WebSocket 断开期间 `ws.send` 无处可去，原来的实现直接静默丢弃：
// worker.created / ack / result 一旦丢，控制面的 dispatch 就永远停在
// pending/created/dispatched，只能等 Watchdog 报「派单超时」。
// 这里把**关键回执**先入队，重连注册成功后按序补发；进度/心跳这类
// 时效性强的消息断线时直接丢，避免重连后再刷一堆过时进度。

export const CRITICAL_TYPES = new Set([
  'worker.created',
  'worker.create_failed',
  'ack',
  'nack',
  'result',
  'worker.destroyed',
]);

export function isCritical(msg) {
  return CRITICAL_TYPES.has(String(msg && msg.t ? msg.t : ''));
}

export class Outbox {
  constructor({ limit = 500, log = () => {} } = {}) {
    this.limit = Math.max(1, limit);
    this.log = log;
    this.queue = [];
  }

  get size() {
    return this.queue.length;
  }

  push(msg) {
    if (this.queue.length >= this.limit) {
      const dropped = this.queue.shift();
      this.log(`[node] 待补发队列已满（${this.limit}），丢弃最旧 ${dropped && dropped.t}`);
    }
    this.queue.push(msg);
  }

  /** 按序补发；write(msg) 返回 false 表示链路不可用，剩余消息留在队列里。 */
  flush(write) {
    while (this.queue.length > 0) {
      if (!write(this.queue[0])) return false;
      this.queue.shift();
    }
    return true;
  }
}