# card_utils.py — 卡牌編解碼共用函式

# Block 配置
BLOCK_NAME  = 8   # Sector 2, Block 0 → 儲存卡牌名稱 (16 bytes UTF-8)
BLOCK_STATS = 9   # Sector 2, Block 1 → 儲存卡牌屬性 (16 bytes)
KEY = [0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF]

# 卡牌類型
TYPE_CHARACTER = 0x01
TYPE_SKILL     = 0x02
TYPE_RPS       = 0x03

# RPS 對照
RPS_TO_BYTE = {"scissors": 0x01, "rock": 0x02, "paper": 0x03}
BYTE_TO_RPS = {0x01: "scissors", 0x02: "rock", 0x03: "paper"}

# Block 9 編碼格式：
#   CHARACTER : [0x01, HP_high, HP_low, atk_scissors, atk_rock, atk_paper, 0…]
#   SKILL     : [0x02, hp_heal, mul_scissors×10, mul_rock×10, mul_paper×10, 0…]
#   RPS       : [0x03, rps_byte, 0…]


def string_to_block(text):
    """字串 → 16 bytes list（UTF-8，不足補空白）"""
    b = bytearray(text.encode("utf-8"))
    if len(b) > 16:
        b = b[:16]
    else:
        b.extend([0x20] * (16 - len(b)))
    return list(b)


def block_to_string(block):
    """16 bytes list → 字串（去尾部空白）"""
    try:
        return bytes(block).decode("utf-8").rstrip()
    except Exception:
        return ""


def encode_stats(card_type, **kw):
    """卡牌屬性 → 16 bytes list"""
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
        buf[1] = int(kw.get("hp_heal", 0))
        buf[2] = int(round(kw.get("mul_scissors", 1.0) * 10))
        buf[3] = int(round(kw.get("mul_rock", 1.0) * 10))
        buf[4] = int(round(kw.get("mul_paper", 1.0) * 10))

    elif card_type == TYPE_RPS:
        buf[1] = RPS_TO_BYTE.get(kw.get("rps", ""), 0x00)

    return buf


def decode_card(name_block, stats_block, uid_str):
    """name block + stats block → card dict（給 JSON 用）"""
    name = block_to_string(name_block)
    t = stats_block[0]

    if t == TYPE_CHARACTER:
        hp = (stats_block[1] << 8) | stats_block[2]
        return {
            "type": "CHARACTER",
            "uid": uid_str,
            "name": name,
            "hp": hp,
            "atk_scissors": stats_block[3],
            "atk_rock": stats_block[4],
            "atk_paper": stats_block[5],
        }

    elif t == TYPE_SKILL:
        return {
            "type": "SKILL",
            "uid": uid_str,
            "name": name,
            "hp_heal": stats_block[1],
            "mul_scissors": stats_block[2] / 10,
            "mul_rock": stats_block[3] / 10,
            "mul_paper": stats_block[4] / 10,
        }

    elif t == TYPE_RPS:
        return {
            "type": "RPS",
            "uid": uid_str,
            "name": name,
            "rps": BYTE_TO_RPS.get(stats_block[1], "unknown"),
        }

    else:
        return {"type": "UNKNOWN", "uid": uid_str}
