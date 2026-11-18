// 进程内事件总线：任何组件的状态变化 → 面板 SSE 实时推送
import { EventEmitter } from 'node:events';
export const bus = new EventEmitter();
bus.setMaxListeners(100);

export function emit(type, payload) {
  bus.emit('change', { type, payload, at: new Date().toISOString() });
}
