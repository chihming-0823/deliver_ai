# -*- coding: utf-8 -*-
import os
import requests

# 從環境變數取得 Google Maps API Key
GOOGLE_MAPS_API_KEY = os.environ.get("GOOGLE_MAPS_API_KEY", "")

def calculate_distance(origin, destination):
    """
    使用 Google Maps Distance Matrix API 計算距離與預估時間
    傳入：
        origin (str) - 取餐地址
        destination (str) - 送達地址
    回傳：
        dict: {"distance_km": float, "duration_min": float}
    """
    if not GOOGLE_MAPS_API_KEY:
        print("⚠️ GOOGLE_MAPS_API_KEY 未設定，請設定環境變數")
        return {"distance_km": 0, "duration_min": 0}

    try:
        url = (
            "https://maps.googleapis.com/maps/api/distancematrix/json"
            f"?origins={origin}"
            f"&destinations={destination}"
            f"&mode=driving"
            f"&language=zh-TW"
            f"&key={GOOGLE_MAPS_API_KEY}"
        )
        response = requests.get(url)
        data = response.json()

        if data.get("status") == "OK":
            element = data["rows"][0]["elements"][0]
            if element["status"] == "OK":
                distance_text = element["distance"]["text"]  # 例如 "5.7 公里"
                duration_text = element["duration"]["text"]  # 例如 "18 分鐘"

                distance_km = float(distance_text.replace("公里", "").strip())
                duration_min = float(duration_text.replace("分鐘", "").strip())

                print(f"📍 Google Maps 計算結果：{distance_km} 公里 / {duration_min} 分鐘")
                return {"distance_km": distance_km, "duration_min": duration_min}
            else:
                print(f"❌ Google 回傳元素異常：{element['status']}")
        else:
            print(f"❌ Google Maps API 回傳錯誤：{data.get('status')}")
        return {"distance_km": 0, "duration_min": 0}

    except Exception as e:
        print(f"❌ calculate_distance 發生錯誤：{e}")
        return {"distance_km": 0, "duration_min": 0}


# 測試用（可直接在命令列測試）
if __name__ == "__main__":
    origin = "桃園市蘆竹區南崁路133號"
    destination = "桃園市中壢區中正路330號"
    result = calculate_distance(origin, destination)
    print(result)
