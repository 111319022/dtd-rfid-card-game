"""
整合版 main.py：
- 同時支援 Web Serial stdin 指令（write / cancel）
- 若偵測到啟動卡（START_UID_STR），進入遊戲讀卡模式（輸出 card JSON 給網頁）
- 偵測到其他卡時，自動執行寫入邏輯（若有 pending 指令）或回報讀取結果
啟動卡 UID 預設為 DE-AD-BE-EF（可修改 START_UID_STR）
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

PIN_SCK  = 18
PIN_MOSI = 23
PIN_MISO = 19
PIN_RST  = 22
PIN_CS   = 5
PIN_LED  = 2

DEBUG        = True
DEBOUNCE_SEC = 1.5
SCAN_INTERVAL = 0.05

# 啟動卡（使用前四個 byte 的字串表示，例：88-04-2A-29）
START_UID_STR = "88-04-2A-29"

led = None
_stdin_buf = ""
_poller = select.poll()
_poller.register(sys.stdin, select.POLLIN)
pending = None
game_mode = False


def send(obj):
	print(json.dumps(obj))


def log(msg):
	if DEBUG:
		print("[DEBUG]", msg)


def blink(times=1, on_ms=120, off_ms=120):
	for _ in range(times):
		led.value(1); time.sleep_ms(on_ms)
		led.value(0); time.sleep_ms(off_ms)


def _drain_stdin():
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
	"""嘗試從 stdin 緩衝取出一行 JSON 並處理（每次只處理一行）"""
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

	ok1 = rdr.write(BLOCK_NAME, name_blk)
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


def build_blocks(name, stats):
	t = {"CHARACTER": TYPE_CHARACTER, "SKILL": TYPE_SKILL, "RPS": TYPE_RPS}.get(stats.get("type", "").upper())
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
	try:
		send({"event": "debug", "message": "recv_cmd", "cmd": cmd})
	except Exception:
		pass
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


def init_reader():
	log("初始化 MFRC522 ...")
	rdr = MFRC522(PIN_SCK, PIN_MOSI, PIN_MISO, PIN_RST, PIN_CS)
	ver = rdr._rreg(0x37)
	log("Version Register = 0x{:02X}".format(ver))
	if ver in (0x00, 0xFF):
		send({"event": "error", "message": "SPI no response"})
	return rdr


def main():
	global led, pending, game_mode
	led = Pin(PIN_LED, Pin.OUT); led.value(0)
	blink(1, 150, 0)

	rdr = init_reader()
	send({"event": "ready", "start_uid": START_UID_STR})

	last_uid = None
	last_time = 0

	while True:
		# 處理 stdin 指令（非阻塞）
		process_stdin()

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

		# 再讀一次 stdin 以避免 race
		process_stdin()

		# 若偵測到啟動卡 → 進入遊戲模式（持續回報 read event）
		if uid_str == START_UID_STR:
			game_mode = True
			send({"event": "game_started", "uid": uid_str})
			last_uid = uid_str
			last_time = now
			time.sleep(SCAN_INTERVAL)
			continue

		# 若為非啟動卡，進入編輯邏輯（若有 pending 就寫入，否則回報讀取）
		if pending is not None:
			p = pending
			pending = None
			if p.get("force", False):
				ok, card = force_write(rdr, raw_uid, p["name_blk"], p["stats_blk"])
			else:
				existing, ok, card = check_and_write(rdr, raw_uid, p["name_blk"], p["stats_blk"])
				if existing is not None:
					send({"event": "card_exists", "card": existing})
					last_uid = uid_str
					last_time = now
					time.sleep(SCAN_INTERVAL)
					continue

			if ok:
				send({"event": "write", "ok": True, "card": card})
				blink(2, 80, 80)
			else:
				send({"event": "write", "ok": False, "uid": uid_str, "message": "write/auth failed"})
				blink(3, 60, 60)

			last_uid = uid_str
			last_time = now
			time.sleep(SCAN_INTERVAL)
			continue

		# 若處於遊戲模式或一般讀取（預設回報讀取）
		if not (uid_str == last_uid and (now - last_time) < DEBOUNCE_SEC):
			nb, sb = read_card(rdr, raw_uid)
			card = decode_card(nb, sb, uid_str) if nb is not None else {"type": "UNKNOWN", "uid": uid_str}
			send(card)
			led.value(1); time.sleep_ms(200); led.value(0)
			last_uid = uid_str
			last_time = now

		time.sleep(SCAN_INTERVAL)


try:
	main()
except KeyboardInterrupt:
	send({"event": "stopped"})
except Exception as e:
	try:
		import sys as _sys
		send({"event": "error", "message": str(e)})
		_sys.print_exception(e)
	except Exception:
		pass
 
