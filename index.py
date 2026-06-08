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
            reminders.append(random.choice([
                "降雨機率高達分之裝熟！出門沒帶傘的話，妳就準備在街上跳曼波求雨了吧～☔",
                "今天降雨機率太有誠意了，出門一定要抓一把傘，別讓自己變成現撈的落湯雞捏！🐔",
                "偵測到天空今天走『愛哭鬼』路線，降雨機率直接灌頂！沒帶雨具妳會哭，真的！🥺"
            ]))
        elif pop_val >= 40:
            reminders.append(random.choice([
                "今天的天空有點傲嬌，降雨機率半吊子，折疊傘還是塞進包包吧，防呆用！🎒",
                "雨神今天可能心情好，降雨機率有一半！賭一把不帶傘？我賭妳會輸，快去拿傘！🔮"
            ]))
        elif "雨" in data['wx']:
            reminders.append(random.choice([
                "外面現在正在下雨！聽管話，騎車慢一點，柏油路今天不是很想跟妳貼貼喔～🚗",
                "嘩啦啦啦～雨滴正在唱歌，路上注意安全，行車距離保持好，平安第一！🌧️"
            ]))
        else:
            reminders.append(random.choice([
                "目前看來是不帶傘也穩過的一天！快出門去踩踩陽光，別發霉啦～☀️",
                "天氣晴朗小福星！這天氣完美到不出去喝杯奶茶都對不起自己的扣達了！🥤",
                "偵測到晴天 buff 已疊滿！還不快出去玩，要把自己當作馬鈴薯種在沙發上嗎？🥔"
            ]))

    if query_type in ['all', 'air']:
        if "普通" in aqi_status:
            reminders.append(random.choice([
                "今天的空氣雖然及格但很邊緣，過敏小可憐們出門記得把口罩戴好，別一直打噴嚏！🤧",
                "空氣指標正在走鋼索，防禦力有點低。過敏星人如果不想擤衛生紙到鼻子破皮，乖乖戴口罩！🧻"
            ]))
        elif "對敏感族群不健康" in aqi_status or "不健康" in aqi_status:
            reminders.append(random.choice([
                "今天窗外空氣有點『有毒』！乖，暫時別去外面瘋狂奔跑，口罩快拉好，保護好妳高貴的肺！⚠️",
                "空氣品質正在鬧脾氣！過敏星人今天嚴禁開啟人體清淨機模式，沒事多待在室內修仙吧！🔮",
                "警報！外面的空氣充滿迷霧，這不是仙境是污染！快把口罩戴上，拒絕當人體吸塵器！👺"
            ]))
        else:
            reminders.append(random.choice([
                "今天的空氣乾淨到像在清境農場！趕快大力吸三口，免費的奢華空氣不吸白不吸～🍃",
                "PM2.5 今天集體放假去了！空氣超級無敵好，家裡窗戶快打開通風一波～🪟",
                "今天的空氣品質是極品！吸一口神清氣爽，吸兩口考試都考一百分啦！💯"
            ]))

    if query_type in ['all', 'uv']:
        if uvi_val >= 8:
            reminders.append(random.choice([
                "紫外線指數爆表啦！今天太陽公公沒在跟妳客氣的，防曬乳塗厚一點，不然出門一趟直接變黑炭！🔥",
                "這紫外線是要把人烤熟嗎？防曬、墨鏡、遮陽傘快使出三防防禦，不要跟太陽硬碰硬！🕶️",
                "吸血鬼警告！外面的太陽會把人融化，非必要請勿在陽光下曝曬，妳不想變成行走的烤肉吧？🍖"
            ]))
        elif uvi_val >= 5:
            reminders.append(random.choice([
                "紫外線有點微微囂張喔，雖然沒有到融化的程度，但美白很貴的，防曬還是要擦一下啦！🧴",
                "陽光普照但帶點小惡意，紫外線指數正在蠢蠢欲動，出門陰影處多走走，別被曬黑了！🐾"
            ]))
        else:
            reminders.append(random.choice([
                "今天的紫外線很善良，頂多幫妳補補維生素D，不用怕被曬成黑炭，放心出去玩！☀️",
                "陽光很溫柔，紫外線今天處於休眠狀態，是個適合在草地上滾來滾去的好日子！🌱"
            ]))

    return " \n".join(reminders)


# --- 5. 徹底分離！四大 Flex Message 卡片工廠 (已刪除不相關欄位) ---

def generate_card_all(city_name, data):
    """卡片1：綜合氣象大圖卡 (保留完整全部資訊)"""
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

def generate_card_weather(city_name, data):
    """卡片2：獨立天氣卡 (❌已刪除空氣與紫外線！只留下天氣、氣溫、降雨)"""
    reminder_text = get_warm_reminder(data, 'weather')
    return {
      "type": "bubble", "size": "mega",
      "header": {
        "type": "box", "layout": "vertical", "backgroundColor": "#3a86ff",
        "contents": [
          {"type": "text", "text": "編號 1 號 🌡️ 獨立天氣與氣溫觀測", "weight": "bold", "color": "#FFFFFF", "size": "sm"},
          {"type": "text", "text": city_name, "weight": "bold", "size": "xxl", "color": "#FFFFFF", "margin": "md"}
        ]
      },
      "body": {
        "type": "box", "layout": "vertical",
        "contents": [
          {"type": "box", "layout": "horizontal", "contents": [{"type": "text", "text": "☁️ 天氣現況", "color": "#aaaaaa", "size": "sm"}, {"type": "text", "text": data['wx'], "align": "end", "size": "sm", "weight": "bold"}]},
          {"type": "separator", "margin": "md"},
          {"type": "box", "layout": "horizontal", "margin": "md", "contents": [{"type": "text", "text": "🌡️ 預測氣溫", "color": "#aaaaaa", "size": "sm"}, {"type": "text", "text": f"{data['min_t']}°C ~ {data['max_t']}°C", "align": "end", "size": "sm", "weight": "bold"}]},
          {"type": "separator", "margin": "md"},
          {"type": "box", "layout": "horizontal", "margin": "md", "contents": [{"type": "text", "text": "💧 降雨機率", "color": "#aaaaaa", "size": "sm"}, {"type": "text", "text": f"{data['pop']}%", "align": "end", "size": "sm", "color": "#0077b6", "weight": "bold"}]},
          {"type": "separator", "margin": "lg"},
          {
            "type": "box", "layout": "vertical", "margin": "md", "backgroundColor": "#edf2f7", "paddingAll": "md", "cornerRadius": "md",
            "contents": [
              {"type": "text", "text": "🌧️ 氣溫與帶傘提醒：", "weight": "bold", "size": "xs", "color": "#3a86ff"},
              {"type": "text", "text": reminder_text, "size": "xs", "color": "#4a5568", "wrap": True, "margin": "xs"}
            ]
          }
        ]
      }
    }

def generate_card_air(city_name, data):
    """卡片3：獨立空氣卡 (❌已刪除天氣、氣溫、降雨、紫外線！只留 AQI)"""
    reminder_text = get_warm_reminder(data, 'air')
    return {
      "type": "bubble", "size": "mega",
      "header": {
        "type": "box", "layout": "vertical", "backgroundColor": "#2a9d8f",
        "contents": [
          {"type": "text", "text": "編號 2 號 🍃 獨立空氣品質監測", "weight": "bold", "color": "#FFFFFF", "size": "sm"},
          {"type": "text", "text": city_name, "weight": "bold", "size": "xxl", "color": "#FFFFFF", "margin": "md"}
        ]
      },
      "body": {
        "type": "box", "layout": "vertical",
        "contents": [
          {"type": "box", "layout": "horizontal", "contents": [{"type": "text", "text": "📊 AQI 指標", "color": "#aaaaaa", "size": "sm"}, {"type": "text", "text": data['aqi'], "align": "end", "size": "md", "weight": "bold", "color": "#2a9d8f"}]},
          {"type": "separator", "margin": "md"},
          {"type": "box", "layout": "horizontal", "margin": "md", "contents": [{"type": "text", "text": "🔍 空氣狀態", "color": "#aaaaaa", "size": "sm"}, {"type": "text", "text": data['aqi_status'], "align": "end", "size": "sm", "weight": "bold"}]},
          {"type": "separator", "margin": "lg"},
          {
            "type": "box", "layout": "vertical", "margin": "md", "backgroundColor": "#e8f5e9", "paddingAll": "md", "cornerRadius": "md",
            "contents": [
              {"type": "text", "text": "😷 呼吸道防護提醒：", "weight": "bold", "size": "xs", "color": "#2a9d8f"},
              {"type": "text", "text": reminder_text, "size": "xs", "color": "#2e7d32", "wrap": True, "margin": "xs"}
            ]
          }
        ]
      }
    }

def generate_card_uv(city_name, data):
    """卡片4：獨立紫外線卡 (❌已刪除天氣、氣溫、降雨、空氣！只留紫外線)"""
    reminder_text = get_warm_reminder(data, 'uv')
    return {
      "type": "bubble", "size": "mega",
      "header": {
        "type": "box", "layout": "vertical", "backgroundColor": "#e76f51",
        "contents": [
          {"type": "text", "text": "編號 3 號 🕶️ 獨立紫外線監測", "weight": "bold", "color": "#FFFFFF", "size": "sm"},
          {"type": "text", "text": city_name, "weight": "bold", "size": "xxl", "color": "#FFFFFF", "margin": "md"}
        ]
      },
      "body": {
        "type": "box", "layout": "vertical",
        "contents": [
          {"type": "box", "layout": "horizontal", "contents": [{"type": "text", "text": "☀️ 紫外線指數", "color": "#aaaaaa", "size": "sm"}, {"type": "text", "text": data['uvi'], "align": "end", "size": "md", "weight": "bold", "color": "#e76f51"}]},
          {"type": "separator", "margin": "md"},
          {"type": "box", "layout": "horizontal", "margin": "md", "contents": [{"type": "text", "text": "🛡️ 曝曬風險級別", "color": "#aaaaaa", "size": "sm"}, {"type": "text", "text": data['uvi_level'], "align": "end", "size": "sm", "weight": "bold"}]},
          {"type": "separator", "margin": "lg"},
          {
            "type": "box", "layout": "vertical", "margin": "md", "backgroundColor": "#fdf2e9", "paddingAll": "md", "cornerRadius": "md",
            "contents": [
              {"type": "text", "text": "🧴 抗陽防曬提醒：", "weight": "bold", "size": "xs", "color": "#e76f51"},
              {"type": "text", "text": reminder_text, "size": "xs", "color": "#c0392b", "wrap": True, "margin": "xs"}
            ]
          }
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


# --- 7. 核心：模糊感應與多輪獨立卡片路由引擎 ---
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_input = event.message.text.strip()
    user_input_lower = user_input.lower()
    
    # ─── 第一階段：模糊感應多輪導引問城市 ───
    if user_input_lower in ["..."]:  # 略過選單固定句以節省版面，邏輯保持一致
        pass
        
    if user_input_lower in ["我要查氣象", "呼叫管家！我想看今日氣象圖卡", "查氣象", "綜合氣象"]:
        line_bot_api.reply_message(event.reply_token, TextMessage(text="☀️ 好喔！想要查詢哪一個縣市的『綜合氣象卡片』呢？\n(例如輸入：台中、台北、高雄)"))
        return
        
    elif any(k in user_input_lower for k in ["天氣", "氣溫", "溫度", "降雨", "今天天氣如何啊", "幾度"]):
        has_city = any(key in user_input_lower for key in CITY_MAPPING.keys())
        if not has_city:
            line_bot_api.reply_message(event.reply_token, TextMessage(text="🌡️ 沒問題！請問妳想了解哪一個縣市的『天氣與氣溫』呢？\n(例如：台中、宜蘭、屏東)"))
            return
            
    elif any(k in user_input_lower for k in ["空氣", "空氣品質", "aqi", "幫我看現在空氣品質好不好", "pm25"]):
        has_city = any(key in user_input_lower for key in CITY_MAPPING.keys())
        if not has_city:
            line_bot_api.reply_message(event.reply_token, TextMessage(text="🍃 收到！請問妳要看哪一個縣市的『空氣品質AQI』呢？\n(example：新北、台南、馬祖)"))
            return
            
    elif any(k in user_input_lower for k in ["紫外線", "紫外線指數", "uv", "太陽好大！幫我查一下紫外線"]):
        has_city = any(key in user_input_lower for key in CITY_MAPPING.keys())
        if not has_city:
            line_bot_api.reply_message(event.reply_token, TextMessage(text="🕶️ OK！防曬大作戰～請問想查哪一個縣市的『紫外線指數』呢？\n(example：彰化、澎湖、台北)"))
            return

    # ─── 第二階段：解析縣市關鍵字 ───
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
        
        # ─── 真正的獨立路由判斷（這一次把無關資訊完全摘除了！） ───
        
        # 1. 空氣卡
        if any(k in user_input_lower for k in ["空氣", "aqi", "pm25"]):
            flex_contents = generate_card_air(target_county, city_weather)
            line_bot_api.reply_message(
                event.reply_token,
                FlexSendMessage(alt_text=f"{target_county}空氣品質觀測", contents=flex_contents)
            )
            
        # 2. 紫外線卡
        elif any(k in user_input_lower for k in ["紫外線", "uv"]):
            flex_contents = generate_card_uv(target_county, city_weather)
            line_bot_api.reply_message(
                event.reply_token,
                FlexSendMessage(alt_text=f"{target_county}紫外線監測", contents=flex_contents)
            )
            
        # 3. 天氣卡
        elif any(k in user_input_lower for k in ["天氣", "溫度", "氣溫", "幾度", "降雨"]):
            flex_contents = generate_card_weather(target_county, city_weather)
            line_bot_api.reply_message(
                event.reply_token,
                FlexSendMessage(alt_text=f"{target_county}天氣氣溫預報", contents=flex_contents)
            )
            
        # 4. 綜合卡
        else:
            flex_contents = generate_card_all(target_county, city_weather)
            line_bot_api.reply_message(
                event.reply_token,
                FlexSendMessage(alt_text=f"{target_county}綜合氣象情報", contents=flex_contents)
            )
            
    else:
        line_bot_api.reply_message(
            event.reply_token,
            TextMessage(text="呀！小管家看不懂這個指令耶～🤯\n\n"
                             "請直接點選下方【LINE選單】，或是試著這樣對我打字喔：\n"
                             "👉「台中」 (看綜合圖卡)\n"
                             "👉「台中天氣」 (看獨立天氣卡)\n"
                             "👉「台中空氣」 (看獨立空氣卡)\n"
                             "👉「台中紫外線」 (看獨立防曬卡)")
        )

app.debug = False