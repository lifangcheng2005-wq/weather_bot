import os
import json
import time
import requests
import random
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, LocationMessage, FlexSendMessage

app = Flask(__name__)

# --- 1. 從 Vercel 環境變數讀取金鑰 ---
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET")
MOENV_API_KEY = os.environ.get("MOENV_API_KEY")
CWA_API_KEY = os.environ.get("CWA_API_KEY")
FIREBASE_DB_URL = os.environ.get("FIREBASE_DB_URL")

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# --- 2. 記憶體快取設計 ---
cache = {
    "data": None,
    "last_updated": 0
}
CACHE_DURATION = 1200  

with open("city_mapping.json", "r", encoding="utf-8") as f:
    CITY_MAPPING = json.load(f)

# --- 3. Firebase 狀態讀寫函式庫 ---
def get_user_state(user_id):
    if not FIREBASE_DB_URL: return None
    try:
        url = f"{FIREBASE_DB_URL.rstrip('/')}/users/{user_id}.json"
        res = requests.get(url, timeout=5).json()
        return res if res else {}
    except Exception as e:
        print(f"❌ Firebase 讀取異常: {e}")
        return None

def set_user_state(user_id, query_type):
    if not FIREBASE_DB_URL: return
    try:
        url = f"{FIREBASE_DB_URL.rstrip('/')}/users/{user_id}.json"
        data = {"query_type": query_type, "timestamp": time.time()}
        requests.put(url, json=data, timeout=5)
    except Exception as e:
        print(f"❌ Firebase 寫入異常: {e}")

def clear_user_state(user_id):
    if not FIREBASE_DB_URL: return
    try:
        url = f"{FIREBASE_DB_URL.rstrip('/')}/users/{user_id}.json"
        requests.delete(url, timeout=5)
    except Exception as e:
        print(f"❌ Firebase 刪除異常: {e}")


# --- 4. 🧠 新增：台灣 GPS 經緯度精準縣市判定器 ---
def get_city_by_coordinates(lat, lon):
    """資管演算法：利用經緯度邊界，秒判斷使用者目前在台灣哪一個縣市"""
    # 這裡用大略的台灣縣市地理方格進行極速分流判斷
    if lat > 25.1: return "基隆"
    elif lat > 24.95 and lon > 121.45: return "台北"
    elif lat > 24.85 and lon > 121.2: return "新北"
    elif lat > 24.75 and lon > 121.0: return "桃園"
    elif lat > 24.6 and lon > 121.5: return "宜蘭"
    elif lat > 24.5 and lon <= 121.1: return "新竹"
    elif lat > 24.3 and lon > 121.3: return "花蓮"
    elif lat > 24.2 and lon <= 120.9: return "苗栗"
    elif lat > 23.95 and lon <= 120.8: return "台中"
    elif lat > 23.8 and lon <= 120.6: return "彰化"
    elif lat > 23.7 and lon > 120.7: return "南投"
    elif lat > 23.55 and lon <= 120.4: return "雲林"
    elif lat > 23.40 and lon <= 120.5: return "嘉義"
    elif lat > 22.85 and lon <= 120.4: return "台南"
    elif lat > 22.45 and lon <= 120.4: return "高雄"
    elif lat > 22.2:
        if lon > 120.8: return "台東"
        else: return "屏東"
    # 離島判定
    if lon < 119.7: return "澎湖"
    if lat > 26.0: return "馬祖"
    if lon < 118.5: return "金門"
    
    return "台中" # 預設兜底防呆縣市


# --- 5. 核心資料撈取與 JSON 整合函式 ---
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


# --- 6. 💬 史詩級擴充：超毒舌/超甜心隨機生活貼心提醒金句庫 ---
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
                "偵測到天空今天走『愛哭鬼』路線，降雨機率直接灌頂！沒帶雨具妳會哭，真的！🥺",
                "外面隨時會倒大水！聽管家的話把傘拿好，柏油路今天不是很想跟妳摔車貼貼喔～🚗"
            ]))
        elif pop_val >= 40:
            reminders.append(random.choice([
                "今天的天空有點傲嬌，降雨機率半吊子，折疊傘還是塞進包包吧，防呆用！🎒",
                "雨神今天可能心情好，降雨機率有一半！賭一把不帶傘？我賭妳會輸，快去拿傘！🔮",
                "天空陰陰的像在生悶氣，雖然還沒下，但乖乖帶把傘總比在路邊淋雨當文藝女主角好吧？🎬"
            ]))
        elif "雨" in data['wx']:
            reminders.append(random.choice([
                "外面現在正在下雨！路上注意安全，行車距離保持好，衣服濕了會感冒的捏！🌧️",
                "嘩啦啦啦～雨滴正在唱歌。聽管話，乖乖待在室內，別出去外面瘋狂奔跑啦～🏡"
            ]))
        else:
            reminders.append(random.choice([
                "目前看來是不帶傘也穩過的一天！快出門去踩踩陽光，別把自己曬發霉啦～☀️",
                "天氣晴朗小福星！這天氣完美到不出去喝杯奶茶都對不起自己的扣達了！🥤",
                "偵測到晴天 buff 已疊滿！還不快出去玩，要把自己當作馬鈴薯種在沙發上嗎？🥔",
                "天空乾乾淨淨，完全不用帶傘！是個適合拍美照發限動的完美日子，出發囉！📸"
            ]))

    if query_type in ['all', 'air']:
        if "普通" in aqi_status:
            reminders.append(random.choice([
                "今天的空氣雖然及格但很邊緣，過敏小可憐們出門記得把口罩戴好，別一直打噴嚏！🤧",
                "空氣指標正在走鋼索，防禦力有點低。過敏星人如果不想擤衛生紙到鼻子破皮，乖乖戴口罩！🧻"
            ]))
        elif "不健康" in aqi_status or "對敏感族群" in aqi_status:
            reminders.append(random.choice([
                "今天窗外空氣有點『有毒』！乖，暫時別去外面瘋狂跑步，口罩快拉好，保護好妳高貴的肺！⚠️",
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


# --- 7. 五大視覺 Flex 卡片工廠 (說明書追加「一鍵GPS位置傳送」) ---

def generate_guide_card():
    """💡 升級版：說明書底部增加「傳送地理位置」動作按鈕"""
    return {
      "type": "bubble", "size": "mega",
      "header": {
        "type": "box", "layout": "vertical", "backgroundColor": "#8338ec",
        "contents": [
          {"type": "text", "text": "📖 氣象小管家超強說明書", "weight": "bold", "color": "#FFFFFF", "size": "sm"},
          {"type": "text", "text": "如何調教小管家？", "weight": "bold", "size": "xl", "color": "#FFFFFF", "margin": "md"}
        ]
      },
      "body": {
        "type": "box", "layout": "vertical", "spacing": "sm",
        "contents": [
          {"type": "text", "text": "嗨！我是妳的專屬氣象管家 🧞‍♂️\n除了點下方選單或手打「台中天氣」之外，現在管家解鎖了『即時 GPS 位置導航』功能囉！", "size": "xs", "color": "#555555", "wrap": True},
          {"type": "separator", "margin": "sm"},
          {"type": "box", "layout": "vertical", "spacing": "xs", "contents": [
              {"type": "text", "text": "🖼️ 盲打城市名 (例如「台南」) -> 噴綜合大圖卡", "size": "xs", "color": "#666666"},
              {"type": "text", "text": "🌡️ 縣市+天氣 (例如「台北天氣」) -> 噴純天氣氣溫卡", "size": "xs", "color": "#3a86ff", "weight": "bold"},
              {"type": "text", "text": "🍃 縣市+空氣 (例如「高雄空氣」) -> 噴純空氣品質卡", "size": "xs", "color": "#2a9d8f", "weight": "bold"},
              {"type": "text", "text": "🕶️ 縣市+防曬 (例如「宜蘭紫外線」) -> 噴純防曬卡", "size": "xs", "color": "#e76f51", "weight": "bold"}
          ]},
          {"type": "separator", "margin": "md"},
          # 🚀 亮點技術：這顆按鈕按下去，會直接彈出 LINE 的內建地圖讓使用者一鍵發送位置！
          {
            "type": "button", "style": "primary", "color": "#ff006e", "height": "sm", "margin": "md",
            "action": {
              "type": "uri",
              "label": "📍 點我一鍵發送當前 GPS 位置",
              "uri": "line://nv/location"
            }
          }
        ]
      }
    }

def create_pure_weather_card(city_name, data):
    reminder_text = get_warm_reminder(data, 'weather')
    return {
      "type": "bubble", "size": "mega",
      "header": {
        "type": "box", "layout": "vertical", "backgroundColor": "#3a86ff",
        "contents": [
          {"type": "text", "text": "🌡️ 獨立天氣與氣溫觀測", "weight": "bold", "color": "#FFFFFF", "size": "sm"},
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
              {"type": "text", "text": "🌧️ 貼心提醒：", "weight": "bold", "size": "xs", "color": "#3a86ff"},
              {"type": "text", "text": reminder_text, "size": "xs", "color": "#4a5568", "wrap": True, "margin": "xs"}
            ]
          }
        ]
      }
    }

def create_pure_air_card(city_name, data):
    reminder_text = get_warm_reminder(data, 'air')
    return {
      "type": "bubble", "size": "mega",
      "header": {
        "type": "box", "layout": "vertical", "backgroundColor": "#2a9d8f",
        "contents": [
          {"type": "text", "text": "🍃 獨立空氣品質監測", "weight": "bold", "color": "#FFFFFF", "size": "sm"},
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
              {"type": "text", "text": "😷 貼心提醒：", "weight": "bold", "size": "xs", "color": "#2a9d8f"},
              {"type": "text", "text": reminder_text, "size": "xs", "color": "#2e7d32", "wrap": True, "margin": "xs"}
            ]
          }
        ]
      }
    }

def create_pure_uv_card(city_name, data):
    reminder_text = get_warm_reminder(data, 'uv')
    return {
      "type": "bubble", "size": "mega",
      "header": {
        "type": "box", "layout": "vertical", "backgroundColor": "#e76f51",
        "contents": [
          {"type": "text", "text": "🕶️ 獨立紫外線監測", "weight": "bold", "color": "#FFFFFF", "size": "sm"},
          {"type": "text", "text": city_name, "weight": "bold", "size": "xxl", "color": "#FFFFFF", "margin": "md"}
        ]
      },
      "body": {
        "type": "box", "layout": "vertical",
        "contents": [
          {"type": "box", "layout": "horizontal", "contents": [{"type": "text", "text": "☀️ 紫外線指數", "color": "#aaaaaa", "size": "sm"}, {"type": "text", "text": data['uvi'], "align": "end", "size": "md", "weight": "bold", "color": "#e76f51"}]},
          {"type": "separator", "margin": "md"},
          {"type": "box", "layout": "horizontal", "margin": "md", "contents": [{"type": "text", "text": "🛡️ 曝曬風險", "color": "#aaaaaa", "size": "sm"}, {"type": "text", "text": data['uvi_level'], "align": "end", "size": "sm", "weight": "bold"}]},
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


# --- 8. Webhook 接收路由 ---
@app.route("/webhook", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature', '')
    body = request.get_data(as_text=True)
    try: handler.handle(body, signature)
    except InvalidSignatureError: abort(400)
    return 'OK'


# --- 9. 🚀 技術亮點：新增 GPS 地理位置訊息處理器 ---
@handler.add(MessageEvent, message=LocationMessage)
def handle_location_message(event):
    user_id = event.source.user_id
    lat = event.message.latitude   # 抓取緯度
    lon = event.message.longitude  # 抓取經度
    
    # 利用我們寫的地理演算法，秒反推縣市名稱！
    detected_city_name = get_city_by_coordinates(lat, lon)
    
    # 讀取 Firebase 記憶看他剛才是點了什麼功能
    user_state = get_user_state(user_id) or {}
    saved_type = user_state.get("query_type", "all")
    
    all_data = fetch_all_weather_data()
    # 統一補上「市」或「縣」以利政府 API 對齊
    full_county_name = detected_city_name + ("縣" if detected_city_name in ["彰化", "南投", "雲林", "嘉義", "屏東", "宜蘭", "花蓮", "台東", "澎湖", "金門", "連江"] else "市")
    if detected_city_name == "馬祖": full_county_name = "連江縣"
        
    city_weather = all_data.get(full_county_name.replace("台", "臺"), {
        "wx": "情報更新中", "pop": "0", "min_t": "--", "max_t": "--",
        "aqi": "讀取中", "aqi_status": "請稍後", "uvi": "0", "uvi_level": "一般"
    })
    
    # 依照多輪記憶分流發送卡片
    if saved_type == "air":
        flex_contents = create_pure_air_card(full_county_name, city_weather)
    elif saved_type == "uv":
        flex_contents = create_pure_uv_card(full_county_name, city_weather)
    elif saved_type == "weather":
        flex_contents = create_pure_weather_card(full_county_name, city_weather)
    else:
        flex_contents = generate_card_all(full_county_name, city_weather)
        
    # 發送卡片並清空狀態
    line_bot_api.reply_message(event.reply_token, FlexSendMessage(alt_text=f"{full_county_name}即時觀測", contents=flex_contents))
    clear_user_state(user_id)


# --- 10. 狀態感應文字訊息處理器 ---
@handler.add(MessageEvent, message=TextMessage)
def handle_text_message(event):
    user_id = event.source.user_id  
    user_input = event.message.text.strip()
    user_input_lower = user_input.lower()
    
    if user_input in ["我要查氣象", "呼叫管家！我想看今日氣象圖卡", "查氣象", "綜合氣象"]:
        set_user_state(user_id, "all")
        line_bot_api.reply_message(event.reply_token, TextMessage(text="☀️ 好喔！想要查詢哪一個縣市的『綜合氣象卡片』呢？\n(例如：台中、台北、高雄)\n\n📍 提示：也可以直接發送您的「GPS位置」給管家喔！"))
        return
        
    elif any(k in user_input_lower for k in ["天氣", "氣溫", "溫度", "降雨", "今天天氣如何啊", "幾度"]):
        if not any(key in user_input_lower for key in CITY_MAPPING.keys()):
            set_user_state(user_id, "weather")
            line_bot_api.reply_message(event.reply_token, TextMessage(text="🌡️ 沒問題！請問妳想了解哪一個縣市的『天氣與氣溫』呢？\n(例如：台中、宜蘭、屏東)\n\n📍 提示：也可以直接發送您的「GPS位置」給管家喔！"))
            return
            
    elif any(k in user_input_lower for k in ["空氣", "空氣品質", "aqi", "幫我看現在空氣品質好不好", "pm25"]):
        if not any(key in user_input_lower for key in CITY_MAPPING.keys()):
            set_user_state(user_id, "air")
            line_bot_api.reply_message(event.reply_token, TextMessage(text="🍃 收到！請問妳要看哪一個縣市的『空氣品質AQI』呢？\n(example：新北、台南、馬祖)\n\n📍 提示：也可以直接發送您的「GPS位置」給管家喔！"))
            return
            
    elif any(k in user_input_lower for k in ["紫外線", "紫外線指數", "uv", "太陽好大！幫我查一下紫外線"]):
        if not any(key in user_input_lower for key in CITY_MAPPING.keys()):
            set_user_state(user_id, "uv")
            line_bot_api.reply_message(event.reply_token, TextMessage(text="🕶️ OK！防曬大作戰～請問想查哪一個縣市的『紫外線指數』呢？\n(example：彰化、澎湖、台北)\n\n📍 提示：也可以直接發送您的「GPS位置」給管家喔！"))
            return

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
        
        user_state = get_user_state(user_id) or {}
        saved_type = user_state.get("query_type", "all")
        
        current_query = saved_type
        if any(k in user_input_lower for k in ["空氣", "aqi", "pm25"]): current_query = "air"
        elif any(k in user_input_lower for k in ["紫外線", "uv"]): current_query = "uv"
        elif any(k in user_input_lower for k in ["天氣", "溫度", "氣溫", "幾度", "降雨"]): current_query = "weather"
        
        if current_query == "air":
            flex_contents = create_pure_air_card(target_county, city_weather)
        elif current_query == "uv":
            flex_contents = create_pure_uv_card(target_county, city_weather)
        elif current_query == "weather":
            flex_contents = create_pure_weather_card(target_county, city_weather)
        else:
            flex_contents = generate_card_all(target_county, city_weather)
            
        line_bot_api.reply_message(event.reply_token, FlexSendMessage(alt_text=f"{target_county}氣象觀測", contents=flex_contents))
        clear_user_state(user_id)
            
    else:
        guide_contents = generate_guide_card()
        line_bot_api.reply_message(event.reply_token, FlexSendMessage(alt_text=f"氣象小管家使用說明書", contents=guide_contents))

app.debug = False