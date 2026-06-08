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
            # 這裡我們直接把 Dialogflow 抓到的原始字串丟給問答核心
            # 讓核心內部的 Gemini 大腦直接去分析到底是想「查全部」還是「查單獨項目」
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
# 3. 核心大腦：AI 精準意圖分析 + 多資料來源動態串接
# =====================================================================
def ask_gemini_weather(user_input_string):
    # 【階段一】叫 Gemini 擔任專業意圖過濾器，幫我們分析城市與特定想查的項目
    intent_instruction = (
        "你是一個氣象查詢意圖分析專家。請分析使用者的輸入，精準提取出「台灣縣市名稱」與「查詢項目分類」。\n"
        "1. 縣市名稱：請一律修正為規範名稱（如：台北->臺北市、台中->臺中市、彰化->彰化縣、高雄->高雄市）。如果使用者沒說城市，預設為'臺中市'。\n"
        "2. 查詢項目意圖 (intent)：請嚴格依據使用者想單獨查的內容進行以下分類：\n"
        "   - 如果提到 '下雨', '降雨', '機率', '雨' -> 分類為 'rain'\n"
        "   - 如果提到 '空氣', '品質', 'AQI', 'PM2.5' -> 分類為 'aqi'\n"
        "   - 如果提到 '紫外線', '防曬', 'UV' -> 分類為 'uv'\n"
        "   - 如果單純問 '天氣', '氣溫', '冷不冷' 或是沒指定特殊項目 -> 分類為 'all'\n"
        "請嚴格只回傳 JSON 格式物件，絕對不要帶任何"
