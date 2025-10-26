# -*- coding: utf-8 -*-
"""
modules/analysis.py — v6.2.3-patch1
主要修正：
1. 平台辨識強化：
   - Uber：現金付款、接受、外送(、(公里)、(分鐘)
   - Foodpanda：拒絕、上線中、送餐資訊、取餐地點、$xx.xx
2. 金額提取修正：排除距離/時間數字，優先 $56、$56.00。
3. 回傳報告對齊 app.py 的清理後地址。
4. 無 emoji，UTF-8（No BOM）。
"""

import re
from typing import List, Tuple

# ---------------------------------------------------------------------
# 金額擷取
# ---------------------------------------------------------------------
_NEG_CONTEXT = r"(公里|km|分鐘|min|小時|hr|秒|sec|公 里|分 鐘)"

def extract_amount(ocr_text: str, lo: float = 20.0, hi: float = 300.0) -> float:
    """依金額樣態擷取，排除距離/時間語境。"""
    t = ocr_text or ""

    # 1) $xx.xx 或 $xx
    m = re.search(r"\$\s*(\d+(?:\.\d{2})?)\b", t)
    if m:
        v = float(m.group(1))
        if lo <= v <= hi:
            return v

    # 2) 無 $ 的候補小數
    for m in re.finditer(r"\b(\d+\.\d{1,2})\b", t):
        val = float(m.group(1))
        tail = t[m.end(): m.end() + 24]
        if re.search(_NEG_CONTEXT, tail, flags=re.IGNORECASE):
            continue
        if lo <= val <= hi:
            return val

    # 3) 無 $ 的候補整數
    for m in re.finditer(r"\b(\d{2,3})\b", t):
        val = float(m.group(1))
        tail = t[m.end(): m.end() + 24]
        if re.search(_NEG_CONTEXT, tail, flags=re.IGNORECASE):
            continue
        if lo <= val <= hi:
            return val

    return 0.0


# ---------------------------------------------------------------------
# 平台樣態特徵判斷
# ---------------------------------------------------------------------
def detect_platform(ocr_text: str) -> Tuple[str, List[str]]:
    """依樣態文字推斷平台與特徵。"""
    text = (ocr_text or "").lower()
    feats: List[str] = []
    platform = "未知平台"

    has_dollar_two_dec = bool(re.search(r"\$\s*\d+\.\d{2}\b", text))
    has_dollar_int     = bool(re.search(r"\$\s*\d{2,3}\b", text))
    has_km_paren       = bool(re.search(r"\(\s*\d+(?:\.\d+)?\s*(公里|km)\s*\)", text))
    has_min_paren      = bool(re.search(r"\(\s*\d+\s*(分鐘|min)\s*\)", text))

    # Uber 強特徵
    if has_km_paren or has_min_paren or ("現金付款" in text) or ("接受" in text) or ("外送(" in text):
        feats.append("Uber 強特徵")
        platform = "Uber Eats"

    # Foodpanda 強特徵
    if ("拒絕" in text) or ("上線中" in text) or ("送餐資訊" in text) or ("取餐地點" in text) or has_dollar_two_dec:
        feats.append("Panda 強特徵")
        platform = "Foodpanda"

    # 若仍未知但為整數金額 → 傾向 Uber
    if platform == "未知平台" and has_dollar_int and not has_dollar_two_dec:
        feats.append("金額整數（無小數）")
        platform = "Uber Eats"

    return platform, feats


# ---------------------------------------------------------------------
# 主分析報告
# ---------------------------------------------------------------------
def analyze_order(
    ocr_text: str,
    distance_km: float,
    duration_min: float,
    pickup_addr: str,
    dropoff_addr: str,
    blacklist_result: str = "未命中",
) -> str:
    platform, features = detect_platform(ocr_text)
    amount = extract_amount(ocr_text)
    earning_per_km = round(amount / distance_km, 2) if distance_km > 0 else 0.0
    threshold = 15.0 if platform == "Foodpanda" else 13.0 if platform == "Uber Eats" else 15.0

    if distance_km <= 0 or duration_min <= 0:
        suggestion = "資訊不足（地址或距離未取到），請再確認後判斷"
    elif amount > 0 and earning_per_km >= threshold:
        suggestion = "收益良好，建議接單"
    else:
        suggestion = f"低於門檻（{threshold} 元/km），建議拒單"

    feature_text = "、".join(features) if features else "無明顯樣態"

    report = (
        f"【平台】：{platform}\n"
        f"【金額】：${amount:.2f}\n"
        f"【取餐地址】：{pickup_addr if pickup_addr else '辨識中/無法擷取'}\n"
        f"【送達地址】：{dropoff_addr if dropoff_addr else '辨識中/無法擷取'}\n"
        f"【距離】：{distance_km:.2f} 公里\n"
        f"【耗時】：約 {duration_min:.1f} 分鐘\n"
        f"【黑名單】：{blacklist_result}\n"
        f"【每公里收益】：{earning_per_km:.2f} 元/km\n"
        f"【辨識特徵】：{feature_text}\n"
        f"【建議】：{suggestion}"
    )
    return report
