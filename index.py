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

# 從環境變數中讀取 API 金鑰（請確認 Vercel 後台有填入這 5 個 Key）
line_bot_api = LineBotApi(os.getenv('LINE_CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.getenv('LINE_CHANNEL_SECRET'))
client = genai.Client()

@app.route("/")
def home():
    return "<h1>LINE 雙向精準氣象小秘書伺服器運作中！</h1>"

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
    reply_text = ask_gemini_weather(user_msg)
    try:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=str(reply_text)))
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
    
    info = "小秘書正在全力解讀氣象數據中..."
    
    if action in ["queryWeather", "input.unknown"]:
        try:
            # 直接將原始問題丟給核心函數，讓內部強大邏輯處理 22 縣市與單獨查詢
            info = ask_gemini_weather(user_query_text)
        except Exception as e:
            info = f"天氣服務繁忙，診斷提示：{str(e)}"
    else:
        info = "抱歉，此功能尚未設定對應的 Action。"

    response_data = {
        "fulfillmentText": str(info),
        "fulfillmentMessages": [{"text": {"text": [str(info)]}}]
    }
    return jsonify(response_data)

# =====================================================================
# 3. 台灣 22 縣市對齊邏輯與模糊比對工具
# =====================================================================
def get_standard_taiwan_city(text):
    """
    完整的台灣 22 縣市對照字典
    能將任何「台中」、「台北」、「嘉義縣」、「馬祖」等輸入自動校正為氣象署標準格式。
    """
    city_map = {
        # 直轄市與市
        "基隆": "基隆市", "基隆市": "基隆市",
        "台北": "臺北市", "臺北": "臺北市", "台北市": "臺北市", "臺北市": "臺北市",
        "新北": "新北市", "新北市": "新北市",
        "桃園": "桃園市", "桃園市": "桃園市",
        "新竹市": "新竹市", # 新竹市與新竹縣分開處理
        "台中": "臺中市", "臺中": "臺中市", "台中市": "臺中市", "臺中市": "臺中市",
        "嘉義市": "嘉義市", # 嘉義市與嘉義縣分開處理
        "台南": "臺南市", "臺南": "臺南市", "台南市": "臺南市", "臺南市": "臺南市",
        "高雄": "高雄市", "高雄市": "高雄市",
        
        # 縣
        "新竹縣": "新竹縣",
        "苗栗": "苗栗縣", "苗栗縣": "苗栗縣",
        "彰化": "彰化縣", "彰化縣": "彰化縣",
        "南投": "南投縣", "南投縣": "南投縣",
        "雲林": "雲林縣", "雲林縣": "雲林縣",
        "嘉義縣": "嘉義縣",
        "屏東": "屏東縣", "屏東縣": "屏東縣",
        "宜蘭": "宜蘭縣", "宜蘭縣": "宜蘭縣",
        "花蓮": "花蓮縣", "花蓮縣": "花蓮縣",
        "台東": "臺東縣", "臺東": "臺東縣", "台東縣": "臺東縣", "臺東縣": "臺東縣",
        "澎湖": "澎湖縣", "澎湖縣": "澎湖縣",
        "金門": "金門縣", "金門縣": "金門縣",
        "連江": "連江縣", "連江縣": "連江縣", "馬祖": "連江縣"
    }

    # 1. 優先掃描對照表
    for key, val in city_map.items():
        if key in text:
            return val

    # 2. 針對新竹/嘉義沒指明縣市時的預設模糊規則
    if "新竹" in text:
        return "新竹縣" if "縣" in text else "新竹市"
    if "嘉義" in text:
        return "嘉義縣" if "縣" in text else "嘉義市"

    return None

def clean_county_name(name):
    """
    極重要！清除「臺、台」與「縣、市」字尾。
    將「臺中市」與「台中市」同時轉換成「台中」進行無痛比對，消滅政府兩大 API 命名不一的臭蟲。
    """
    if not name:
        return ""
    return name.replace("臺", "台").replace("縣", "").replace("市", "").strip()

# =====================================================================
# 4. 氣象大腦核心：AI 意圖過濾 + 精準單獨查詢
# =====================================================================
def ask_gemini_weather(user_input_string):
    # 【第一階段：城市對齊】
    # 優先使用 Python 內建的 22 縣市高速字典過濾
    city = get_standard_taiwan_city(user_input_string)
    
    # 萬一 Python 字典沒掃到（例如使用者打「那邊天氣如何」），再交給 Gemini 來預設或推測
    intent_instruction = (
        "你是一個天氣意圖分析專家。請分析使用者的輸入，並分類他想單獨查詢的項目：\n"
        "1. 縣市名稱：如果分析不出台灣的具體縣市，請一律預設回傳 '臺中市'。\n"
        "2. 意圖分類 (intent)：\n"
        "   - 若提到 '下雨', '降雨', '機率', '雨傘', '雨' -> 意圖為 'rain'\n"
        "   - 若提到 '空氣', '品質', 'AQI', 'PM2.5', '細懸浮微粒' -> 意圖為 'aqi'\n"
        "   - 若提到 '紫外線', '防曬', '太陽', 'UV' -> 意圖為 'uv'\n"
        "   - 若純問 '天氣', '氣溫', '冷不冷' 或未特別指明單獨項目 -> 意圖為 'all'\n"
        "請只回傳標準 JSON 格式，不帶 Markdown。範例：{\"city\": \"臺中市\", \"intent\": \"rain\"}"
    )
    
    intent = "all"
    try:
        intent_response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=user_input_string,
            config=types.GenerateContentConfig(
                system_instruction=intent_instruction,
                response_mime_type="application/json"
            )
        )
        info_dict = json.loads(intent_response.text)
        if not city:
            city = info_dict.get("city", "臺中市")
        intent = info_dict.get("intent", "all")
    except:
        if not city:
            city = "臺中市"
        intent = "all"

    # 【第二階段：精準資料抓取】
    # 建立空的資料容器，只會抓取意圖相關的 API
    weather_info = ""
    aqi_info = ""
    uv_info = ""
    
    # 2-1. 氣象署 API（天氣與降雨機率）
    if intent in ['all', 'rain']:
        cwa_key = os.getenv('CWA_API_KEY')
        url_weather = f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-C0032-001?Authorization={cwa_key}&locationName={city}"
        try:
            res = requests.get(url_weather, timeout=4).json()
            loc = res["records"]["location"][0]
            state = loc["weatherElement"][0]["time"][0]["parameter"]["parameterName"]
            rain = loc["weatherElement"][1]["time"][0]["parameter"]["parameterName"]
            min_t = loc["weatherElement"][2]["time"][0]["parameter"]["parameterName"]
            max_t = loc["weatherElement"][4]["time"][0]["parameter"]["parameterName"]
            
            if intent == 'rain':
                weather_info = f"💧 降雨預報：目前 {city} 的降雨機率為 {rain}%！ (天氣現況為：{state})"
            else:
                weather_info = f"天氣現況：{state}，降雨機率：{rain}%，氣溫：{min_t}°C ~ {max_t}°C"
        except Exception as e:
            weather_info = f"暫時無法取得氣象署降雨資料"

    # 2-2. 環境部 API (空氣品質 AQI)
    if intent in ['all', 'aqi']:
        moenv_key = os.getenv('MOENV_API_KEY')
        url_aqi = f"https://data.moenv.gov.tw/api/v2/aqx_p_432?api_key={moenv_key}&format=json"
        try:
            res = requests.get(url_aqi, timeout=4).json()
            target_clean = clean_county_name(city)
            # 利用模糊比對過濾出對應縣市的所有測站
            records = [r for r in res['records'] if clean_county_name(r.get('county', '')) == target_clean]
            if records:
                # 排序取出最新一筆資料
                aqi_val = records[0].get('aqi', '觀測中')
                status = records[0].get('status', '正常')
                pm25 = records[0].get('pm2.5', '觀測中')
                aqi_info = f"🍃 空氣品質：AQI 指數為 {aqi_val}，品質狀態為【{status}】(PM2.5: {pm25} μg/m³)"
            else:
                aqi_info = "空氣品質：目前該縣市無即時監測站數據"
        except Exception as e:
            aqi_info = "空氣品質：環境部空氣品質資料獲取失敗"

    # 2-3. 環境部 API (紫外線 UV)
    if intent in ['all', 'uv']:
        moenv_key = os.getenv('MOENV_API_KEY')
        url_uv = f"https://data.moenv.gov.tw/api/v2/uv_p_01?api_key={moenv_key}&format=json"
        try:
            res = requests.get(url_uv, timeout=4).json()
            target_clean = clean_county_name(city)
            # 利用模糊比對過濾出該縣市的紫外線觀測站
            records = [r for r in res['records'] if clean_county_name(r.get('county', '')) == target_clean]
            if records:
                uwi = records[0].get('uwi', '0')
                uv_info = f"☀️ 紫外線觀測：目前即時紫外線指數為 {uwi}"
            else:
                uv_info = "紫外線：目前該地區無即時紫外線觀測站數據"
        except Exception as e:
            uv_info = "紫外線：環境部紫外線資料獲取失敗"

    # 【第三階段：AI 智慧修飾包裝】
    summary_instruction = (
        "你是一個貼心的生活氣象小秘書。請根據我們為你即時抓取到的台灣各項環境觀測數據，進行最貼切的回覆排版：\n"
        "【重要回覆原則】\n"
        "1. 請仔細檢視『即時數據清單』，『有給你的數據你才回覆』！如果裡面只有降雨機率或只有紫外線數據，代表使用者只想單獨查詢該項目，請針對該項目進行重點熱情回覆與生活提醒，『絕對不准無中生有去虛構其他沒提供給你的空氣、天氣或任何數據』！\n"
        "2. 排版請多換行，並適當添加豐富的 emoji 表情符號（如 🌤️, 🌧️, 😷, 🕶️），確保非常方便用手機閱讀。"
    )
    
    # 建立數據 Payload 餵給 Gemini 包裝
    data_payload = f"【使用者提問】: {user_input_string}\n【分析縣市】: {city}\n【查詢項目】: {intent}\n【即時數據清單】:\n"
    if weather_info: data_payload += f"- 氣象預報: {weather_info}\n"
    if aqi_info: data_payload += f"- 空氣品質: {aqi_info}\n"
    if uv_info: data_payload += f"- 紫外線指數: {uv_info}\n"
    
    try:
        ai_reply = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=data_payload,
            config=types.GenerateContentConfig(system_instruction=summary_instruction)
        )
        if ai_reply.text:
            return ai_reply.text
    except:
        pass
        
    # 保底純文字機制 (萬一 AI 生成超時)
    final_text = f"🌤️ 氣象小秘書觀測報告 (📍{city})：\n"
    if weather_info: final_text += f"{weather_info}\n"
    if aqi_info: final_text += f"{aqi_info}\n"
    if uv_info: final_text += f"{uv_info}\n"
    return final_text

if __name__ == "__main__":
    app.run(debug=True)
```
eof