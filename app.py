# -*- coding: utf-8 -*-
"""
app.py — v6.2.1 Address Picker Fix + Dual-Channel Blob + Full Logging
修正：OCR 取餐/送達地址抽取邏輯，避免兩者重複；加入候補策略與詳細 log。
"""

import os
import io
import re
import json
import sqlite3
import logging
from typing import Tuple, List, Optional
from flask import Flask, request, jsonify
from PIL import Image
import pytesseract
import requests

# ───────────────────────────────────────────────
# LINE Bot SDK (v3.x)
# ───────────────────────────────────────────────
from linebot.v3.webhook import WebhookHandler, MessageEvent
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    MessagingApiBlob,
    ReplyMessageRequest,
    TextMessage,
)
from linebot.v3.webhooks import TextMessageContent, ImageMessageContent

LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET", "")
LINE_ENABLED = bool(LINE_CHANNEL_ACCESS_TOKEN and LINE_CHANNEL_SECRET)

# ───────────────────────────────────────────────
# Internal modules
# ───────────────────────────────────────────────
from modules.maps import get_distance_duration
from modules.postal_lookup import compose_clean_address, normalize_address

# ───────────────────────────────────────────────
# Logging（檔案 + 主控台）
# ───────────────────────────────────────────────
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
if not any(isinstance(h, logging.FileHandler) for h in logger.handlers):
    fh = logging.FileHandler("delivery_ai.log", encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
    logger.addHandler(fh)
if not any(isinstance(h, logging.StreamHandler) for h in logger.handlers):
    sh = logging.StreamHandler()
    sh.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    logger.addHandler(sh)

# ───────────────────────────────────────────────
# Flask
# ───────────────────────────────────────────────
app = Flask(__name__)
app.config["JSON_AS_ASCII"] = False
DB_PATH = "delivery_ai.db"

# ───────────────────────────────────────────────
# 工具
# ───────────────────────────────────────────────
def check_blacklist(text: str) -> str:
    try:
        if not os.path.exists(DB_PATH):
            return "資料庫不存在"
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT keyword FROM blacklist")
        rows = c.fetchall()
        conn.close()
        text_l = text.lower()
        matched = [kw for (kw,) in rows if kw and kw.strip() and kw.lower() in text_l]
        return "、".join(matched) if matched else "未命中"
    except Exception as e:
        logger.error(f"check_blacklist 例外：{e}")
        return "檢查失敗"

def extract_amount(ocr_text: str) -> float:
    if not ocr_text:
        return 0.0
    t = ocr_text.replace(",", "")
    m = re.search(r"\$?\s*([0-9]+\.[0-9]{1,2})\s*\$?", t)
    if m:
        return float(m.group(1))
    m = re.search(r"(?:NT\$|NTD|\$)?\s*([0-9]{2,5})\s*(?:元|塊|NT\$|NTD)?", t)
    return float(m.group(1)) if m else 0.0

def detect_platform(ocr_text: str) -> str:
    if re.search(r"\$?\s*\d+\.\d{1,2}\s*\$?", ocr_text):
        return "Foodpanda"
    if re.search(r"Uber\s*Eats|ubereats", ocr_text, flags=re.I):
        return "Uber Eats"
    if "送餐資訊" in ocr_text:
        return "Foodpanda"
    return "未知平台"

_TW_CITY = r"(台北市|新北市|桃園市|台中市|台南市|高雄市|基隆市|新竹市|嘉義市|新竹縣|苗栗縣|彰化縣|南投縣|雲林縣|嘉義縣|屏東縣|宜蘭縣|花蓮縣|台東縣|澎湖縣|連江縣|金門縣)"
_TW_ROAD = r"[^\s\d]+(?:路|街|大道|巷|弄)[^,\s]*"
_ADDR_LIKE = re.compile(rf"{_TW_CITY}|{_TW_ROAD}|(\d+號)")

def _is_addr_like(s: str) -> bool:
    s2 = normalize_address(s)
    return bool(_ADDR_LIKE.search(s2))

def _cleanup_line(s: str) -> str:
    s = s.strip()
    s = s.replace("：", ":").replace("公司:", "")  # 去掉常見干擾前綴
    s = re.sub(r"\s+", " ", s)
    return s

def _pick_best_addr(cands: List[str]) -> Optional[str]:
    """在候選行中挑最像地址的一行；若無則 None"""
    scored = []
    for c in cands:
        s = normalize_address(_cleanup_line(c))
        score = 0
        if re.search(_TW_CITY, s): score += 3
        if re.search(_TW_ROAD, s): score += 3
        if re.search(r"\d+號", s): score += 2
        if re.search(r"\d{3,6}", s): score += 1  # 郵遞區或門牌數字
        scored.append((score, s))
    scored.sort(reverse=True)
    return scored[0][1] if scored and scored[0][0] >= 3 else None

def _are_same_addr(a: str, b: str) -> bool:
    sa = re.sub(r"[^一-龥a-zA-Z0-9]", "", normalize_address(a))
    sb = re.sub(r"[^一-龥a-zA-Z0-9]", "", normalize_address(b))
    return bool(sa) and sa == sb

def extract_addresses(ocr_text: str) -> Tuple[str, str]:
    """
    改良版：明確以「送餐資訊」區塊抽 dropoff；
    取餐地址優先用 (O) 行與其後 1-3 行；若缺，再用「送餐資訊」之前最後一個地址樣式行。
    若兩者相同，啟動候補策略避免重複。
    """
    if not ocr_text:
        return "辨識中/無法擷取", "辨識中/無法擷取"

    raw_lines = [ln for ln in ocr_text.splitlines()]
    lines = [ln for ln in (l.strip() for l in raw_lines) if ln.strip()]
    logger.info(f"[ADDR] OCR 行數：{len(lines)}")
    logger.info(f"[ADDR] 片段預覽：{lines[:8]}")

    # 1) 找送餐資訊區塊作為 dropoff
    idx_drop_hdr = -1
    for i, ln in enumerate(lines):
        if "送餐資訊" in ln.replace(" ", ""):
            idx_drop_hdr = i
            break

    drop = None
    if idx_drop_hdr >= 0:
        cand = lines[idx_drop_hdr + 1 : idx_drop_hdr + 5]
        logger.info(f"[ADDR] 送餐資訊候選：{cand}")
        drop = _pick_best_addr(cand)

        # 若 pick_best 失敗，合併看看
        if not drop:
            merged = normalize_address(_cleanup_line("".join(cand)))
            if _is_addr_like(merged):
                drop = merged

    # 2) 取餐地址：優先 (O) 起始行之後 1-3 行
    pick = None
    idx_o = -1
    for i, ln in enumerate(lines):
        if ln.strip().startswith("(O)") or ln.strip().startswith("O)"):
            idx_o = i
            break
    if idx_o >= 0:
        cand = lines[idx_o + 1 : idx_o + 4]
        logger.info(f"[ADDR] 取餐(O)候選：{cand}")
        pick = _pick_best_addr(cand)
        if not pick:
            # (O) 行本身有時包含地址
            if _is_addr_like(lines[idx_o]):
                pick = normalize_address(_cleanup_line(lines[idx_o]))

    # 3) 若還沒有 pick，用「送餐資訊之前」最後一個像地址的行
    if not pick and idx_drop_hdr > 0:
        back = [ln for ln in lines[:idx_drop_hdr] if _is_addr_like(ln)]
        if back:
            pick = normalize_address(_cleanup_line(back[-1]))
            logger.info(f"[ADDR] 取餐回溯候選：{back[-3:]} -> {pick}")

    # 4) 若還沒有 drop，從「送餐資訊之後」挑第一個像地址的行
    if not drop:
        fwd = [ln for ln in lines[idx_drop_hdr + 1 :]] if idx_drop_hdr >= 0 else lines
        fwd = [ln for ln in fwd if _is_addr_like(ln)]
        if fwd:
            drop = normalize_address(_cleanup_line(fwd[0]))
            logger.info(f"[ADDR] 送達回溯候選：{fwd[:3]} -> {drop}")

    # 5) 最終保底
    if not pick:
        pick = "辨識中/無法擷取"
    if not drop:
        drop = "辨識中/無法擷取"

    # 6) 去重複策略：若相同，嘗試替換另一候補
    if _are_same_addr(pick, drop):
        logger.warning(f"[ADDR] 取餐/送達相同，啟動去重複策略：{pick}")
        # 嘗試：送達改用送餐資訊塊中的次佳
        if idx_drop_hdr >= 0:
            cand = lines[idx_drop_hdr + 1 : idx_drop_hdr + 6]
            # 去掉與 pick 相同者
            alt = [c for c in cand if not _are_same_addr(c, pick)]
            drop2 = _pick_best_addr(alt)
            if drop2 and not _are_same_addr(drop2, pick):
                drop = drop2
        # 若仍相同，嘗試取餐用 (O) 前一個地址行
        if _are_same_addr(pick, drop):
            left = [ln for ln in lines[: max(idx_o, 0)] if _is_addr_like(ln)]
            if left:
                pick2 = normalize_address(_cleanup_line(left[-1]))
                if not _are_same_addr(pick2, drop):
                    pick = pick2

        # 仍相同就只保留送達，取餐標記
        if _are_same_addr(pick, drop):
            pick = "辨識中/無法擷取(疑同送達)"

    logger.info(f"[ADDR] 最終取餐：{pick}")
    logger.info(f"[ADDR] 最終送達：{drop}")
    return pick, drop

def ocr_image_bytes(image_bytes: bytes) -> str:
    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        text = pytesseract.image_to_string(img, lang="chi_tra+eng")
        text = re.sub(r"[ \t]+", " ", text).replace("臺", "台")
        logger.info(f"OCR 擷取完成（{len(text)}字）")
        return text
    except Exception as e:
        logger.error(f"OCR 失敗：{e}")
        return ""

def build_report(platform, amount, pickup, dropoff, dist_km, dur_min, bl) -> str:
    earning_per_km = round(amount / dist_km, 2) if dist_km > 0 else 0.0
    threshold = 15.0
    suggestion = "✅ 收益良好，建議接單" if earning_per_km >= threshold else f"⚠️ 低於門檻 ({threshold} 元/km)，建議拒單"
    return (
        f"【平台】：{platform}\n"
        f"【金額】：${amount}\n"
        f"【取餐地址】：{pickup}\n"
        f"【送達地址】：{dropoff}\n"
        f"【距離】：{dist_km:.2f} 公里\n"
        f"【耗時】：約 {dur_min:.1f} 分鐘\n"
        f"【黑名單】：{bl}\n"
        f"【每公里收益】：{earning_per_km} 元/km\n"
        f"【建議】：{suggestion}"
    )

# ───────────────────────────────────────────────
# Routes
# ───────────────────────────────────────────────
@app.route("/test", methods=["GET"])
def test():
    return jsonify({"ok": True, "msg": "delivery_ai v6.2.1 running"})

# ───────────────────────────────────────────────
# LINE Webhook
# ───────────────────────────────────────────────
if LINE_ENABLED:
    handler = WebhookHandler(LINE_CHANNEL_SECRET)
    configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
    api_client = ApiClient(configuration)
    msg_api = MessagingApi(api_client)
    blob_api = MessagingApiBlob(api_client)

    @app.route("/callback", methods=["POST"])
    def callback():
        signature = request.headers.get("X-Line-Signature", "")
        body = request.get_data(as_text=True)
        handler.handle(body, signature)
        return "OK", 200

    @handler.add(MessageEvent, message=ImageMessageContent)
    def on_image(event):
        logger.info(f"[LINE] 收到圖片事件 id={event.message.id}")
        image_bytes = b""

        # 通道 A：SDK 嘗試
        try:
            resp = blob_api.get_message_content(message_id=event.message.id)
            if hasattr(resp, "content") and resp.content:
                image_bytes = resp.content
            elif hasattr(resp, "read"):
                image_bytes = resp.read()
            elif hasattr(resp, "iter_bytes"):
                for chunk in resp.iter_bytes():
                    image_bytes += chunk
            elif hasattr(resp, "iter_content"):
                for chunk in resp.iter_content(chunk_size=4096):
                    image_bytes += chunk
        except Exception as e:
            logger.warning(f"SDK 通道錯誤：{e}")

        # 通道 B：HTTP API 備援
        if not image_bytes:
            try:
                url = f"https://api-data.line.me/v2/bot/message/{event.message.id}/content"
                headers = {"Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"}
                with requests.get(url, headers=headers, stream=True, timeout=30) as r:
                    r.raise_for_status()
                    for chunk in r.iter_content(chunk_size=8192):
                        image_bytes += chunk
                logger.info("[LINE] HTTP 通道成功下載圖片")
            except Exception as e:
                logger.error(f"HTTP 通道錯誤：{e}")

        logger.info(f"[LINE] 影像 bytes 取得：{len(image_bytes)}")
        if not image_bytes:
            msg_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text="⚠️ 讀取影像失敗（來源無內容）。請再傳一次。")]
                )
            )
            return

        # 正常流程：OCR → 抽地址 → 正規化 → Maps → 報告
        ocr_text = ocr_image_bytes(image_bytes)
        platform = detect_platform(ocr_text)
        amount = extract_amount(ocr_text)
        pickup, dropoff = extract_addresses(ocr_text)

        pick_c = compose_clean_address(pickup) if "辨識中" not in pickup else pickup
        drop_c = compose_clean_address(dropoff) if "辨識中" not in dropoff else dropoff

        # 若兩者仍判定相同，直接回報並避免發送 0 距離誤導
        if (isinstance(pick_c, str) and isinstance(drop_c, str) and pick_c == drop_c) or \
           (isinstance(pick_c, str) and "辨識中" in pick_c) and (isinstance(drop_c, str) and "辨識中" in drop_c):
            logger.warning("[MAPS] 取餐/送達仍相同或皆未知，跳過距離計算。")
            dist, dur = 0.0, 0.0
        else:
            dist, dur = get_distance_duration(pick_c, drop_c)

        bl = check_blacklist(ocr_text + " " + pickup + " " + dropoff)
        report = build_report(platform, amount, pickup, dropoff, dist, dur, bl)

        logger.info(f"[LINE] 成功分析：{pickup} → {dropoff} = {dist}km / {dur}min")
        msg_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=report)]
            )
        )

else:
    @app.route("/callback", methods=["POST"])
    def callback_disabled():
        return "LINE disabled", 200

# ───────────────────────────────────────────────
# Main
# ───────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    logger.info("✅ Flask 啟動 (v6.2.1 Address Picker Fix)")
    app.run(host="0.0.0.0", port=port, debug=False)
