# -*- coding: utf-8 -*-
"""
modules/postal_lookup.py — v6.2.3-patch2
在 v6.2.2 架構上增強：
1. compose_clean_address() 新增：
   - 移除前導逗號、郵遞區號、台灣/臺灣字樣。
   - 修正門牌號碼連寫或分隔（367,369 → 367號）。
2. 保留原先 _load_zip_db()、normalize_address()、pick_best_addr() 等結構。
"""

import os
import re
import pandas as pd
import logging

# ───────────────────────────────────────────────
# Logger
# ───────────────────────────────────────────────
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# ───────────────────────────────────────────────
# 郵遞區號資料庫快取
# ───────────────────────────────────────────────
ZIP_DF = None

def _load_zip_db() -> pd.DataFrame:
    """載入 data/zipcodes.xlsx"""
    global ZIP_DF
    if ZIP_DF is not None:
        return ZIP_DF

    data_path = os.path.join("data", "zipcodes.xlsx")
    if not os.path.exists(data_path):
        logger.error(f"zipcodes.xlsx 不存在於 {data_path}")
        raise FileNotFoundError(f"缺少郵遞區號資料表 {data_path}")

    try:
        df = pd.read_excel(data_path, dtype=str)
        df.columns = [c.strip().upper() for c in df.columns]
        ZIP_DF = df
        logger.info(f"zipcodes.xlsx 已載入，共 {len(df)} 筆資料")
        return df
    except Exception as e:
        logger.error(f"讀取 zipcodes.xlsx 失敗：{e}")
        raise

# ───────────────────────────────────────────────
# 地址正規化
# ───────────────────────────────────────────────
def normalize_address(addr: str) -> str:
    if not addr:
        return ""
    addr = re.sub(r"[\s\t]+", "", addr)
    addr = addr.replace("臺", "台").replace("　", "")
    addr = re.sub(r"[：:]", "", addr)
    return addr

def compose_clean_address(addr: str) -> str:
    """補全城市與郵遞區號，並清理 OCR 錯誤格式"""
    addr = normalize_address(addr)
    if not addr or "辨識中" in addr:
        return addr

    # 1. 清除前導符號與郵遞區號殘影
    addr = re.sub(r"^[,，、\s]+", "", addr)
    addr = re.sub(r"^\d{3,5}\s*", "", addr)
    addr = addr.replace("台灣", "").replace("臺灣", "")

    # 2. 修正門牌號碼連寫或分隔（367,369 → 367號）
    addr = re.sub(r"(\d+)[,、／/\.]\s*(\d+)\s*號", r"\1號", addr)

    # 3. 若同時有城市重複（例如「桃園市桃園市蘆竹區」）則去重
    addr = re.sub(r"(台北市|新北市|桃園市|台中市|台南市|高雄市)"
                  r"\1", r"\1", addr)

    # 4. 從郵遞區號表比對補全
    df = _load_zip_db()
    hit = df[df["ROAD"].apply(lambda r: isinstance(r, str) and r in addr)]
    if not hit.empty:
        city = hit.iloc[0]["CITY"]
        zipc = hit.iloc[0]["ZIPCODE"]
        if city and city not in addr:
            return f"{city}{addr}"
        return addr
    return addr

def enrich_address(addr: str) -> str:
    """若缺城市，嘗試從道路比對補上"""
    if not addr:
        return addr
    df = _load_zip_db()
    for _, row in df.iterrows():
        if isinstance(row["ROAD"], str) and row["ROAD"] in addr:
            city = row.get("CITY", "")
            if city and city not in addr:
                return f"{city}{addr}"
    return addr

# ───────────────────────────────────────────────
# 地址相似性比對（給 OCR 輔助）
# ───────────────────────────────────────────────
_TW_CITY = r"(台北市|新北市|桃園市|台中市|台南市|高雄市|基隆市|新竹市|嘉義市|新竹縣|苗栗縣|彰化縣|南投縣|雲林縣|嘉義縣|屏東縣|宜蘭縣|花蓮縣|台東縣)"
_TW_ROAD = r"[^\s\d]+(?:路|街|大道|巷|弄)[^,\s]*"
_ADDR_LIKE = re.compile(rf"{_TW_CITY}|{_TW_ROAD}|(\d+號)")

def is_addr_like(s: str) -> bool:
    s = normalize_address(s)
    return bool(_ADDR_LIKE.search(s))

def pick_best_addr(lines: list[str]) -> str:
    """從多行中選出最可能的地址，支援 (X)/(O) 開頭"""
    scored = []
    for line in lines:
        s = normalize_address(line)
        score = 0
        if re.match(r"^\(?[XO]\)", s):
            score += 4
        if re.search(_TW_CITY, s):
            score += 3
        if re.search(_TW_ROAD, s):
            score += 3
        if re.search(r"\d+號", s):
            score += 2
        if re.search(r"\d{3,6}", s):
            score += 1
        scored.append((score, s))
    scored.sort(reverse=True)
    return scored[0][1] if scored and scored[0][0] >= 3 else ""

# ───────────────────────────────────────────────
# 郵遞區號模糊比對補全
# ───────────────────────────────────────────────
def fuzzy_match_city(addr: str) -> str:
    """當 OCR 缺城市時，嘗試從郵遞區號表模糊補上"""
    if not addr:
        return addr
    df = _load_zip_db()
    for _, row in df.iterrows():
        if isinstance(row["ROAD"], str) and row["ROAD"][:2] in addr:
            return f"{row['CITY']}{addr}"
    return addr
