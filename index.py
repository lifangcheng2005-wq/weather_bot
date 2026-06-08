import os
import json
import requests
from flask import Flask, request, abort, make_response, jsonify

from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

from google import genai
from google.genai import types

app = Flask(__name__)

# 從系統環境變數讀取
line_bot_api = LineBotApi(os.getenv('LINE_CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.getenv('LINE_CHANNEL_SECRET'))
client = genai.Client()

@app.route("/")
def home():
    return "<h1>LINE 氣象小秘書伺服器運作中！</h1>"

# =====================================================================
# 1. LINE Bot 接收端點
# =====================================================================
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature', '')
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_msg = event.message.text.strip()
    
    # 呼叫核心函式取得回覆
    reply_text = ask_gemini_weather(user_msg)
    
    # 加固發送機制，確保不論如何都強迫發送字串
    try:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=str(reply_text))
        )
    except Exception as e:
        print(f"LINE Reply Error: {e}")

# =====================================================================
# 2. Dialogflow Fulfillment 接收端點
# =====================================================================
@app.route("/webhook", methods=["POST"])
def webhook():
    req = request.get_json(force=True)
    action = req["queryResult"].get("action", "")
    user_query_text = req["queryResult"].get("queryText", "")
    
    info = "天氣查詢服務暫時繁忙，請稍後再試。"
    
    if action in ["queryWeather", "input.unknown"]:
        try:
            geo_city = req["queryResult"]["parameters"].get("geo-city", "")
            if geo_city:
                city_formatted = geo_city.strip()
                if not city_formatted.endswith(('市', '縣')):
                    city_formatted += "縣" if city_formatted in ["彰化", "南投", "雲林", "屏東", "臺東", "台東", "花蓮", "澎湖", "金門", "連江"] else "市"
                info = ask_gemini_weather(f"我要查詢 {city_formatted}。原句：{user_query_text}")
            else:
                info = ask_gemini_weather(user_query_text)
        except Exception as e:
            info = f"連線診斷提示：{str(e)}"
    else:
        info = "抱歉，此功能尚未設定對應的 Action。"

    response_data = {
        "fulfillmentText": str(info),
        "fulfillmentMessages": [{"text": {"text": [str(info)]}}]
    }
    return jsonify(response_data)

# =====================================================================
# 3. 核心功能：分析意圖、線上即時抓取 JSON、Gemini 生成回覆
# =====================================================================
def ask_gemini_weather(user_input_string):
    # 預設城市與意圖
    city = "臺中市"
    intent = "all"
    
    # 如果原句裡本來就有寫好城市，我們直接提取，避免多戳一次 Gemini 造成超時
    for c in ["臺北", "台北", "新北", "桃園", "臺中", "台中", "臺南", "台南", "高雄", "基隆", "新竹", "苗栗", "彰化", "南投", "雲林", "嘉義", "屏東", "宜蘭", "花蓮", "臺東", "台東", "澎湖", "金門", "連江"]:
        if c in user_input_string:
            city = c.replace("台北", "臺北市").replace("臺北", "臺北市").replace("台中", "臺中市").replace("臺中", "臺中市").replace("台南", "臺南市").replace("臺南", "臺南市").replace("新北", "新北市").replace("桃園", "桃園市").replace("高雄", "高雄市")
            if not city.endswith(('市', '縣')):
                city += "市"
            break

    weather_info = "無查詢此項目"
    aqi_info = "無查詢此項目"
    uv_info = "無查詢此項目"
    
    # 1. 抓取氣象署資料
    cwa_key = os.getenv('CWA_API_KEY')
    url_weather = f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-C0032-001?Authorization={cwa_key}&locationName={city}"
    try:
        res = requests.get(url_weather, timeout=3).json()
        loc = res["records"]["location"][0]
        state = loc["weatherElement"][0]["time"][0]["parameter"]["parameterName"]
        rain = loc["weatherElement"][1]["time"][0]["parameter"]["parameterName"]
        min_t = loc["weatherElement"][2]["time"][0]["parameter"]["parameterName"]
        max_t = loc["weatherElement"][4]["time"][0]["parameter"]["parameterName"]
        weather_info = f"天氣現況：{state}，降雨機率：{rain}%，氣溫：{min_t}°C ~ {max_t}°C"
    except:
        weather_info = "暫時無法取得氣象預報"

    # 2. 抓取環境部資料（空氣品質）
    moenv_key = os.getenv('MOENV_API_KEY')
    url_aqi = f"https://data.moenv.gov.tw/api/v2/aqx_p_432?api_key={moenv_key}&format=json"
    try:
        res = requests.get(url_aqi, timeout=3).json()
        search_city = city.replace("臺", "台")
        records = [r for r in res['records'] if r['county'] == search_city]
        if records:
            aqi_info = f"AQI指數：{records[0]['aqi']}（{records[0]['status']}）"
    except:
        aqi_info = "暫時無法取得空氣品質"

    # 3. 嘗試由 Gemini 生成智慧型外殼回覆
    summary_instruction = (
        "你是一個貼心的生活氣象小秘書。請根據提供的即時數據整理成一篇有條理、溫暖易讀的手機訊息回覆。\n"
        "多用換行與豐富的 emoji (如 🌤️, 🌧️)。"
    )
    data_payload = f"城市：{city}\n數據：{weather_info} / {aqi_info}"
    
    try:
        ai_reply = client.models.generate_content(
            model='gemini-3.5-flash',
            contents=data_payload,
            config=types.GenerateContentConfig(system_instruction=summary_instruction)
        )
        if ai_reply.text:
            return ai_reply.text
    except Exception as e:
        # 🌟 萬一 Gemini API 卡住或金鑰失效，啟動「純文字保底機制」，絕對要讓使用者看到資料！
        pass
        
    return f"🌤️ 報告！最新即時氣象數據如下：\n📍 查詢城市：{city}\n📦 {weather_info}\n🍃 {aqi_info}\n✨ 祝妳有個美好的一天！"

if __name__ == "__main__":
    app.run(debug=True)