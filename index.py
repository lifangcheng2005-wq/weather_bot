import os
import json
import time
import requests
import random
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
CACHE_DURATION = 1200  

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
                        try: uvi_val = float(record.get("uvenex", 0))
                        except: uvi_val = 0.0
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

# --- 4. 擴充隨機俏皮生活貼心提醒 ---
def get_warm_reminder(data, query_type):
    try: pop_val = int(data['pop'])
    except: pop_val = 0
    try: uvi_val = float(data['uvi'])
    except: uvi_val = 0.0
    aqi_status = data['aqi_status']
    reminders = []
    
    if query_type in ['all', 'weather']:
        if pop_val >= 70:
            reminders.append(random.choice(["降雨機率高達分之裝熟！出門沒帶傘的話，妳就準備在街上跳曼波求雨了吧～☔", "今天降雨機率太有誠意了，出門一定要抓一把傘，別讓自己變成現撈的落湯雞捏！🐔"]))
        elif pop_val >= 40:
            reminders.append("今天的天空有點傲嬌，降雨機率半吊子，折疊傘還是塞進包包吧！🎒")
        elif "雨" in data['wx']:
            reminders.append("外面現在正在下雨！開車騎車慢一點，柏油路今天不是很想跟妳貼貼喔～🚗")
        else:
            reminders.append(random.choice(["目前看來是不帶傘也穩過的一天！快出門別發霉啦～☀️", "天氣晴朗小福星！這天氣完美到不出去喝杯奶茶都對不起自己的扣達了！🥤"]))

    if query_type in ['all', 'air']:
        if "普通" in aqi_status:
            reminders.append("今天的空氣雖然及格但很邊緣，過敏小可憐們出門記得把口罩戴好！🤧")
        elif "不健康" in aqi_status or "對敏感族群" in aqi_status:
            reminders.append(random.choice(["今天窗外空氣有點『有毒』！口罩快拉好，保護好妳高貴的肺！⚠️", "空氣品質正在鬧脾氣！沒事多待在室內修仙吧！🔮"]))
        else:
            reminders.append("今天的空氣乾淨到像在清境農場！趕快大力吸三口，免費的奢華空氣不吸白不吸～🍃")

    if query_type in ['all', 'uv']:
        if uvi_val >= 8:
            reminders.append(random.choice(["紫外線指數爆表啦！防曬乳塗厚一點，不然出門一趟直接變黑炭！🔥", "非必要請勿在陽光下曝曬，妳不想變成行走的烤肉吧？🍖"]))
        elif uvi_val >= 5:
            reminders.append("紫外線有點微微囂張喔，雖然沒有到融化的程度，但防曬還是要擦一下啦！🧴")
        else:
            reminders.append("今天的紫外線很善良，頂多幫妳補補維生素D，放心出去玩！☀️")

    return " \n".join(reminders)


# --- 5. 強制更名避開快取！三大「極致純淨」獨立 Flex 卡片 ---

def create_pure_weather_card(city_name, data):
    """【純天氣卡】絕無空氣、紫外線欄位 (天空藍)"""
    reminder_text = get_warm_reminder(data, 'weather')
    return {
      "type": "bubble", "size": "mega",
      "header": {
        "type": "box", "layout": "vertical", "backgroundColor": "#3a86ff",
        "contents": [
          {"type": "text", "text": "🌡️ 天氣與氣溫觀報", "weight": "bold", "color": "#FFFFFF", "size": "sm"},
          {"type": "text", "text": city_name, "weight": "bold", "size": "xxl", "color": "#FFFFFF", "margin": "md"}
        ]
      },
      "body": {
        "type": "box", "layout": "vertical",
        "contents": [
          {"type": "box", "layout": "horizontal", "contents": [
              {"type": "text", "text": "☁️ 天氣現況", "color": "#aaaaaa", "size": "sm"}, 
              {"type": "text", "text": data['wx'], "align": "end", "size": "sm", "weight": "bold"}
          ]},
          {"type": "separator", "margin": "md"},
          {"type": "box", "layout": "horizontal", "margin": "md", "contents": [
              {"type": "text", "text": "🌡️ 預測氣溫", "color": "#aaaaaa", "size": "sm"}, 
              {"type": "text", "text": f"{data['min_t']}°C ~ {data['max_t']}°C", "align": "end", "size": "sm", "weight": "bold"}
          ]},
          {"type": "separator", "margin": "md"},
          {"type": "box", "layout": "horizontal", "margin": "md", "contents": [
              {"type": "text", "text": "💧 降雨機率", "color": "#aaaaaa", "size": "sm"}, 
              {"type": "text", "text": f"{data['pop']}%", "align": "end", "size": "sm", "color": "#0077b6", "weight": "bold"}
          ]},
          {"type": "separator", "margin": "lg"},
          {
            "type": "box", "layout": "vertical", "margin": "md", "backgroundColor": "#edf2f7", "paddingAll": "md", "cornerRadius": "md",
            "contents": [
              {"type": "text", "text": "🌧️ 貼心提醒：", "weight": "bold", "size": "xs", "color": "#3a86ff"},
              {"type": "text", "text": reminder_text, "size": "xs", "color": "#4a5568", "wrap": True, "margin": "xs"}
            ]
          }
        ]
      }
    }

def create_pure_air_card(city_name, data):
    """【純空氣卡】絕無溫度、降雨、紫外線欄位 (森林綠)"""
    reminder_text = get_warm_reminder(data, 'air')
    return {
      "type": "bubble", "size": "mega",
      "header": {
        "type": "box", "layout": "vertical", "backgroundColor": "#2a9d8f",
        "contents": [
          {"type": "text", "text": "🍃 空氣品質監測", "weight": "bold", "color": "#FFFFFF", "size": "sm"},
          {"type": "text", "text": city_name, "weight": "bold", "size": "xxl", "color": "#FFFFFF", "margin": "md"}
        ]
      },
      "body": {
        "type": "box", "layout": "vertical",
        "contents": [
          {"type": "box", "layout": "horizontal", "contents": [
              {"type": "text", "text": "📊 AQI 指標", "color": "#aaaaaa", "size": "sm"}, 
              {"type": "text", "text": data['aqi'], "align": "end", "size": "md", "weight": "bold", "color": "#2a9d8f"}
          ]},
          {"type": "separator", "margin": "md"},
          {"type": "box", "layout": "horizontal", "margin": "md", "contents": [
              {"type": "text", "text": "🔍 空氣狀態", "color": "#aaaaaa", "size": "sm"}, 
              {"type": "text", "text": data['aqi_status'], "align": "end", "size": "sm", "weight": "bold"}
          ]},
          {"type": "separator", "margin": "lg"},
          {
            "type": "box", "layout": "vertical", "margin": "md", "backgroundColor": "#e8f5e9", "paddingAll": "md", "cornerRadius": "md",
            "contents": [
              {"type": "text", "text": "😷 貼心提醒：", "weight": "bold", "size": "xs", "color": "#2a9d8f"},
              {"type": "text", "text": reminder_text, "size": "xs", "color": "#2e7d32", "wrap": True, "margin": "xs"}
            ]
          }
        ]
      }
    }

def create_pure_uv_card(city_name, data):
    """【純紫外線卡】絕無天氣、降雨、空氣欄位 (炙熱橘)"""
    reminder_text = get_warm_reminder(data, 'uv')
    return {
      "type": "bubble", "size": "mega",
      "header": {
        "type": "box", "layout": "vertical", "backgroundColor": "#e76f51",
        "contents": [
          {"type": "text", "text": "🕶️ 紫外線指數監測", "weight": "bold", "color": "#FFFFFF", "size": "sm"},
          {"type": "text", "text": city_name, "weight": "bold", "size": "xxl", "color": "#FFFFFF", "margin": "md"}
        ]
      },
      "body": {
        "type": "box", "layout": "vertical",
        "contents": [
          {"type": "box", "layout": "horizontal", "contents": [
              {"type": "text", "text": "☀️ 紫外線指數", "color": "#aaaaaa", "size": "sm"}, 
              {"type": "text", "text": data['uvi'], "align": "end", "size": "md", "weight": "bold", "color": "#e76f51"}
          ]},
          {"type": "separator", "margin": "md"},
          {"type": "box", "layout": "horizontal", "margin": "md", "contents": [
              {"type": "text", "text": "🛡️ 曝曬風險", "color": "#aaaaaa", "size": "sm"}, 
              {"type": "text", "text": data['uvi_level'], "align": "end", "size": "sm", "weight": "bold"}
          ]},
          {"type": "separator", "margin": "lg"},
          {
            "type": "box", "layout": "vertical", "margin": "md", "backgroundColor": "#fdf2e9", "paddingAll": "md", "cornerRadius": "md",
            "contents": [
              {"type": "text", "text": "🧴 貼心提醒：", "weight": "bold", "size": "xs", "color": "#e76f51"},
              {"type": "text", "text": reminder_text, "size": "xs", "color": "#c0392b", "wrap": True, "margin": "xs"}
            ]
          }
        ]
      }
    }

def generate_card_all(city_name, data):
    """【綜合卡】依然保留全包欄位 (深灰色)"""
    reminder_text = get_warm_reminder(data, 'all')
    return {
      "type": "bubble", "size": "mega",
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
              {"type": "text", "text": "💡 管家俏皮提醒：", "weight": "bold", "size": "xs", "color": "#ff6b6b"},
              {"type": "text", "text": reminder_text, "size": "xs", "color": "#555555", "wrap": True, "margin": "xs"}
            ]
          }
        ]
      },
      "footer": {
        "type": "box", "layout": "vertical", "contents": [{"type": "text", "text": "✨ 祝妳有個美好的一天！", "size": "xs", "color": "#aaaaaa", "align": "center"}]
      }
    }


# --- 6. Webhook ---
@app.route("/webhook", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature', '')
    body = request.get_data(as_text=True)
    try: handler.handle(body, signature)
    except InvalidSignatureError: abort(400)
    return 'OK'


# --- 7. 路由核心控制 (精準對接更名後的獨立圖卡) ---
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_input = event.message.text.strip()
    user_input_lower = user_input.lower()
    
    # 選單導引判斷
    if user_input_lower in ["我要查氣象", "呼叫管家！我想看今日氣象圖卡", "查氣象", "綜合氣象"]:
        line_bot_api.reply_message(event.reply_token, TextMessage(text="☀️ 好喔！想要查詢哪一個縣市的『綜合氣象卡片』呢？\n(例如：台中、台北、高雄)"))
        return
    elif any(k in user_input_lower for k in ["天氣", "氣溫", "溫度", "降雨", "今天天氣如何啊", "幾度"]):
        if not any(key in user_input_lower for key in CITY_MAPPING.keys()):
            line_bot_api.reply_message(event.reply_token, TextMessage(text="🌡️ 沒問題！請問妳想了解哪一個縣市的『天氣與氣溫』呢？\n(例如：台中、宜蘭、屏東)"))
            return
    elif any(k in user_input_lower for k in ["空氣", "空氣品質", "aqi", "幫我看現在空氣品質好不好", "pm25"]):
        if not any(key in user_input_lower for key in CITY_MAPPING.keys()):
            line_bot_api.reply_message(event.reply_token, TextMessage(text="🍃 收到！請問妳要看哪一個縣市的『空氣品質AQI』呢？\n(example：新北、台南、馬祖)"))
            return
    elif any(k in user_input_lower for k in ["紫外線", "紫外線指數", "uv", "太陽好大！幫我查一下紫外線"]):
        if not any(key in user_input_lower for key in CITY_MAPPING.keys()):
            line_bot_api.reply_message(event.reply_token, TextMessage(text="🕶️ OK！防曬大作戰～請問想查哪一個縣市的『紫外線指數』呢？\n(example：彰化、澎湖、台北)"))
            return

    # 縣市解析
    target_city_key = None
    for key in CITY_MAPPING.keys():
        if key in user_input_lower:
            target_city_key = key
            break

    if target_city_key:
        city_info = CITY_MAPPING[target_city_key]
        target_county = city_info["county"]
        
        all_data = fetch_all_weather_data()
        city_weather = all_data.get(target_county, {
            "wx": "情報更新中", "pop": "0", "min_t": "--", "max_t": "--",
            "aqi": "讀取中", "aqi_status": "請稍後", "uvi": "0", "uvi_level": "一般"
        })
        
        # ─── 呼叫全新獨立卡片 (強制刷新 JSON 骨架) ───
        
        # 1. 查空氣 -> 只餵 create_pure_air_card 骨架
        if any(k in user_input_lower for k in ["空氣", "aqi", "pm25"]):
            pure_contents = create_pure_air_card(target_county, city_weather)
            line_bot_api.reply_message(event.reply_token, FlexSendMessage(alt_text=f"{target_county}空氣品質", contents=pure_contents))
            
        # 2. 查紫外線 -> 只餵 create_pure_uv_card 骨架
        elif any(k in user_input_lower for k in ["紫外線", "uv"]):
            pure_contents = create_pure_uv_card(target_county, city_weather)
            line_bot_api.reply_message(event.reply_token, FlexSendMessage(alt_text=f"{target_county}紫外線指數", contents=pure_contents))
            
        # 3. 查天氣 -> 只餵 create_pure_weather_card 骨架
        elif any(k in user_input_lower for k in ["天氣", "溫度", "氣溫", "幾度", "降雨"]):
            pure_contents = create_pure_weather_card(target_county, city_weather)
            line_bot_api.reply_message(event.reply_token, FlexSendMessage(alt_text=f"{target_county}天氣預報", contents=pure_contents))
            
        # 4. 盲打或查綜合氣象 -> 給 generate_card_all 全套大圖卡
        else:
            full_contents = generate_card_all(target_county, city_weather)
            line_bot_api.reply_message(event.reply_token, FlexSendMessage(alt_text=f"{target_county}綜合氣象", contents=full_contents))
            
    else:
        line_bot_api.reply_message(event.reply_token, TextMessage(text="請輸入【城市 + 想查的項目】，管家隨時待命！"))

app.debug = False
