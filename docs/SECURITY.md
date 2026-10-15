# 憑證與已知風險（SECURITY）

定位：記錄「有哪些憑證、各自能幹什麼、**已知風險與目前的決策**」。
改動憑證機制（換帳號、換寄送方式、加新 API key）時，請更新本檔。

> 決策原則：本專案是**單人、單機**的自動化產線，不追求企業級安全，
> 但要**知道自己在接受什麼風險**，並把升級路徑寫下來，而不是留一句「之後再說」。

## 1. 憑證清單（全部在 `.secrets/`，整個目錄不進版控）

| 檔案 | 授予什麼 | 洩漏後能做什麼 | 目前狀態 |
|---|---|---|---|
| `notify.json` | Gmail **應用程式密碼**（SMTP/IMAP 登入） | **讀取＋寄送整個 Gmail 信箱** | 使用**主帳號**；風險 R-001（已接受） |
| `client_secret.json` | OAuth 用戶端身分（Desktop app） | 單獨無效（還需要 token） | 搭配 `yt_token.json` |
| `yt_token.json` | `youtube.upload`（＋`force-ssl`） | 以你的名義**上傳／修改頻道影片** | 首次授權後自動快取 |
| `gumroad_token.txt` | Gumroad API | 以你的名義**建商品／改價／看銷售** | — |

**共同緩解（四項都做）**
- `.secrets/` 整個被 `.gitignore` 排除（`git check-ignore .secrets/notify.json` 可驗證）。
- 權限一律 `chmod 600`（2026-10-15 已把 `yt_token.json` 與一個 `.corrupt-` 殘檔從 644 修正）。
- 專案**不在任何雲端同步資料夾**（iCloud / Dropbox / Google Drive）——已確認。
- 憑證一旦外流，**立即單獨撤銷**（Gmail 應用程式密碼可單獨撤，不影響其他裝置）。

## 2. 已知風險

### R-001：通知使用「主帳號」的應用程式密碼（狀態：**已接受**）

- **記錄日**：2026-10-15
- **決策**：維持主帳號應用程式密碼（評估後接受此風險，不引入 OAuth／專用帳號）。
- **為什麼算風險**：Google 對第三方 App 建議改用「Sign in with Google」，理由是
  **應用程式密碼沒有權限範圍**——它不像 OAuth `gmail.send` 只能寄信，而是
  **整個信箱的鑰匙**（可讀＋可寄）。信箱是其他服務的密碼重設入口，
  因此可能連鎖波及 Gumroad、YouTube、甚至金融帳號。
- **「單人本機」擋掉／擋不掉什麼**：
  - 擋掉：別人共用這台電腦、有人翻你的檔案。
  - **擋不掉**：(1) 這台機器上跑的**第三方程式**（ComfyUI custom node、`pip` 套件、
    下載回來的腳本）都以你的身分執行，讀得到 `.secrets/`；
    (2) **備份**（Time Machine 等）也會一併帶走憑證。
- **目前緩解**：見 §1 的四項（600 + gitignore + 不雲端同步 + 可即時撤銷），且僅本機使用。
- **升級路徑（要做時才做）**：
  - **A. 專用通知信箱**（成本最低，程式不動）：另開一個只做通知的 Gmail，
    `username`＝該帳號、`to`＝你的主信箱。外洩只影響空殼信箱。
    步驟見 `.secrets/README.md` §1。
  - **B. OAuth + Gmail API `gmail.send`**：權限僅「寄信」，不能讀；
    但需接 Gmail API（`google-api-python-client`，走 `.venv`），且 consent screen 在
    **Testing 模式會讓 refresh token 每 7 天過期**（要不掛就得把 app 發佈成「正式」，
    會顯示未驗證警告）。通知層可插拔（`notify.py` 的 `CHANNELS`），真要換再開一個 channel。
- **重啟這個決策的時機**（出現任一項就來重新評估）：
  - 這台機器開始跑更多來路不明的第三方程式；
  - `.secrets/` 權限被放寬、或被移進同步／共用資料夾；
  - 憑證疑似外洩（例如貼到對話、截圖、寄出）；
  - 主信箱開始被其他重要服務（金融、平台後台）當作唯一救援入口。

### R-002（低）：`catalog/runs.jsonl` 等紀錄會進版控

- `catalog/*.jsonl` 是刻意進版控的紀錄（含 prompt/seed/狀態），**不含憑證**。
- 注意：不要把任何 token／密碼寫進 sidecar、manifest、`publish/*.json` 或事件流
  （這些可能被 commit）。憑證一律只放 `.secrets/`。

## 3. 變更紀錄

| 日期 | 變更 |
|---|---|
| 2026-10-15 | 建立本檔；記錄 R-001（主帳號應用程式密碼，已接受）；修正 `.secrets/` 權限至 600 |
