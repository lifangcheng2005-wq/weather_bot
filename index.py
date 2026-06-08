import os
import json
import time
import requests
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, FlexSendMessage

app = Flask(__name__)

# --- 1. 從 Vercel 環境變數讀取金鑰 ---
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET")
MOENV_API_KEY = os.environ.get("MOENV_API_KEY")
CWA_API_KEY = os.environ.get("CWA_API_KEY")

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# --- 2. 記憶體快取設計 ---
cache = {
    "data": None,
    "last_updated": 0
}
CACHE_DURATION = 1200  # 快取 20 分鐘，避免頻繁呼叫 API 被封鎖 IP

# 載入全台城市對照表 JSON
with open("city_mapping.json", "r", encoding="utf-8") as f:
    CITY_MAPPING = json.load(f)

# --- 3. 核心資料撈取與 JSON 整合函式 ---
def fetch_all_weather_data():
    current_time = time.time()
    
    # 若快取未過期，直接回傳快取資料，加速回應
    if cache["data"] and (current_time - cache["last_updated"] < CACHE_DURATION):
        return cache["data"]

    print("⚡ 正在向政府 API 更新全台縣市 JSON 資料...")
    integrated_data = {}

    # A. 撈取氣象署全台天氣預報 JSON
    try:
        cwa_url = f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-C0032-001?Authorization={CWA_API_KEY}&format=JSON"
        cwa_res = requests.get(cwa_url, timeout=10).json()
        location_list = cwa_res.get("records", {}).get("location", [])
        
        for loc in location_list:
            # 防護：將政府回傳的 "台" 統一轉為 "臺"
            cname = loc.get("locationName", "").replace("台", "臺")
            weather_elements = loc.get("weatherElement", [])
            
            wx = weather_elements[0].get("time", [{}])[0].get("parameter", {}).get("parameterName", "情報獲取中")
            pop = weather_elements[1].get("time", [{}])[0].get("parameter", {}).get("parameterName", "0")
            min_t = weather_elements[2].get("time", [{}])[0].get("parameter", {}).get("parameterName", "--")
            max_t = weather_elements[4].get("time", [{}])[0].get("parameter", {}).get("parameterName", "--")
            
            integrated_data[cname] = {
                "wx": wx, "pop": pop, "min_t": min_t, "max_t": max_t,
                "aqi": "維持常態", "aqi_status": "良好", "uvi": "3", "uvi_level": "低量級"
            }
    except Exception as e:
        print(f"❌ 氣象署 API 異常: {e}")

    # B. 撈取環境部全台 AQI JSON
    try:
        aqi_url = f"https://data.moenv.gov.tw/api/v2/aqx_p_43?api_key={MOENV_API_KEY}&format=json"
        aqi_res = requests.get(aqi_url, timeout=10).json()
        aqi_records = aqi_res.get("records", [])
        
        for record in aqi_records:
            county = record.get("county", "").replace("台", "臺") # 統一轉臺
            sitename = record.get("sitename", "")
            
            for k, v in CITY_MAPPING.items():
                if v["county"] == county and v["aqi_station"] == sitename:
                    if v["county"] in integrated_data:
                        integrated_data[v["county"]]["aqi"] = record.get("aqi", "無資料")
                        integrated_data[v["county"]]["aqi_status"] = record.get("status", "正常")
    except Exception as e:
        print(f"❌ 環境部 AQI API 異常: {e}")

    # C. 撈取環境部全台 紫外線 UVI JSON
    try:
        uv_url = f"https://data.moenv.gov.tw/api/v2/uv_p_01?api_key={MOENV_API_KEY}&format=json"
        uv_res = requests.get(uv_url, timeout=10).json()
        uv_records = uv_res.get("records", [])
        
        for record in uv_records:
            county = record.get("county", "").replace("台", "臺") # 統一轉臺
            sitename = record.get("sitename", "")
            
            for k, v in CITY_MAPPING.items():
                if v["county"] == county and v["uv_station"] == sitename:
                    if v["county"] in integrated_data:
                        try:
                            uvi_val = float(record.get("uvenex", 0))
                        except:
                            uvi_val = 0.0
                            
                        if uvi_val <= 2: level = "微量級"
                        elif uvi_val <= 5: level = "低量級"
                        elif uvi_val <= 7: level = "中量級"
                        elif uvi_val <= 10: level = "過量級"
                        else: level = "危險級"
                        
                        integrated_data[v["county"]]["uvi"] = str(uvi_val)
                        integrated_data[v["county"]]["uvi_level"] = level
    except Exception as e:
        print(f"❌ 環境部 UVI API 異常: {e}")

    # 如果成功整合到資料，存入記憶體快取
    if integrated_data:
        cache["data"] = integrated_data
        cache["last_updated"] = current_time
        
    return integrated_data

# --- 4. 生成 LINE Flex Message 綜合圖卡 ---
def generate_flex_message(city_name, data):
    return {
      "type": "bubble",
      "size": "mega",
      "header": {
        "type": "box", "layout": "vertical", "backgroundColor": "#272c35",
        "contents": [
          {"type": "text", "text": "☀️ 全台氣象即時情報", "weight": "bold", "color": "#FFFFFF", "size": "sm"},
          {"type": "text", "text": city_name, "weight": "bold", "size": "xxl", "color": "#FFFFFF", "margin": "md"}
        ]
      },
      "body": {
        "type": "box", "layout": "vertical",
        "contents": [
          {
            "type": "box", "layout": "horizontal",
            "contents": [
              {"type": "text", "text": "📦 天氣現況", "color": "#aaaaaa", "size": "sm"},
              {"type": "text", "text": f"{data['wx']} ({data['min_t']}°C ~ {data['max_t']}°C)", "align": "end", "size": "sm", "weight": "bold"}
            ]
          },
          {"type": "separator", "margin": "md"},
          {
            "type": "box", "layout": "horizontal", "margin": "md",
            "contents": [
              {"type": "text", "text": "💧 降雨機率", "color": "#aaaaaa", "size": "sm"},
              {"type": "text", "text": f"{data['pop']}%", "align": "end", "size": "sm", "weight": "bold"}
            ]
          },
          {"type": "separator", "margin": "md"},
          {
            "type": "box", "layout": "horizontal", "margin": "md",
            "contents": [
              {"type": "text", "text": "🍃 空氣品質", "color": "#aaaaaa", "size": "sm"},
              {"type": "text", "text": f"AQI {data['aqi']} ({data['aqi_status']})", "align": "end", "size": "sm", "color": "#00aa00", "weight": "bold"}
            ]
          },
          {"type": "separator", "margin": "md"},
          {
            "type": "box", "layout": "horizontal", "margin": "md",
            "contents": [
              {"type": "text", "text": "🕶️ 紫外線指數", "color": "#aaaaaa", "size": "sm"},
              {"type": "text", "text": f"{data['uvi']} ({data['uvi_level']})", "align": "end", "size": "sm", "weight": "bold"}
            ]
          }
        ]
      },
      "footer": {
        "type": "box", "layout": "vertical",
        "contents": [
          {"type": "text", "text": "✨ 祝妳有個美好的一天！", "size": "xs", "color": "#aaaaaa", "align": "center"}
        ]
      }
    }

# --- 5. Webhook 接收端（網址後端綁定 /webhook） ---
@app.route("/webhook", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature', '')
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

# --- 6. 核心訊息處理判斷（精準分流對話邏輯） ---
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_input = event.message.text.strip().lower()
    
    # 尋找輸入字串中是否包含 22 縣市的關鍵字
    target_city_key = None
    for key in CITY_MAPPING.keys():
        if key in user_input:
            target_city_key = key
            break

    # 如果有比對到縣市關鍵字
    if target_city_key:
        city_info = CITY_MAPPING[target_city_key]
        target_county = city_info["county"]
        
        # 撈取並整合政府 JSON 資料
        all_data = fetch_all_weather_data()
        
        # 後端Fallback機制：若 API 漏抓，提供安全預設值，確保機器人絕對不會吐出錯誤
        city_weather = all_data.get(target_county, {
            "wx": "情報更新中", "pop": "0", "min_t": "--", "max_t": "--",
            "aqi": "讀取中", "aqi_status": "請稍後", "uvi": "0", "uvi_level": "一般"
        })
        
        # --- 分流判斷開始 ---
        
        # 1. 分開詢問：空氣品質
        if "空氣" in user_input or "aqi" in user_input or "pm25" in user_input:
            reply_text = f"🍃【{target_county}】空氣品質指標：\n🔹 AQI 指標：{city_weather['aqi']}\n🔹 狀態說明：{city_weather['aqi_status']}"
            line_bot_api.reply_message(event.reply_token, TextMessage(text=reply_text))
            
        # 2. 分開詢問：紫外線
        elif "紫外線" in user_input or "uv" in user_input:
            reply_text = f"🕶️【{target_county}】紫外線即時監測：\n🔹 紫外線指數：{city_weather['uvi']}\n🔹 風險級別：{city_weather['uvi_level']}"
            line_bot_api.reply_message(event.reply_token, TextMessage(text=reply_text))
            
        # 3. 分開詢問：溫度 / 氣溫
        elif "溫度" in user_input or "氣溫" in user_input or "幾度" in user_input or "熱" in user_input or "冷" in user_input:
            reply_text = f"🌡️【{target_county}】即時氣溫情報：\n🔹 目前天氣：{city_weather['wx']}\n🔹 預測氣溫：{city_weather['min_t']}°C ~ {city_weather['max_t']}°C\n🔹 降雨機率：{city_weather['pop']}%"
            line_bot_api.reply_message(event.reply_token, TextMessage(text=reply_text))
            
        # 4. 綜合查詢：使用者沒加指定項目，直接給最漂亮的綜合版 Flex Message
        else:
            flex_contents = generate_flex_message(target_county, city_weather)
            line_bot_api.reply_message(
                event.reply_token,
                FlexSendMessage(alt_text=f"{target_county}綜合氣象情報", contents=flex_contents)
            )
            
    else:
        # 當找不到城市關鍵字時的貼心引導提示
        line_bot_api.reply_message(
            event.reply_token,
            TextMessage(text="請輸入【城市 + 查詢項目】。\n\n例如：\n👉「台中天氣」(看綜合圖卡)\n👉 nudge「台中溫度」(單獨查氣溫)\n👉「台中空氣」(單獨查AQI)\n👉「台北紫外線」(單獨查抗陽指標)")
        )

app.debug = False