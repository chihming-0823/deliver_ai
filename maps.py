# -*- coding: utf-8 -*-
"""
modules/maps.py — v6.2.1-revA
回到 v6.2.1 的 Distance Matrix 取距離/時間邏輯；僅做極簡清理與詳細 log。
"""

import os
import re
import logging
from typing import Tuple
from urllib.parse import urlencode

import requests

logger = logging.getLogger(__name__)
if not logger.handlers:
    logger.setLevel(logging.INFO)
    fh = logging.FileHandler("delivery_ai.log", encoding="utf-8")
    fmt = logging.Formatter("%(asctime)s - %(levelname)s - [%(name)s] %(message)s")
    fh.setFormatter(fmt)
    logger.addHandler(fh)

_FULL2HALF = str.maketrans({"，": ",", "：": ":", "；": ";", "（": "(", "）": ")", "　": " "})
_MULTI_COMMA = re.compile(r"\s*,\s*")

def normalize_address(addr: str) -> str:
    s = (addr or "").translate(_FULL2HALF).strip()
    s = _MULTI_COMMA.sub(",", s)
    s = re.sub(r"\s+", " ", s).strip(", ").strip()
    return s

def get_distance_duration(origin: str, destination: str) -> Tuple[float, float]:
    api_key = os.getenv("GOOGLE_MAPS_API_KEY", "")
    if not api_key:
        logger.error("[maps] ❌ 缺少 GOOGLE_MAPS_API_KEY")
        return 0.0, 0.0

    o = normalize_address(origin)
    d = normalize_address(destination)
    logger.info(f"[maps] 📍 查詢距離：{o} → {d}")

    params = {
        "origins": o,
        "destinations": d,
        "mode": "driving",
        "language": "zh-TW",
        "units": "metric",
        "key": api_key,
    }
    url = "https://maps.googleapis.com/maps/api/distancematrix/json?" + urlencode(params)

    try:
        r = requests.get(url, timeout=12)
        data = r.json()
    except Exception as e:
        logger.error(f"[maps] REQUEST_FAIL: {e}")
        return 0.0, 0.0

    if data.get("status") != "OK":
        logger.error(f"[maps] API_STATUS: {data.get('status')}")
        return 0.0, 0.0

    rows = data.get("rows", [])
    if not rows or not rows[0].get("elements"):
        logger.error("[maps] EMPTY_ELEMENTS")
        return 0.0, 0.0

    el = rows[0]["elements"][0]
    if el.get("status") != "OK":
        logger.error(f"[maps] ELEMENT_STATUS: {el.get('status')}")
        return 0.0, 0.0

    km = round(el["distance"]["value"] / 1000.0, 2)
    mins = round(el["duration"]["value"] / 60.0, 1)
    logger.info(f"[maps] ✅ 成功：{o} → {d} = {km} 公里 / {mins} 分鐘")
    return km, mins
