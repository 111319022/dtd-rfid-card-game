"""
card_server.py — 透過 Web Serial API 雙向操作卡牌

通訊：line-delimited JSON，baud 115200
"""

import sys
import select
import time
import json
from machine import Pin
from mfrc522 import MFRC522
from card_utils import (
    BLOCK_NAME, BLOCK_STATS, KEY,
    TYPE_CHARACTER, TYPE_SKILL, TYPE_RPS,
    string_to_block, encode_stats, decode_card,
)

PIN_SCK, PIN_MOSI, PIN_MISO, PIN_RST, PIN_CS = 18, 23, 19, 22, 5
PIN_LED = 2

DEBOUNCE_SEC = 1.5
SCAN_INTERVAL = 0.05

TYPE_MAP = {
    "CHARACTER": TYPE_CHARACTER,
    "SKILL": TYPE_SKILL,
    "RPS": TYPE_RPS,
}

led = None
_stdin_buf = ""          # stdin 累積緩衝區（處理 Serial 分段傳輸）
_poller = select.poll()
_poller.register(sys.stdin, select.POLLIN)
pending = None


def send(obj):
    print(json.dumps(obj))


def blink(times=1, on_ms=120, off_ms=120):
    for _ in range(times):
        led.value(1); time.sleep_ms(on_ms)
        led.value(0); time.sleep_ms(off_ms)


# ─── stdin 讀取（有緩衝、不截斷）────────────────────────────────

def _drain_stdin():
    """把目前 stdin 中所有可用位元組讀進 _stdin_buf"""
    global _stdin_buf
    while _poller.poll(0):
        try:
            chunk = sys.stdin.read(64)
            if chunk:
                _stdin_buf += chunk
            else:
                break
        except Exception:
            break


def process_stdin():
    """
    嘗試從緩衝區取出一行完整 JSON 並處理。
    每次只處理一行；呼叫多次可清空所有排隊的指令。
    """
    global _stdin_buf, pending
    _drain_stdin()
    if "\n" not in _stdin_buf:
        return
    idx = _stdin_buf.index("\n")
    line = _stdin_buf[:idx].strip()
    _stdin_buf = _stdin_buf[idx + 1:]
    if not line:
        return
    try:
        handle_command(json.loads(line))
    except Exception as e:
        send({"event": "error", "message": "bad json: " + str(e)})


# ─── 卡片讀寫（單次 auth session）───────────────────────────────

def _auth(rdr, uid):
    """select + auth，成功回傳 True"""
    if rdr.select_tag(uid) != rdr.OK:
        return False
    if rdr.auth(rdr.AUTHENT1A, BLOCK_STATS, KEY, uid) != rdr.OK:
        rdr.stop_crypto1()
        return False
    return True


def read_card(rdr, uid):
    """讀取並回傳 (name_block, stats_block)，失敗回 (None, None)"""
    if not _auth(rdr, uid):
        return None, None
    nb = rdr.read(BLOCK_NAME)
    sb = rdr.read(BLOCK_STATS)
    rdr.stop_crypto1()
    return nb, sb


def check_and_write(rdr, uid, name_blk, stats_blk):
    """
    單次 auth session：先讀取現有資料，若空白則直接寫入並驗證。
    回傳 (existing_card | None, write_ok | None, verified_card | None)
      - existing_card != None  → 卡片已有資料，需網頁確認
      - write_ok == True/False → 已嘗試寫入
      - verified_card          → 寫後讀回的卡片資料
    """
    if not _auth(rdr, uid):
        return None, False, None

    nb = rdr.read(BLOCK_NAME)
    sb = rdr.read(BLOCK_STATS)
    uid_str = uid_str_of(uid)

    if nb is not None and sb is not None:
        existing = decode_card(nb, sb, uid_str)
        if existing["type"] != "UNKNOWN":
            rdr.stop_crypto1()
            return existing, None, None  # 有資料，讓網頁決定

    # 空白卡或讀取失敗 → 同一 session 直接寫入
    ok1 = rdr.write(BLOCK_NAME, name_blk)
    ok2 = rdr.write(BLOCK_STATS, stats_blk)
    if ok1 != rdr.OK or ok2 != rdr.OK:
        rdr.stop_crypto1()
        return None, False, None

    # 同一 session 讀回驗證
    nb2 = rdr.read(BLOCK_NAME)
    sb2 = rdr.read(BLOCK_STATS)
    rdr.stop_crypto1()

    card = decode_card(nb2, sb2, uid_str) if nb2 is not None else {"uid": uid_str}
    return None, True, card


def force_write(rdr, uid, name_blk, stats_blk):
    """
    單次 auth session：直接寫入並讀回驗證。
    回傳 (write_ok, verified_card | None)
    """
    if not _auth(rdr, uid):
        return False, None
    ok1 = rdr.write(BLOCK_NAME, name_blk)
    ok2 = rdr.write(BLOCK_STATS, stats_blk)
    if ok1 != rdr.OK or ok2 != rdr.OK:
        rdr.stop_crypto1()
        return False, None
    nb = rdr.read(BLOCK_NAME)
    sb = rdr.read(BLOCK_STATS)
    rdr.stop_crypto1()
    uid_str = uid_str_of(uid)
    card = decode_card(nb, sb, uid_str) if nb is not None else {"uid": uid_str}
    return True, card


# ─── 指令處理 ────────────────────────────────────────────────────

def build_blocks(name, stats):
    t = TYPE_MAP.get(stats.get("type", "").upper())
    if t is None:
        raise ValueError("unknown card type: " + str(stats.get("type")))
    if t == TYPE_CHARACTER:
        sb = encode_stats(t,
                          hp=int(stats.get("hp", 0)),
                          atk_scissors=int(stats.get("atk_scissors", 0)),
                          atk_rock=int(stats.get("atk_rock", 0)),
                          atk_paper=int(stats.get("atk_paper", 0)))
    elif t == TYPE_SKILL:
        sb = encode_stats(t,
                          hp_heal=int(stats.get("hp_heal", 0)),
                          mul_scissors=float(stats.get("mul_scissors", 1.0)),
                          mul_rock=float(stats.get("mul_rock", 1.0)),
                          mul_paper=float(stats.get("mul_paper", 1.0)))
    elif t == TYPE_RPS:
        sb = encode_stats(t, rps=stats.get("rps", ""))
    return string_to_block(name or ""), sb


def handle_command(cmd):
    global pending
    action = cmd.get("cmd")
    if action == "write":
        try:
            name_blk, stats_blk = build_blocks(cmd.get("name", ""), cmd.get("stats", {}))
            pending = {
                "name_blk": name_blk,
                "stats_blk": stats_blk,
                "force": bool(cmd.get("force", False)),
            }
            send({"event": "waiting", "action": "write"})
        except Exception as e:
            send({"event": "error", "message": "build failed: " + str(e)})
    elif action == "cancel":
        pending = None
        send({"event": "cancelled"})
    else:
        send({"event": "error", "message": "unknown cmd: " + str(action)})


def uid_str_of(uid):
    return "-".join("{:02X}".format(b) for b in uid[:4])


# ─── 主迴圈 ──────────────────────────────────────────────────────

def main():
    global led, pending
    led = Pin(PIN_LED, Pin.OUT); led.value(0)
    blink(1, 150, 0)

    rdr = MFRC522(PIN_SCK, PIN_MOSI, PIN_MISO, PIN_RST, PIN_CS)
    send({"event": "ready"})

    last_uid = None
    last_time = 0

    while True:
        # ① stdin 指令（迴圈開頭清空所有排隊指令）
        process_stdin()

        # ② 偵測卡片
        stat, _ = rdr.request(rdr.REQIDL)
        if stat != rdr.OK:
            if last_uid is not None and (time.time() - last_time) > DEBOUNCE_SEC:
                last_uid = None
            time.sleep(SCAN_INTERVAL)
            continue

        stat, raw_uid = rdr.anticoll()
        if stat != rdr.OK:
            time.sleep(SCAN_INTERVAL)
            continue

        uid_str = uid_str_of(raw_uid)
        now = time.time()

        # ③ 卡片剛偵測到：再讀一次 stdin
        #    解決「按鈕和感應幾乎同時」的 race condition
        process_stdin()

        # ④ 寫入模式
        if pending is not None:
            p = pending
            pending = None  # 先清除，避免重複觸發

            if p.get("force", False):
                ok, card = force_write(rdr, raw_uid, p["name_blk"], p["stats_blk"])
            else:
                existing, ok, card = check_and_write(rdr, raw_uid, p["name_blk"], p["stats_blk"])
                if existing is not None:
                    # 有資料，等網頁確認
                    send({"event": "card_exists", "card": existing})
                    last_uid = uid_str
                    last_time = now
                    time.sleep(SCAN_INTERVAL)
                    continue

            if ok:
                send({"event": "write", "ok": True, "card": card})
                blink(2, 80, 80)
            else:
                send({"event": "write", "ok": False, "uid": uid_str,
                      "message": "write/auth failed"})
                blink(3, 60, 60)

            last_uid = uid_str
            last_time = now

        # ⑤ 自動讀取（防抖）
        elif not (uid_str == last_uid and (now - last_time) < DEBOUNCE_SEC):
            nb, sb = read_card(rdr, raw_uid)
            card = decode_card(nb, sb, uid_str) if nb is not None else {"type": "UNKNOWN", "uid": uid_str}
            send({"event": "read", "card": card})
            led.value(1); time.sleep_ms(200); led.value(0)
            last_uid = uid_str
            last_time = now

        time.sleep(SCAN_INTERVAL)


try:
    main()
except KeyboardInterrupt:
    print(json.dumps({"event": "stopped"}))
except Exception as e:
    import sys as _sys
    print(json.dumps({"event": "error", "message": str(e)}))
    _sys.print_exception(e)
