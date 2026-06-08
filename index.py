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
CACHE_DURATION = 1200  # 快取 20 分鐘

# 載入全台城市對照表 JSON
with open("city_mapping.json", "r", encoding="utf-8") as f:
    CITY_MAPPING = json.load(f)

# --- 3. 核心資料撈取與 JSON 整合函式 ---
def fetch_all_weather_data():
    current_time = time.time()
    
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
            county = record.get("county", "").replace("台", "臺")
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
            county = record.get("county", "").replace("台", "臺")
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

    if integrated_data:
        cache["data"] = integrated_data
        cache["last_updated"] = current_time
        
    return integrated_data

# --- 4. 生成有溫度的貼心提醒文字 ---
def get_warm_reminder(data, query_type):
    """根據當前氣象數據，生成貼心的生活提醒"""
    try:
        pop_val = int(data['pop'])
    except:
        pop_val = 0
        
    try:
        uvi_val = float(data['uvi'])
    except:
        uvi_val = 0.0
        
    aqi_status = data['aqi_status']

    reminders = []
    
    # 針對天氣/降雨的提醒
    if query_type in ['all', 'weather']:
        if pop_val >= 50:
            reminders.append("今天降雨機率偏高，出門記得帶把傘，別淋濕感冒囉！☔")
        elif "雨" in data['wx']:
            reminders.append("外面正在下雨或有陣雨，路上騎車開車要注意安全、減速慢行喔！🚗")
        else:
            reminders.append("目前看來是個適合出門的好天氣，祝妳今天事事順心！✨")

    # 針對空氣品質的提醒
    if query_type in ['all', 'air']:
        if "普通" in aqi_status:
            reminders.append("今天的空氣品質普通，過敏體質的朋友出門可以考慮戴個口罩喔。😷")
        elif "對敏感族群不健康" in aqi_status or "不健康" in aqi_status:
            reminders.append("今天空氣品質不太理想，盡量減少戶外劇烈運動，口罩一定要戴好！⚠️")
        else:
            reminders.append("窗外空氣很清新，可以多呼吸新鮮空氣、放鬆一下心情！🍃")

    # 針對紫外線的提醒
    if query_type in ['all', 'uv']:
        if uvi_val >= 6:
            reminders.append("紫外線指數偏高！出門記得塗防曬乳、戴帽子或撐陽傘，小心中暑和曬傷喔！🕶️")
        else:
            reminders.append("今天的紫外線很溫和，但陽光大的時候還是要注意補充水分。💧")

    # 隨機或組合回傳一條溫暖的問候
    return " \n".join(reminders)


# --- 5. 生成 LINE Flex Message 綜合圖卡 (包含完整欄位與動態提醒) ---
def generate_flex_message(city_name, data):
    reminder_text = get_warm_reminder(data, 'all')
    return {
      "type": "bubble",
      "size": "mega",
      "header": {
        "type": "box", "layout": "vertical", "backgroundColor": "#272c35",
        "contents": [
          {"type": "text", "text": "☀️ 全台氣象綜合情報", "weight": "bold", "color": "#FFFFFF", "size": "sm"},
          {"type": "text", "text": city_name, "weight": "bold", "size": "xxl", "color": "#FFFFFF", "margin": "md"}
        ]
      },
      "body": {
        "type": "box", "layout": "vertical",
        "contents": [
          {"type": "box", "layout": "horizontal", "contents": [{"type": "text", "text": "📦 天氣現況", "color": "#aaaaaa", "size": "sm"}, {"type": "text", "text": f"{data['wx']} ({data['min_t']}°C ~ {data['max_t']}°C)", "align": "end", "size": "sm", "weight": "bold"}]},
          {"type": "separator", "margin": "md"},
          {"type": "box", "layout": "horizontal", "margin": "md", "contents": [{"type": "text", "text": "💧 降雨機率", "color": "#aaaaaa", "size": "sm"}, {"type": "text", "text": f"{data['pop']}%", "align": "end", "size": "sm", "weight": "bold"}]},
          {"type": "separator", "margin": "md"},
          {"type": "box", "layout": "horizontal", "margin": "md", "contents": [{"type": "text", "text": "🍃 空氣品質", "color": "#aaaaaa", "size": "sm"}, {"type": "text", "text": f"AQI {data['aqi']} ({data['aqi_status']})", "align": "end", "size": "sm", "color": "#00aa00", "weight": "bold"}]},
          {"type": "separator", "margin": "md"},
          {"type": "box", "layout": "horizontal", "margin": "md", "contents": [{"type": "text", "text": "🕶️ 紫外線指數", "color": "#aaaaaa", "size": "sm"}, {"type": "text", "text": f"{data['uvi']} ({data['uvi_level']})", "align": "end", "size": "sm", "weight": "bold"}]},
          {"type": "separator", "margin": "lg"},
          {
            "type": "box", "layout": "vertical", "margin": "md", "backgroundColor": "#f8f9fa", "paddingAll": "md", "cornerRadius": "md",
            "contents": [
              {"type": "text", "text": "💡 管家貼心提醒：", "weight": "bold", "size": "xs", "color": "#555555"},
              {"type": "text", "text": reminder_text, "size": "xs", "color": "#666666", "wrap": True, "margin": "xs"}
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


# --- 6. Webhook 接收端 ---
@app.route("/webhook", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature', '')
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'


# --- 7. 核心訊息處理判斷（全面重構四大情境） ---
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
        
        # 撈取最新整合 JSON 資料
        all_data = fetch_all_weather_data()
        city_weather = all_data.get(target_county, {
            "wx": "情報更新中", "pop": "0", "min_t": "--", "max_t": "--",
            "aqi": "讀取中", "aqi_status": "請稍後", "uvi": "0", "uvi_level": "一般"
        })
        
        # 💡 情境 1：使用者輸入「氣象」（如：台中氣象） -> 產出包含動態提醒的完整 Flex Message 卡片
        if "氣象" in user_input:
            flex_contents = generate_flex_message(target_county, city_weather)
            line_bot_api.reply_message(
                event.reply_token,
                FlexSendMessage(alt_text=f"{target_county}綜合氣象情報", contents=flex_contents)
            )
            
        # 💡 情境 2：使用者輸入「天氣」（如：台中天氣、台中氣溫、幾度） -> 顯示天氣、氣溫、降雨機率 + 貼心提醒
        elif "天氣" in user_input or "溫度" in user_input or "氣溫" in user_input or "幾度" in user_input:
            reminder = get_warm_reminder(city_weather, 'weather')
            reply_text = f"🌡️【{target_county}】即時天氣與氣溫：\n" \
                         f"🔹 天氣現況：{city_weather['wx']}\n" \
                         f"🔹 預測氣溫：{city_weather['min_t']}°C ~ {city_weather['max_t']}°C\n" \
                         f"🔹 降雨機率：{city_weather['pop']}%\n\n" \
                         f"💡 管家貼心提醒：\n{reminder}"
            line_bot_api.reply_message(event.reply_token, TextMessage(text=reply_text))
            
        # 💡 情境 3：使用者輸入「空氣」（如：台中空氣、aqi） -> 顯示空氣品質 + 貼心提醒
        elif "空氣" in user_input or "aqi" in user_input or "pm25" in user_input:
            reminder = get_warm_reminder(city_weather, 'air')
            reply_text = f"🍃【{target_county}】空氣品質觀測：\n" \
                         f"🔹 AQI 指標：{city_weather['aqi']}\n" \
                         f"🔹 狀態說明：{city_weather['aqi_status']}\n\n" \
                         f"💡 管家貼心提醒：\n{reminder}"
            line_bot_api.reply_message(event.reply_token, TextMessage(text=reply_text))
            
        # 💡 情境 4：使用者輸入「紫外線」（如：台中紫外線、uv） -> 顯示紫外線指數 + 貼心提醒
        elif "紫外線" in user_input or "uv" in user_input:
            reminder = get_warm_reminder(city_weather, 'uv')
            reply_text = f"🕶️【{target_county}】紫外線即時監測：\n" \
                         f"🔹 紫外線指數：{city_weather['uvi']}\n" \
                         f"🔹 風險級別：{city_weather['uvi_level']}\n\n" \
                         f"💡 管家貼心提醒：\n{reminder}"
            line_bot_api.reply_message(event.reply_token, TextMessage(text=reply_text))
            
        # 預設後備：如果只輸入縣市名字（如：台中），就預設給最完整的 Flex Message 綜合卡片
        else:
            flex_contents = generate_flex_message(target_county, city_weather)
            line_bot_api.reply_message(
                event.reply_token,
                FlexSendMessage(alt_text=f"{target_county}綜合氣象情報", contents=flex_contents)
            )
            
    else:
        # 導引提示
        line_bot_api.reply_message(
            event.reply_token,
            TextMessage(text="請輸入【城市 + 想查的項目】。管家馬上為妳貼心送上！\n\n"
                             "🔍 查詢範例：\n"
                             "1️⃣ 「台中氣象」👉 完整大圖卡\n"
                             "2️⃣ 「台中天氣」👉 氣溫與降雨\n"
                             "3️⃣ 「台中空氣」👉 AQI品質\n"
                             "4️⃣ 「台中紫外線」👉 紫外線防曬")
        )

app.debug = False