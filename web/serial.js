/**
 * serial.js — 共用 Web Serial API 連接邏輯
 * 使用方式：
 *   const conn = new SerialConnection({ onMessage, onLine, onConnect, onDisconnect });
 *   await conn.connect();
 *   await conn.send({ cmd: 'write', ... });
 *   await conn.disconnect();
 */

export class SerialConnection {
  #port = null;
  #reader = null;
  #writer = null;
  #keepReading = false;
  #buffer = '';

  constructor({ onMessage, onLine, onConnect, onDisconnect } = {}) {
    this.onMessage = onMessage ?? (() => {});
    this.onLine = onLine ?? (() => {});
    this.onConnect = onConnect ?? (() => {});
    this.onDisconnect = onDisconnect ?? (() => {});
    this.baudRate = 115200;

    if ('serial' in navigator) {
      navigator.serial.addEventListener('disconnect', () => {
        if (this.#port) this.disconnect();
      });
    }
  }

  get connected() { return this.#port !== null; }

  async connect() {
    if (!('serial' in navigator)) throw new Error('此瀏覽器不支援 Web Serial API，請使用 Chrome / Edge');
    this.#port = await navigator.serial.requestPort();
    await this.#port.open({ baudRate: this.baudRate });
    this.#writer = this.#port.writable.getWriter();
    this.#keepReading = true;
    this.#readLoop();
    this.onConnect();
  }

  async disconnect() {
    this.#keepReading = false;
    try { if (this.#reader) await this.#reader.cancel(); } catch {}
    try { if (this.#writer) this.#writer.releaseLock(); } catch {}
    try { if (this.#port) await this.#port.close(); } catch {}
    this.#port = null; this.#reader = null; this.#writer = null; this.#buffer = '';
    this.onDisconnect();
  }

  async send(obj) {
    if (!this.#writer) throw new Error('未連接');
    const line = JSON.stringify(obj) + '\n';
    await this.#writer.write(new TextEncoder().encode(line));
    return line.trim();
  }

  async #readLoop() {
    const decoder = new TextDecoderStream();
    const closed = this.#port.readable.pipeTo(decoder.writable).catch(() => {});
    this.#reader = decoder.readable.getReader();
    try {
      while (this.#keepReading) {
        const { value, done } = await this.#reader.read();
        if (done) break;
        if (value) {
          this.#buffer += value;
          let idx;
          while ((idx = this.#buffer.indexOf('\n')) >= 0) {
            const raw = this.#buffer.slice(0, idx).trim();
            this.#buffer = this.#buffer.slice(idx + 1);
            if (!raw) continue;
            this.onLine(raw);
            if (raw.startsWith('{') && raw.endsWith('}')) {
              try { this.onMessage(JSON.parse(raw)); } catch {}
            }
          }
        }
      }
    } catch (e) {
      this.onLine('[read error] ' + e.message);
    } finally {
      try { this.#reader.releaseLock(); } catch {}
      await closed;
    }
  }
}

/** 通用 UI 工具 */
export function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g,
    c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

export function badgeFor(type) {
  const cls = { SKILL: 'skill', RPS: 'rps', CHARACTER: '', UNKNOWN: 'unknown' }[type] ?? 'unknown';
  return `<span class="badge ${cls}">${escapeHtml(type)}</span>`;
}

export function statsHtml(c) {
  if (c.type === 'CHARACTER') return `
    <span class="stat">HP <b>${c.hp ?? '-'}</b></span>
    <span class="stat">✊ <b>${c.atk_rock ?? '-'}</b></span>
    <span class="stat">✌️ <b>${c.atk_scissors ?? '-'}</b></span>
    <span class="stat">✋ <b>${c.atk_paper ?? '-'}</b></span>`;
  if (c.type === 'SKILL') return `
    <span class="stat">heal <b>${c.hp_heal ?? '-'}</b></span>
    <span class="stat">✊×<b>${c.mul_rock ?? '-'}</b></span>
    <span class="stat">✌️×<b>${c.mul_scissors ?? '-'}</b></span>
    <span class="stat">✋×<b>${c.mul_paper ?? '-'}</b></span>`;
  if (c.type === 'RPS') {
    const playerLabel = ({ 1: 'P1', 2: 'P2' })[c.player] ?? '通用';
    return `<span class="stat">出招 <b>${escapeHtml(c.rps ?? '-')}</b></span><span class="stat">玩家 <b>${playerLabel}</b></span>`;
  }
  return '';
}
