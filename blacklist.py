# coding: utf-8
import os
import csv
from typing import Iterable


def _load_words() -> set:
    base = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
    words = set()
    for name in ("blacklist.txt", "blacklist.csv"):
        path = os.path.join(base, name)
        if os.path.exists(path):
            if name.endswith(".txt"):
                with open(path, "r", encoding="utf-8") as f:
                    for ln in f:
                        w = ln.strip()
                        if w:
                            words.add(w)
            else:
                with open(path, "r", encoding="utf-8") as f:
                    for row in csv.reader(f):
                        for cell in row:
                            w = (cell or "").strip()
                            if w:
                                words.add(w)
    return words


BL_WORDS = _load_words()


def check(pickup_text: str, dropoff_text: str) -> str:
    """
    簡單關鍵字黑名單。任一地址命中即回 '命中'，否則 '無'。
    """
    text = " ".join([(pickup_text or ""), (dropoff_text or "")])
    if not text:
        return "無"
    for w in BL_WORDS:
        if w and w in text:
            return "命中"
    return "無"
