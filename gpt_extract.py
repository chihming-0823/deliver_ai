# -*- coding: utf-8 -*-
"""
modules/gpt_extract.py — v6.1p 無 Emoji 安全版
用途：
    第一層 GPT 辨識模組
    - 接收 base64 圖片字串
    - 呼叫 OpenAI Chat Completions API
    - 解析平台、金額、取餐地址、送達地址
編碼：
    UTF-8 (No BOM)
"""

import os
import json
import requests

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()

def analyze_delivery_image(b64_image: str) -> dict:
    """
    使用 OpenAI API 辨識外送截圖內容。
    參數：
        b64_image (str): 圖片的 base64 字串
    回傳：
        dict(platform, amount, pickup_address, dropoff_address)
    """
    if not OPENAI_API_KEY:
        raise RuntimeError("未設定 OPENAI_API_KEY 環境變數")

    system_prompt = (
        "你是一個外送訂單辨識助手。"
        "請從圖片中精準擷取以下四項資訊：平台（Foodpanda / Uber Eats / Unknown）、金額、取餐地址、送達地址。"
        "請輸出嚴格 JSON，鍵名固定且全為小寫："
        "{"
        '"platform": "Foodpanda", '
        '"amount": 84.5, '
        '"pickup_address": "桃園市蘆竹區南崁路一段114號", '
        '"dropoff_address": "桃園市蘆竹區忠孝西路101號"'
        "}"
        "不要包含任何其他說明文字。若某欄無法辨識請留空字串。"
    )

    payload = {
        "model": "gpt-5",
        "messages": [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "以下是外送截圖，base64："},
                    {"type": "text", "text": b64_image}
                ]
            }
        ],
        "temperature": 0.0,
        "response_format": {"type": "json_object"}
    }

    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json"
    }

    try:
        res = requests.post("https://api.openai.com/v1/chat/completions", headers=headers, json=payload, timeout=90)
    except Exception as e:
        raise RuntimeError(f"呼叫 OpenAI API 失敗：{e}")

    if res.status_code != 200:
        raise RuntimeError(f"OpenAI API 回應錯誤 ({res.status_code})：{res.text[:300]}")

    try:
        result_json = res.json()
        content = result_json["choices"][0]["message"]["content"]
        data = json.loads(content)
    except Exception as e:
        raise RuntimeError(f"無法解析 OpenAI 回傳內容：{e}，原始內容={res.text[:400]}")

    # 統一輸出格式
    return {
        "platform": str(data.get("platform", "") or "Unknown").strip(),
        "amount": data.get("amount", 0),
        "pickup_address": str(data.get("pickup_address", "") or "").strip(),
        "dropoff_address": str(data.get("dropoff_address", "") or "").strip(),
    }
