#!/usr/bin/env node
// 加密報告：AES-256-GCM，密鑰由密碼經 PBKDF2-SHA256 導出。零依賴（Node 內建 WebCrypto）。
// 用法：REPORT_PASSWORD=xxx node encrypt_report.js <輸入檔> <輸出.json>
//       REPORT_PASSWORD=xxx node encrypt_report.js --decrypt <輸入.json>   （自測用，印出明文）
// 瀏覽器端用同參數解密（見 index.html decryptMacroReport）。
const fs = require('fs');
const { webcrypto: c } = require('crypto');
const ITER = 600000;
const b64 = (u) => Buffer.from(u).toString('base64');
const unb64 = (s) => new Uint8Array(Buffer.from(s, 'base64'));

async function key(pw, salt, iter) {
  const base = await c.subtle.importKey('raw', new TextEncoder().encode(pw), 'PBKDF2', false, ['deriveKey']);
  return c.subtle.deriveKey({ name: 'PBKDF2', salt, iterations: iter, hash: 'SHA-256' }, base,
    { name: 'AES-GCM', length: 256 }, false, ['encrypt', 'decrypt']);
}

(async () => {
  const pw = process.env.REPORT_PASSWORD;
  if (!pw || pw.length < 12) { console.error('REPORT_PASSWORD 未設定或少於 12 字元，拒絕加密'); process.exit(2); }
  const args = process.argv.slice(2);
  if (args[0] === '--decrypt') {
    const j = JSON.parse(fs.readFileSync(args[1], 'utf8'));
    const k = await key(pw, unb64(j.salt), j.iter);
    const pt = await c.subtle.decrypt({ name: 'AES-GCM', iv: unb64(j.iv) }, k, unb64(j.ct));
    process.stdout.write(new TextDecoder().decode(pt));
    return;
  }
  const [inp, out] = args;
  const salt = c.getRandomValues(new Uint8Array(16)), iv = c.getRandomValues(new Uint8Array(12));
  const k = await key(pw, salt, ITER);
  const ct = await c.subtle.encrypt({ name: 'AES-GCM', iv }, k, new TextEncoder().encode(fs.readFileSync(inp, 'utf8')));
  fs.writeFileSync(out, JSON.stringify({ v: 1, kdf: 'PBKDF2-SHA256', iter: ITER, salt: b64(salt), iv: b64(iv), ct: b64(new Uint8Array(ct)) }));
  console.log('encrypted →', out);
})().catch((e) => { console.error('失敗：', e.message); process.exit(1); });
