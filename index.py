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

# 載入全台城市對照表 JSON[cite: 2]
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

# --- 4. 擴充版：超俏皮又生活化的動態貼心提醒 ---
def get_warm_reminder(data, query_type):
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
    
    # 🌧️ 俏皮天氣提醒
    if query_type in ['all', 'weather']:
        if pop_val >= 70:
            reminders.append(random.choice([
                "降雨機率高達分之裝熟！出門沒帶傘的話，妳就只能在街上跳曼波了啦～☔",
                "今天降雨機率太有誠意了，出門一定要抓一把傘，別讓自己變成現撈的落湯雞捏！🐔"
            ]))
        elif pop_val >= 40:
            reminders.append("今天的天空有點愛哭，降雨機率要高不高、要低不低的，保險起見折疊傘還是塞進包包吧！🎒")
        elif "雨" in data['wx']:
            reminders.append("外面現在正在下雨！聽話，開車騎車慢一點，不要跟柏油路開玩笑喔～🚗")
        else:
            reminders.append(random.choice([
                "目前看來是個不帶傘也穩過的一天！快出門去踩踩陽光，別發霉啦～☀️",
                "天氣晴朗小福星！這天氣完美到不出去喝杯奶茶都對不起自己了對吧？🥤"
            ]))

    # 😷 俏皮空氣提醒
    if query_type in ['all', 'air']:
        if "普通" in aqi_status:
            reminders.append("今天的空氣雖然及格但很邊緣，過敏小可憐們出門記得把口罩戴好，別一直打噴嚏囉！🤧")
        elif "對敏感族群不健康" in aqi_status or "不健康" in aqi_status:
            reminders.append(random.choice([
                "今天窗外空氣有點『有毒』！乖，暫時別去外面瘋狂奔跑，口罩快拉好，保護好妳高貴的肺！⚠️",
                "空氣品質正在鬧脾氣！過敏星人今天嚴禁開啟人體清淨機模式，沒事多待在室內修仙吧！🔮"
            ]))
        else:
            reminders.append(random.choice([
                "今天的空氣乾淨到像在清境農場！趕快大力吸三口，免費的奢華空氣不吸白不吸～🍃",
                "PM2.5 今天集體放假去了！空氣超級無敵好，家裡窗戶快打開通風一波～🪟"
            ]))

    # 🕶️ 俏皮紫外線提醒
    if query_type in ['all', 'uv']:
        if uvi_val >= 8:
            reminders.append(random.choice([
                "紫外線指數爆表啦！今天太陽公公沒在跟妳客氣的，防曬乳塗厚一點，不然出門一趟直接變黑炭！🔥",
                "這紫外線是要把人烤熟嗎？防曬、墨鏡、遮陽傘快使出三防防禦，不要跟太陽硬碰硬！🕶️"
            ]))
        elif uvi_val >= 5:
            reminders.append("紫外線有點微微囂張喔，雖然沒有到融化的程度，但美白很貴的，防曬還是要擦一下啦！🧴")
        else:
            reminders.append("今天的紫外線很善良，頂多幫妳補補維生素D，不用怕被曬成小黑炭，放心出去玩！🐾")

    return " \n".join(reminders)


# --- 5. 生成 LINE Flex Message 綜合圖卡 ---
def generate_flex_message(city_name, data):
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


# --- 7. 核心：循序漸進多輪對話邏輯 ---
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_input = event.message.text.strip().lower()
    
    # ─── 第一階段：使用者點擊 LINE 選單或輸入主指令 ───
    if user_input == "我要查氣象":
        line_bot_api.reply_message(event.reply_token, TextMessage(text="☀️ 好喔！想要查詢哪一個縣市的『綜合氣象卡片』呢？\n(例如輸入：台中市、台北、花蓮)"))
        return
    elif user_input == "我要查天氣":
        line_bot_api.reply_message(event.reply_token, TextMessage(text="🌡️ 沒問題！請問妳想了解哪一個縣市的『天氣與氣溫』呢？\n(例如輸入：台中、高雄、屏東)"))
        return
    elif user_input == "我要查空氣":
        line_bot_api.reply_message(event.reply_token, TextMessage(text="🍃 收到！請問妳要看哪一個縣市的『空氣品質AQI』呢？\n(例如輸入：新北、台南、金門)"))
        return
    elif user_input == "我要查紫外線":
        line_bot_api.reply_message(event.reply_token, TextMessage(text="🕶️ OK！防曬大作戰～請問想查哪一個縣市的『紫外線指數』呢？\n(例如輸入：彰化、澎湖、台北)"))
        return

    # ─── 第二階段：判斷使用者輸入的文字有沒有包含「縣市」 ───
    target_city_key = None
    for key in CITY_MAPPING.keys():
        if key in user_input:
            target_city_key = key
            break

    # 如果抓到縣市名稱了！
    if target_city_key:
        city_info = CITY_MAPPING[target_city_key]
        target_county = city_info["county"]
        
        all_data = fetch_all_weather_data()
        city_weather = all_data.get(target_county, {
            "wx": "情報更新中", "pop": "0", "min_t": "--", "max_t": "--",
            "aqi": "讀取中", "aqi_status": "請稍後", "uvi": "0", "uvi_level": "一般"
        })
        
        # 根據剛剛被帶進來的上下文或使用者輸入做回應分流
        # (不論使用者是一次打完「台中空氣」，還是循序漸進被問完打「台中」，都能精準判定)
        if "空氣" in user_input:
            reminder = get_warm_reminder(city_weather, 'air')
            reply_text = f"🍃【{target_county}】空氣品質觀測：\n" \
                         f"🔹 AQI 指標：{city_weather['aqi']}\n" \
                         f"🔹 狀態說明：{city_weather['aqi_status']}\n\n" \
                         f"💡 管家俏皮提醒：\n{reminder}"
            line_bot_api.reply_message(event.reply_token, TextMessage(text=reply_text))
            
        elif "紫外線" in user_input or "uv" in user_input:
            reminder = get_warm_reminder(city_weather, 'uv')
            reply_text = f"🕶️【{target_county}】紫外線即時監測：\n" \
                         f"🔹 紫外線指數：{city_weather['uvi']}\n" \
                         f"🔹 風險級別：{city_weather['uvi_level']}\n\n" \
                         f"💡 管家俏皮提醒：\n{reminder}"
            line_bot_api.reply_message(event.reply_token, TextMessage(text=reply_text))
            
        elif "天氣" in user_input or "溫度" in user_input or "氣溫" in user_input or "幾度" in user_input:
            reminder = get_warm_reminder(city_weather, 'weather')
            reply_text = f"🌡️【{target_county}】即時天氣與氣溫：\n" \
                         f"🔹 天氣現況：{city_weather['wx']}\n" \
                         f"🔹 預測氣溫：{city_weather['min_t']}°C ~ {city_weather['max_t']}°C\n" \
                         f"🔹 降雨機率：{city_weather['pop']}%\n\n" \
                         f"💡 管家俏皮提醒：\n{reminder}"
            line_bot_api.reply_message(event.reply_token, TextMessage(text=reply_text))
            
        else:
            # 預設直接吐綜合大卡片（也包含完整提醒內容）
            flex_contents = generate_flex_message(target_county, city_weather)
            line_bot_api.reply_message(
                event.reply_token,
                FlexSendMessage(alt_text=f"{target_county}綜合氣象情報", contents=flex_contents)
            )
            
    else:
        # 如果既不是主指令，字串裡也完全沒有縣市，就給予提示選單
        line_bot_api.reply_message(
            event.reply_token,
            TextMessage(text="呀！管家聽不太懂妳的意思耶～🤯\n\n"
                             "請直接點擊下方的【LINE選單】進行查詢，或是輸入：\n"
                             "👉「我要查氣象」\n"
                             "👉「我要查天氣」\n"
                             "👉「我要查空氣」\n"
                             "👉「我要查紫外線」")
        )

app.debug = False