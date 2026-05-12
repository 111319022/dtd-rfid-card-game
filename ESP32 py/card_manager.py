"""
card_manager.py — 卡牌讀寫工具
在 Thonny 執行，透過終端機互動操作

功能：
  [R] 讀取卡片 → 顯示所有屬性
  [W] 寫入卡片 → 選擇類型後逐一輸入屬性
"""

import time
from mfrc522 import MFRC522
from card_utils import (
    BLOCK_NAME, BLOCK_STATS, KEY,
    TYPE_CHARACTER, TYPE_SKILL, TYPE_RPS,
    RPS_TO_BYTE, string_to_block, encode_stats, decode_card
)

PIN_SCK  = 18
PIN_MOSI = 23
PIN_MISO = 19
PIN_RST  = 22
PIN_CS   = 5


def init_reader():
    return MFRC522(PIN_SCK, PIN_MOSI, PIN_MISO, PIN_RST, PIN_CS)


def wait_for_card(rdr, prompt="請將卡片靠近讀卡機..."):
    print(prompt)
    while True:
        (stat, _) = rdr.request(rdr.REQIDL)
        if stat == rdr.OK:
            (stat, uid) = rdr.anticoll()
            if stat == rdr.OK:
                return uid
        time.sleep(0.1)


def auth_and_read(rdr, uid):
    """驗證並讀取 Block 8、9，失敗回傳 None, None"""
    if rdr.select_tag(uid) != rdr.OK:
        print("❌ 選卡失敗")
        return None, None

    if rdr.auth(rdr.AUTHENT1A, BLOCK_STATS, KEY, uid) != rdr.OK:
        print("❌ 密碼驗證失敗（非標準 Mifare 卡或已加密）")
        rdr.stop_crypto1()
        return None, None

    name_block  = rdr.read(BLOCK_NAME)
    stats_block = rdr.read(BLOCK_STATS)
    rdr.stop_crypto1()

    if name_block is None or stats_block is None:
        print("❌ 讀取失敗")
        return None, None

    return name_block, stats_block


def auth_and_write(rdr, uid, name_block, stats_block):
    """驗證並寫入 Block 8、9，回傳 True/False"""
    if rdr.select_tag(uid) != rdr.OK:
        print("❌ 選卡失敗")
        return False

    if rdr.auth(rdr.AUTHENT1A, BLOCK_STATS, KEY, uid) != rdr.OK:
        print("❌ 密碼驗證失敗")
        rdr.stop_crypto1()
        return False

    ok_name  = rdr.write(BLOCK_NAME,  name_block)
    ok_stats = rdr.write(BLOCK_STATS, stats_block)
    rdr.stop_crypto1()

    if ok_name != rdr.OK or ok_stats != rdr.OK:
        print("❌ 寫入失敗，請確認卡片貼緊")
        return False

    return True


# ─── 輸入輔助 ───────────────────────────────────────────────

def input_int(prompt, min_val=0, max_val=255):
    while True:
        try:
            v = int(input(prompt))
            if min_val <= v <= max_val:
                return v
            print("  請輸入 {} ~ {} 的整數".format(min_val, max_val))
        except Exception:
            print("  輸入格式錯誤，請重新輸入")


def input_float(prompt, min_val=0.1, max_val=25.5):
    while True:
        try:
            v = float(input(prompt))
            if min_val <= v <= max_val:
                return v
            print("  請輸入 {} ~ {} 的數值".format(min_val, max_val))
        except Exception:
            print("  輸入格式錯誤，請重新輸入")


# ─── 讀取模式 ───────────────────────────────────────────────

def do_read(rdr):
    uid = wait_for_card(rdr)
    uid_str = "-".join("{:02X}".format(b) for b in uid[:4])
    print("UID：{}".format(uid_str))

    name_block, stats_block = auth_and_read(rdr, uid)
    if name_block is None:
        return

    card = decode_card(name_block, stats_block, uid_str)
    t = card.get("type", "UNKNOWN")

    print("\n── 卡牌資料 ─────────────────")
    print("  名稱：{}".format(card.get("name", "(未設定)")))
    print("  類型：{}".format(t))

    if t == "CHARACTER":
        print("  HP            ：{}".format(card["hp"]))
        print("  剪刀攻擊力    ：{}".format(card["atk_scissors"]))
        print("  石頭攻擊力    ：{}".format(card["atk_rock"]))
        print("  布   攻擊力   ：{}".format(card["atk_paper"]))

    elif t == "SKILL":
        print("  回血量        ：{}".format(card["hp_heal"]))
        print("  剪刀攻擊倍數  ：{}x".format(card["mul_scissors"]))
        print("  石頭攻擊倍數  ：{}x".format(card["mul_rock"]))
        print("  布   攻擊倍數 ：{}x".format(card["mul_paper"]))

    elif t == "RPS":
        label = {"scissors": "剪刀", "rock": "石頭", "paper": "布"}.get(card["rps"], "?")
        print("  出招          ：{}".format(label))

    else:
        print("  ⚠ 卡片尚未寫入資料（Block 9 類型未知：0x{:02X}）".format(
            stats_block[0]))

    print("─────────────────────────────\n")


# ─── 寫入模式 ───────────────────────────────────────────────

def build_character():
    print("\n── 角色卡屬性輸入 ───────────")
    name      = input("  名稱（最多16字元）：")
    hp        = input_int("  HP（1~999）：", 1, 999)
    atk_s     = input_int("  剪刀攻擊力（0~255）：")
    atk_r     = input_int("  石頭攻擊力（0~255）：")
    atk_p     = input_int("  布  攻擊力（0~255）：")
    name_blk  = string_to_block(name)
    stats_blk = encode_stats(TYPE_CHARACTER,
                             hp=hp,
                             atk_scissors=atk_s,
                             atk_rock=atk_r,
                             atk_paper=atk_p)
    return name_blk, stats_blk


def build_skill():
    print("\n── 技能卡屬性輸入 ───────────")
    name     = input("  名稱（最多16字元）：")
    hp_heal  = input_int("  回血量（0~255，不回血填 0）：")
    mul_s    = input_float("  剪刀攻擊倍數（例如 2.0，不加成填 1.0）：")
    mul_r    = input_float("  石頭攻擊倍數：")
    mul_p    = input_float("  布  攻擊倍數：")
    name_blk  = string_to_block(name)
    stats_blk = encode_stats(TYPE_SKILL,
                             hp_heal=hp_heal,
                             mul_scissors=mul_s,
                             mul_rock=mul_r,
                             mul_paper=mul_p)
    return name_blk, stats_blk


def build_rps():
    print("\n── 剪刀石頭布卡屬性輸入 ─────")
    name = input("  名稱（例如：剪刀 A）：")
    while True:
        rps = input("  出招 [s=剪刀 / r=石頭 / p=布]：").strip().lower()
        if rps in ("s", "r", "p"):
            break
        print("  請輸入 s / r / p")
    rps_map  = {"s": "scissors", "r": "rock", "p": "paper"}
    name_blk  = string_to_block(name)
    stats_blk = encode_stats(TYPE_RPS, rps=rps_map[rps])
    return name_blk, stats_blk


def do_write(rdr):
    print("\n  選擇卡牌類型：")
    print("  1. 角色卡 CHARACTER")
    print("  2. 技能卡 SKILL")
    print("  3. 剪刀石頭布卡 RPS")
    choice = input("  輸入數字：").strip()

    if choice == "1":
        name_blk, stats_blk = build_character()
    elif choice == "2":
        name_blk, stats_blk = build_skill()
    elif choice == "3":
        name_blk, stats_blk = build_rps()
    else:
        print("無效選擇，取消寫入")
        return

    uid = wait_for_card(rdr, "\n準備就緒，請將卡片靠近讀卡機...")

    if auth_and_write(rdr, uid, name_blk, stats_blk):
        # 驗證：重新讀回來對比
        uid2 = wait_for_card(rdr, "寫入完成，再靠近一次進行驗證...")
        nb, sb = auth_and_read(rdr, uid2)
        if nb is not None:
            uid_str = "-".join("{:02X}".format(b) for b in uid2[:4])
            card = decode_card(nb, sb, uid_str)
            print("✅ 驗證成功：{} / {} / UID {}".format(
                card.get("name"), card.get("type"), uid_str))
        else:
            print("⚠ 寫入成功但驗證讀取失敗，請手動確認")


# ─── 主選單 ─────────────────────────────────────────────────

def main():
    rdr = init_reader()
    print("\n╔══════════════════════════╗")
    print("║   RFID 卡牌管理工具      ║")
    print("╚══════════════════════════╝")

    while True:
        print("\n  [R] 讀取卡片")
        print("  [W] 寫入卡片")
        print("  [Q] 結束")
        cmd = input("\n請選擇：").strip().upper()

        if cmd == "R":
            do_read(rdr)
        elif cmd == "W":
            do_write(rdr)
        elif cmd == "Q":
            print("結束程式。")
            break
        else:
            print("無效指令")


try:
    main()
except KeyboardInterrupt:
    print("\n中斷。")
