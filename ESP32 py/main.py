"""
main.py — RFID Card Game ESP32 主程式（整合版）
所有邏輯均在此檔案，無需其他 .py（除 mfrc522.py）。

通訊：line-delimited JSON，baud 115200

指令（Web → ESP32）：
  {"cmd":"write","force":bool,"name":"...","stats":{...}}
  {"cmd":"erase"}              ← 清除卡片資料
  {"cmd":"cancel"}
  {"cmd":"list_activations"}   ← 查詢啟動卡清單
  {"cmd":"set_activations","uids":["AA-BB-CC-DD",...]}  ← 儲存啟動卡清單
  {"cmd":"list_settings"}      ← 查詢設定卡清單
  {"cmd":"set_settings","uids":[...]}                   ← 儲存設定卡清單

事件（ESP32 → Web）：
  {"event":"ready","activations":[...],"settings":[...]}
  {"event":"waiting","action":"write"|"erase"}
  {"event":"read","card":{...}}
  {"event":"activation","card":{...}}    ← 偵測到啟動卡
  {"event":"settings_card","card":{...}} ← 偵測到設定卡
  {"event":"write","ok":bool,"card":{...}}
  {"event":"erase","ok":bool,"uid":"..."}
  {"event":"card_exists","card":{...}}
  {"event":"cancelled"}
  {"event":"activations","uids":[...]}
  {"event":"activations_saved","uids":[...]}
  {"event":"settings","uids":[...]}
  {"event":"settings_saved","uids":[...]}
  {"event":"error","message":"..."}
"""

import sys
import select
import time
import json
import os
from machine import Pin
from mfrc522 import MFRC522

# ─── 卡片編解碼（inlined from card_utils.py）────────────────────────

BLOCK_NAME  = 8   # Sector 2, Block 0 → 名稱 (16 bytes)
BLOCK_STATS = 9   # Sector 2, Block 1 → 屬性 (16 bytes)
KEY = [0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF]

TYPE_CHARACTER = 0x01
TYPE_SKILL     = 0x02
TYPE_RPS       = 0x03

RPS_TO_BYTE = {"scissors": 0x01, "rock": 0x02, "paper": 0x03}
BYTE_TO_RPS = {0x01: "scissors", 0x02: "rock", 0x03: "paper"}


def string_to_block(text):
    b = bytearray(text.encode("utf-8"))
    if len(b) > 16:
        b = b[:16]
    else:
        b.extend([0x20] * (16 - len(b)))
    return list(b)


def block_to_string(block):
    try:
        return bytes(block).decode("utf-8").rstrip()
    except Exception:
        return ""


def encode_stats(card_type, **kw):
    buf = [0x00] * 16
    buf[0] = card_type
    if card_type == TYPE_CHARACTER:
        hp = int(kw.get("hp", 0))
        buf[1] = (hp >> 8) & 0xFF
        buf[2] = hp & 0xFF
        buf[3] = int(kw.get("atk_scissors", 0))
        buf[4] = int(kw.get("atk_rock", 0))
        buf[5] = int(kw.get("atk_paper", 0))
    elif card_type == TYPE_SKILL:
        buf[1] = int(kw.get("hp_heal", 0)) & 0xFF
        buf[2] = int(round(kw.get("mul_scissors", 1.0) * 10))
        buf[3] = int(round(kw.get("mul_rock", 1.0) * 10))
        buf[4] = int(round(kw.get("mul_paper", 1.0) * 10))
    elif card_type == TYPE_RPS:
        buf[1] = RPS_TO_BYTE.get(kw.get("rps", ""), 0x00)
        buf[2] = int(kw.get("player", 0)) & 0xFF  # 0=通用, 1=P1, 2=P2
    return buf


def decode_card(name_block, stats_block, uid_str):
    name = block_to_string(name_block)
    t = stats_block[0]
    if t == TYPE_CHARACTER:
        hp = (stats_block[1] << 8) | stats_block[2]
        return {"type": "CHARACTER", "uid": uid_str, "name": name,
                "hp": hp, "atk_scissors": stats_block[3],
                "atk_rock": stats_block[4], "atk_paper": stats_block[5]}
    elif t == TYPE_SKILL:
        hp_heal = stats_block[1]
        if hp_heal >= 128:
            hp_heal -= 256
        return {"type": "SKILL", "uid": uid_str, "name": name,
                "hp_heal": hp_heal,
                "mul_scissors": stats_block[2] / 10,
                "mul_rock":     stats_block[3] / 10,
                "mul_paper":    stats_block[4] / 10}
    elif t == TYPE_RPS:
        return {"type": "RPS", "uid": uid_str, "name": name,
                "rps": BYTE_TO_RPS.get(stats_block[1], "unknown"),
                "player": stats_block[2] if len(stats_block) > 2 else 0}
    else:
        return {"type": "UNKNOWN", "uid": uid_str}


# ─── 啟動卡管理（activation.json）────────────────────────────────────

ACTIVATION_FILE = "activation.json"
SETTINGS_FILE   = "settings.json"
DEFAULT_ACTIVATIONS = ["39-72-13-07"]
DEFAULT_SETTINGS    = ["B4-27-0D-03"]
_activations = []
_settings_uids = []


def _load_uid_list(path, defaults):
    try:
        with open(path, "r") as f:
            data = json.load(f)
            if isinstance(data, list):
                return [str(u).upper() for u in data]
    except Exception:
        pass
    return defaults[:]


def _save_uid_list(path, uids):
    norm = [str(u).upper() for u in uids]
    try:
        with open(path, "w") as f:
            json.dump(norm, f)
    except Exception as e:
        send({"event": "error", "message": "save " + path + " failed: " + str(e)})
    return norm


def load_activations():
    global _activations
    _activations = _load_uid_list(ACTIVATION_FILE, DEFAULT_ACTIVATIONS)
    return _activations


def save_activations(uids):
    global _activations
    _activations = _save_uid_list(ACTIVATION_FILE, uids)


def load_settings():
    global _settings_uids
    _settings_uids = _load_uid_list(SETTINGS_FILE, DEFAULT_SETTINGS)
    return _settings_uids


def save_settings(uids):
    global _settings_uids
    _settings_uids = _save_uid_list(SETTINGS_FILE, uids)


def is_activation(uid_str):
    return uid_str.upper() in _activations


def is_settings_card(uid_str):
    return uid_str.upper() in _settings_uids


# ─── 硬體設定 ────────────────────────────────────────────────────────

PIN_SCK, PIN_MOSI, PIN_MISO, PIN_RST, PIN_CS = 18, 23, 19, 22, 5
PIN_LED = 2

DEBOUNCE_SEC  = 1.5
SCAN_INTERVAL = 0.05

TYPE_MAP = {
    "CHARACTER": TYPE_CHARACTER,
    "SKILL":     TYPE_SKILL,
    "RPS":       TYPE_RPS,
}

led        = None
_stdin_buf = ""
_poller    = select.poll()
_poller.register(sys.stdin, select.POLLIN)
pending    = None


# ─── 通訊輔助 ────────────────────────────────────────────────────────

def send(obj):
    print(json.dumps(obj))


def blink(times=1, on_ms=120, off_ms=120):
    for _ in range(times):
        led.value(1); time.sleep_ms(on_ms)
        led.value(0); time.sleep_ms(off_ms)


# ─── stdin 讀取（緩衝，避免截斷）─────────────────────────────────────

def _drain_stdin():
    global _stdin_buf
    while _poller.poll(0):
        try:
            ch = sys.stdin.read(1)   # read 1 byte：避免 read(n) 在 MicroPython 等待滿 n bytes 而阻塞
            if ch:
                _stdin_buf += ch
            else:
                break
        except Exception:
            break


def process_stdin():
    global _stdin_buf, pending
    _drain_stdin()
    if "\n" not in _stdin_buf:
        return
    idx  = _stdin_buf.index("\n")
    line = _stdin_buf[:idx].strip()
    _stdin_buf = _stdin_buf[idx + 1:]
    if not line:
        return
    try:
        handle_command(json.loads(line))
    except Exception as e:
        send({"event": "error", "message": "bad json: " + str(e)})


# ─── 卡片讀寫（單次 auth session）────────────────────────────────────

def _auth(rdr, uid):
    if rdr.select_tag(uid) != rdr.OK:
        return False
    if rdr.auth(rdr.AUTHENT1A, BLOCK_STATS, KEY, uid) != rdr.OK:
        rdr.stop_crypto1()
        return False
    return True


def read_card(rdr, uid):
    if not _auth(rdr, uid):
        return None, None
    nb = rdr.read(BLOCK_NAME)
    sb = rdr.read(BLOCK_STATS)
    rdr.stop_crypto1()
    return nb, sb


def check_and_write(rdr, uid, name_blk, stats_blk):
    """force=False：同一 session 讀取確認 → 若有資料回傳 existing，否則寫入+驗證。"""
    if not _auth(rdr, uid):
        return None, False, None

    nb = rdr.read(BLOCK_NAME)
    sb = rdr.read(BLOCK_STATS)
    uid_str = uid_str_of(uid)

    if nb is not None and sb is not None:
        existing = decode_card(nb, sb, uid_str)
        if existing["type"] != "UNKNOWN":
            rdr.stop_crypto1()
            return existing, None, None

    ok1 = rdr.write(BLOCK_NAME,  name_blk)
    ok2 = rdr.write(BLOCK_STATS, stats_blk)
    if ok1 != rdr.OK or ok2 != rdr.OK:
        rdr.stop_crypto1()
        return None, False, None

    nb2 = rdr.read(BLOCK_NAME)
    sb2 = rdr.read(BLOCK_STATS)
    rdr.stop_crypto1()

    card = decode_card(nb2, sb2, uid_str) if nb2 is not None else {"uid": uid_str}
    return None, True, card


def force_write(rdr, uid, name_blk, stats_blk):
    """force=True：同一 session 直接寫入+驗證。"""
    if not _auth(rdr, uid):
        return False, None

    ok1 = rdr.write(BLOCK_NAME,  name_blk)
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


# ─── 指令處理 ────────────────────────────────────────────────────────

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
        sb = encode_stats(t, rps=stats.get("rps", ""), player=int(stats.get("player", 0)))
    return string_to_block(name or ""), sb


def handle_command(cmd):
    global pending
    action = cmd.get("cmd")

    if action == "write":
        try:
            name_blk, stats_blk = build_blocks(cmd.get("name", ""), cmd.get("stats", {}))
            pending = {
                "name_blk":  name_blk,
                "stats_blk": stats_blk,
                "force":     bool(cmd.get("force", False)),
                "is_erase":  False,
            }
            send({"event": "waiting", "action": "write"})
        except Exception as e:
            send({"event": "error", "message": "build failed: " + str(e)})

    elif action == "erase":
        pending = {
            "name_blk":  [0x20] * 16,
            "stats_blk": [0x00] * 16,
            "force":     True,
            "is_erase":  True,
        }
        send({"event": "waiting", "action": "erase"})

    elif action == "cancel":
        pending = None
        send({"event": "cancelled"})

    elif action == "list_activations":
        send({"event": "activations", "uids": _activations})

    elif action == "set_activations":
        uids = cmd.get("uids", [])
        if not isinstance(uids, list):
            send({"event": "error", "message": "set_activations: uids must be a list"})
            return
        save_activations(uids)
        send({"event": "activations_saved", "uids": _activations})

    elif action == "list_settings":
        send({"event": "settings", "uids": _settings_uids})

    elif action == "set_settings":
        uids = cmd.get("uids", [])
        if not isinstance(uids, list):
            send({"event": "error", "message": "set_settings: uids must be a list"})
            return
        save_settings(uids)
        send({"event": "settings_saved", "uids": _settings_uids})

    else:
        send({"event": "error", "message": "unknown cmd: " + str(action)})


def uid_str_of(uid):
    return "-".join("{:02X}".format(b) for b in uid[:4])


# ─── 主迴圈 ──────────────────────────────────────────────────────────

def main():
    global led, pending

    load_activations()
    load_settings()

    led = Pin(PIN_LED, Pin.OUT); led.value(0)
    blink(1, 150, 0)

    rdr = MFRC522(PIN_SCK, PIN_MOSI, PIN_MISO, PIN_RST, PIN_CS)
    send({"event": "ready", "activations": _activations, "settings": _settings_uids})

    last_uid  = None
    last_time = 0

    while True:
        # ① 處理 stdin 指令
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
        now     = time.time()

        # ③ 卡片剛偵測到：再讀一次 stdin（race fix）
        process_stdin()

        # ④ 寫入 / 清除模式
        if pending is not None:
            p       = pending
            pending = None
            is_erase = p.get("is_erase", False)

            if p.get("force", False):
                ok, card = force_write(rdr, raw_uid, p["name_blk"], p["stats_blk"])
            else:
                existing, ok, card = check_and_write(rdr, raw_uid, p["name_blk"], p["stats_blk"])
                if existing is not None:
                    send({"event": "card_exists", "card": existing})
                    last_uid  = uid_str
                    last_time = now
                    time.sleep(SCAN_INTERVAL)
                    continue

            if is_erase:
                send({"event": "erase", "ok": bool(ok), "uid": uid_str})
            else:
                if ok:
                    send({"event": "write", "ok": True, "card": card})
                else:
                    send({"event": "write", "ok": False, "uid": uid_str,
                          "message": "write/auth failed"})

            blink(2 if ok else 3, 80 if ok else 60, 80 if ok else 60)
            last_uid  = uid_str
            last_time = now

        # ⑤ 自動讀取（防抖）
        elif not (uid_str == last_uid and (now - last_time) < DEBOUNCE_SEC):
            nb, sb = read_card(rdr, raw_uid)
            card   = decode_card(nb, sb, uid_str) if (nb is not None and sb is not None) else {"type": "UNKNOWN", "uid": uid_str}

            if is_activation(uid_str):
                send({"event": "activation", "card": card})
            elif is_settings_card(uid_str):
                send({"event": "settings_card", "card": card})
            else:
                send({"event": "read", "card": card})

            led.value(1); time.sleep_ms(200); led.value(0)
            last_uid  = uid_str
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
