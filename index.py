import os
import json
import requests
from flask import Flask, request, abort, make_response, jsonify

# 引入 LINE 官方 SDK
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

# 引入 Google 最新 GenAI SDK
from google import genai
from google.genai import types

app = Flask(__name__)

# 直接從系統環境變數讀取（金鑰全部填在 Vercel 後台）
line_bot_api = LineBotApi(os.getenv('LINE_CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.getenv('LINE_CHANNEL_SECRET'))
client = genai.Client() # SDK 會自動尋找並綁定環境變數中的 GEMINI_API_KEY

@app.route("/")
def home():
    return "<h1>LINE Bot & Dialogflow 雙棲氣象伺服器運作中！</h1>"

# =====================================================================
# 1. LINE Bot 接收端點 (對應 LINE 控制台的 Webhook URL 填 /callback)
# =====================================================================
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

# 處理 LINE 文字訊息的邏輯
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_msg = event.message.text.strip()
    
    # 呼叫我們封裝好的「AI 氣象大腦」函式
    reply_text = ask_gemini_weather(user_msg)
    
    # 回傳給 LINE 使用者
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply_text)
    )


# =====================================================================
# 2. Dialogflow Fulfillment 接收端點 (終極通車修正版)
# =====================================================================
@app.route("/webhook", methods=["POST"])
def webhook():
    req = request.get_json(force=True)
    
    # 取得 Dialogflow 目前觸發的 Action 名稱
    action = req["queryResult"]["action"]
    user_query_text = req["queryResult"]["queryText"]
    
    # 當 Dialogflow 觸發氣象查詢
    if action in ["queryWeather", "input.unknown"]:
        try:
            geo_city = req["queryResult"]["parameters"].get("geo-city", "")
            if geo_city:
                user_query_text = f"我要查詢 {geo_city}。原句：{user_query_text}"
        except:
            pass
            
        info = ask_gemini_weather(user_query_text)
    else:
        info = "抱歉，此功能尚未設定對應的 Action。"

    # 🌟 關鍵修正：確保包成符合 Dialogflow 標準的 JSON 格式物件
    response_data = {
        "fulfillmentText": info,
        "fulfillmentMessages": [
            {
                "text": {
                    "text": [info]
                }
            }
        ]
    }

    # 回傳 JSON 並明確指定 Content-Type
    return jsonify(response_data)


# =====================================================================
# 3. 核心封裝函式：負責分析意圖、線上抓 JSON、並請 Gemini 統整回覆
# =====================================================================
def ask_gemini_weather(user_input_string):
    # 階段一：分析縣市與意圖
    intent_instruction = (
        "你是一個天氣查詢意圖分析專家。請分析使用者的輸入，精準提取出「台灣縣市名稱」與「查詢項目」。\n"
        "1. 縣市名稱請一律修正為規範名稱（如：台北->臺北市、台中->臺中市、彰化->彰化縣）。\n"
        "2. 查詢項目請分類為：'all'(全部查/未指定), 'weather'(只查天氣降雨), 'aqi'(只查空氣品質), 'uv'(只查紫外線)。\n"
        "請嚴格只回傳 JSON 格式，不帶任何 Markdown 標籤。範例：{\"city\": \"臺中市\", \"intent\": \"all\"}"
    )
    
    try:
        intent_response = client.models.generate_content(
            model='gemini-3.5-flash',
            contents=user_input_string,
            config=types.GenerateContentConfig(
                system_instruction=intent_instruction,
                response_mime_type="application/json"
            )
        )
        info_dict = json.loads(intent_response.text)
        city = info_dict.get("city", "臺中市")
        intent = info_dict.get("intent", "all")
    except:
        city = "臺中市"
        intent = "all"

    # 階段二：即時線上抓取政府開放資料 JSON
    weather_info = "無查詢此項目"
    aqi_info = "無查詢此項目"
    uv_info = "無查詢此項目"
    
    # 2-1. 氣象署資料
    if intent in ['all', 'weather']:
        cwa_key = os.getenv('CWA_API_KEY')
        url_weather = f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-C0032-001?Authorization={cwa_key}&locationName={city}"
        try:
            res = requests.get(url_weather).json()
            loc = res["records"]["location"][0]
            state = loc["weatherElement"][0]["time"][0]["parameter"]["parameterName"]
            rain = loc["weatherElement"][1]["time"][0]["parameter"]["parameterName"]
            min_t = loc["weatherElement"][2]["time"][0]["parameter"]["parameterName"]
            max_t = loc["weatherElement"][4]["time"][0]["parameter"]["parameterName"]
            weather_info = f"天氣現況：{state}，降雨機率：{rain}%，氣溫：{min_t}°C ~ {max_t}°C"
        except:
            weather_info = "暫時無法取得氣象預報資料"

    # 2-2. 環境部 AQI
    if intent in ['all', 'aqi']:
        moenv_key = os.getenv('MOENV_API_KEY')
        url_aqi = f"https://data.moenv.gov.tw/api/v2/aqx_p_432?api_key={moenv_key}&format=json"
        try:
            res = requests.get(url_aqi).json()
            search_city = city.replace("臺", "台")
            records = [r for r in res['records'] if r['county'] == search_city]
            if records:
                aqi_info = f"AQI 指數：{records[0]['aqi']}，空氣品質狀態：{records[0]['status']}"
        except:
            aqi_info = "暫時無法取得空氣品質資料"

    # 2-3. 環境部 紫外線
    if intent in ['all', 'uv']:
        moenv_key = os.getenv('MOENV_API_KEY')
        url_uv = f"https://data.moenv.gov.tw/api/v2/uv_p_01?api_key={moenv_key}&format=json"
        try:
            res = requests.get(url_uv).json()
            search_city = city.replace("臺", "台")
            records = [r for r in res['records'] if r['county'] == search_city]
            if records:
                uv_info = f"紫外線指數：{records[0]['uwi']}"
        except:
            uv_info = "暫時無法取得紫外線資料"

    # 階段三：Gemini 生成智慧貼心回覆
    summary_instruction = (
        "你是一個貼心的生活氣象小秘書。請根據提供的使用者原始提問與我們幫他即時抓取到的環境數據，"
        "整理成一篇有條理、溫暖且易讀的訊息回覆。回覆規範如下：\n"
        "1. 必須完整呈現抓取到的有效數據重點。\n"
        "2. 依據數據主動給出貼心的生活提醒（例如：下雨提醒帶傘、空氣差提醒戴口罩、紫外線強提醒防曬）。\n"
        "3. 請適當添加豐富的 emoji 表情符號（如 🌤️、🌧️、😷、✨），排版多用換行，方便手機或網頁閱讀。"
    )
    
    data_payload = (
        f"使用者詢問：{user_input_string}\n"
        f"解析城市：{city}\n"
        f"最新觀測數據：\n"
        f"- 天氣資訊：{weather_info}\n"
        f"- 空氣品質：{aqi_info}\n"
        f"- 紫外線資訊：{uv_info}\n"
    )
    
    try:
        ai_reply = client.models.generate_content(
            model='gemini-3.5-flash',
            contents=data_payload,
            config=types.GenerateContentConfig(system_instruction=summary_instruction)
        )
        return ai_reply.text
    except:
        return f"小秘書思考稍微耽擱了... \n幫您抓到的即時數據如下：\n{weather_info}\n{aqi_info}"


if __name__ == "__main__":
    app.run(debug=True)