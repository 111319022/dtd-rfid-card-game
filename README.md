# RFID Card Game

這是一個以 **ESP32 + MFRC522 讀卡機** 搭配純前端網頁實作的 RFID 卡牌桌遊。玩家將實體 NFC/RFID 卡片靠近讀卡機，透過「剪刀石頭布」機制進行雙人對戰。卡片資料（角色、技能、出拳）直接寫入 Mifare Classic 卡片，網頁與 ESP32 之間以 **Web Serial API** 進行即時通訊。

> **瀏覽器限制：本專案使用 Web Serial API，僅支援 Google Chrome 及 Microsoft Edge（版本 89+）。**  
> 不支援 Firefox、Safari 及其他瀏覽器。

---
# 開發團隊

### 軟體與系統開發
| 成員 | 分工 |  |
| :--- | :--- | :--- |
| **[徐丞睿 (Raaay)](https://github.com/111319022)** | **韌體與系統整合**<br>ESP32 MicroPython 開發、硬體與瀏覽器通訊橋接、RFID 讀卡系統設計與硬體焊接 | `MicroPython` `Firmware` `Hardware` |
| **[賴昱凱 (鈺奶)](https://github.com/yunai87)** | **遊戲機制與數值**<br>核心戰鬥邏輯實作、卡片數值平衡規劃與遊戲演算法開發 | `JavaScript` `Game Logic` `Math` |
| **[王宸祐 (LAS)](https://github.com/LAS0324)** | **UI/UX 介面設計**<br>前端網頁視覺優化、使用者操作流程規劃與介面開發 | `Frontend` `UI/UX` `Web Design` |

### 視覺與工業設計
| 成員 | 分工 |  |
| :--- | :--- | :--- |
| **鍾尚哲 (Cucu)** | **產品結構與工業設計**<br>實體外殼木工建模與製造、產品組裝整合、外觀質感規劃 | `Industrial Design` `Fabrication` |
| **宋懿宸** | **視覺美術**<br>卡片原畫設計、角色視覺設定、遊戲美術風格統籌 | `Illustration` `Game Art` |

---

## 系統架構

```
實體 RFID 卡片
      │  感應
      ▼
  ESP32 + MFRC522
      │  USB (Serial 115200 baud)
      │  line-delimited JSON
      ▼
 Web Serial API
      │
      ▼
  瀏覽器網頁 (index.html)
  ├── serial.js   — 連線 / 收發封裝
  ├── battle.js   — 對戰狀態機
  └── style.css
```

---

## 硬體需求

| 元件 | 說明 |
|------|------|
| ESP32 開發板 | 任意型號，需支援 MicroPython |
| MFRC522 RFID 模組 | 使用 SPI 介面 |
| Mifare Classic 1K 卡片 | 作為角色卡、技能卡、出拳卡、啟動卡、設定卡 |
| USB 傳輸線 | 連接 ESP32 與電腦 |

### ESP32 腳位對應

| MFRC522 | ESP32 GPIO |
|---------|-----------|
| SCK     | 18        |
| MOSI    | 23        |
| MISO    | 19        |
| RST     | 22        |
| SDA/CS  | 5         |

內建 LED：GPIO 2　/　外接 LED：GPIO 4（與內建 LED 同步亮滅）

---

## 安裝與部署

### 1. 燒錄 ESP32 韌體

1. 安裝 [MicroPython](https://micropython.org/download/ESP32_GENERIC/) 至 ESP32。
2. 使用 Thonny 或 `mpremote` 將以下檔案上傳至 ESP32 根目錄：
   - `ESP32 py/main.py`
   - `ESP32 py/mfrc522.py`
3. 重啟 ESP32，`main.py` 會自動執行並等待連線。

### 2. 開啟網頁

直接以 Chrome 或 Edge 開啟 `web/index.html`（本機檔案即可，無需伺服器）。

---

## Web Serial API 連線流程

> **注意：必須使用 Chrome 或 Edge 瀏覽器，否則 Web Serial API 不可用。**

```
使用者點擊「連接 ESP32」
        │
        ▼
瀏覽器彈出串列埠選擇視窗
（選取 ESP32 對應的 COM 埠 / /dev/tty...）
        │
        ▼
navigator.serial.requestPort()
        │
        ▼
port.open({ baudRate: 115200 })
        │
        ▼
建立 TextDecoderStream 讀取迴圈
以換行符號 \n 分割訊息
        │
        ▼
ESP32 回傳 {"event":"ready", "activations":[...], "settings":[...]}
        │
        ▼
連線成功，狀態列顯示「已連接」
```

### 通訊格式

所有訊息均為 **換行分隔的 JSON（line-delimited JSON）**，baud rate 115200。

**網頁 → ESP32（指令）**

| cmd | 說明 |
|-----|------|
| `write` | 寫入卡片資料（`name`、`stats`、`force`） |
| `erase` | 清除卡片資料 |
| `cancel` | 取消等待中的寫入 / 清除 |
| `list_activations` | 查詢啟動卡 UID 清單 |
| `set_activations` | 儲存啟動卡 UID 清單 |
| `list_settings` | 查詢設定卡 UID 清單 |
| `set_settings` | 儲存設定卡 UID 清單 |

**ESP32 → 網頁（事件）**

| event | 說明 |
|-------|------|
| `ready` | 開機完成，附帶啟動卡 / 設定卡清單 |
| `read` | 偵測到一般卡片，附帶卡片資料 |
| `activation` | 偵測到啟動卡 |
| `settings_card` | 偵測到設定卡 |
| `waiting` | 等待感應卡片（寫入 / 清除模式） |
| `write` | 寫入結果（`ok: true/false`） |
| `erase` | 清除結果（`ok: true/false`） |
| `card_exists` | 卡片已有資料，等待確認覆寫 |
| `cancelled` | 已取消操作 |
| `error` | 錯誤訊息 |

---

## 卡片類型

### CHARACTER（角色卡）

每位玩家在回合開始前感應，確立對戰角色。

| 欄位 | 說明 | 範圍 |
|------|------|------|
| `name` | 角色名稱（最多 16 bytes） | — |
| `hp` | 生命值 | 1–999 |
| `atk_rock` | 石頭攻擊力 | 0–255 |
| `atk_scissors` | 剪刀攻擊力 | 0–255 |
| `atk_paper` | 布攻擊力 | 0–255 |

### SKILL（技能卡）

每回合可選擇感應一張技能卡，在出拳前套用加成。技能效果（回血 / 扣血）於結果公布時才生效。

| 欄位 | 說明 | 範圍 |
|------|------|------|
| `name` | 技能名稱 | — |
| `hp_heal` | 回血（正值）/ 扣血（負值） | −128–127 |
| `mul_rock` | 石頭傷害倍率 | 0–25.5 |
| `mul_scissors` | 剪刀傷害倍率 | 0–25.5 |
| `mul_paper` | 布傷害倍率 | 0–25.5 |

### RPS（出拳卡）

代表石頭 / 剪刀 / 布的出招，可設定為 P1 專用、P2 專用或通用。

| 欄位 | 說明 |
|------|------|
| `name` | 卡片名稱 |
| `rps` | `rock` / `scissors` / `paper` |
| `player` | `1`=P1 專用、`2`=P2 專用、`0`=通用 |

---

## 對戰流程

```
感應啟動卡 → 進入候機室
        │
再感應啟動卡 → 開始本場
        │
  P1 感應角色卡
        │
  P2 感應角色卡
        │
┌────────────────────────────────────
│ P1【選用】感應技能卡 → 感應出拳卡         
│ P2【選用】感應技能卡 → 感應出拳卡         
│                                      
│        雙方出拳後進入 2 秒隱藏期         
│                ↓                     
│           揭曉雙方出招                 
│          ↓（1.25 秒）                 
│       公布傷害 ＋ 技能效果              
│          ↓（3.2 秒）                  
│     進入下一回合（或戰鬥結束）            
└─────────────────────────────────────
        │
  一方 HP ≤ 0 → 對戰結束
        │
再感應啟動卡 → 開始新局
```

**勝負規則**

- 剪刀 > 布 > 石頭 > 剪刀
- 平手：雙方本回合零傷害
- 雙方同時歸零：平手

**防彈跳**：每張卡片感應後有 1.5 秒冷卻，請下一位玩家換卡時稍候。

---

## 特殊卡片：啟動卡 & 設定卡

| 卡片 | 功能 |
|------|------|
| **啟動卡** | 感應後跳至對戰候機室；對戰中感應第二次可重新開始 |
| **設定卡** | 感應後跳至設定頁面；對戰中需確認兩次才離開 |

UID 清單儲存於 ESP32 的 `activation.json` / `settings.json`，可在設定頁面新增、刪除或透過感應自動登錄。

---

## 設定頁面功能

| 分頁 | 功能 |
|------|------|
| 卡片 UID 設定 | 管理啟動卡與設定卡的 UID 清單，支援手動輸入或感應自動新增 |
| 卡片管理器 | 寫入 / 更新卡片資料，支援角色卡、技能卡、出拳卡；讀取歷史點選即可載入表單 |
| 清除卡片 | 將實體卡片資料歸零（不可復原） |

---

## 檔案結構

```
rfid-card-game/
├── web/
│   ├── index.html      # 主網頁（首頁 / 對戰 / 設定三個 View）
│   ├── battle.js       # 對戰狀態機
│   ├── serial.js       # Web Serial API 封裝 + UI 工具函式
│   └── style.css       # 樣式
└── ESP32 py/
    ├── main.py         # ESP32 主程式（自動執行）
    ├── mfrc522.py      # MFRC522 驅動
    └── card_manager.py # Thonny 互動式卡片讀寫工具（選用）
```
