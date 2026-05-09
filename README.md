# 台股 AI 分析儀表板

純前端、單檔 HTML 的台股自選股 + AI 進場分析工具。

## 功能

- 自選股管理（localStorage 持久化）
- 即時股價（FinMind API）
- 近 3 個月走勢圖（Chart.js）
- 自動計算 MA5/MA20/MA60、RSI(14)
- AI 深度分析（優勢、劣勢、技術面、進場建議）
- 支援 Groq（推薦）或 Gemini API，自動偵測

## 使用

開啟 [index.html](./index.html) 或前往 GitHub Pages 網址。

## API Key

- **Groq（推薦）**：[console.groq.com/keys](https://console.groq.com/keys) 取得 `gsk_` 開頭的免費 Key
- **Gemini**：[aistudio.google.com/apikey](https://aistudio.google.com/apikey) 取得 `AIza` 開頭的 Key

Key 僅存於使用者瀏覽器 localStorage，不會上傳。

## 免責聲明

本工具的分析僅供參考，不構成任何投資建議。投資人應自行判斷風險。
