import pyupbit, openai, pandas as pd, time, datetime, os, threading, re
from telegram import Bot
from telegram.ext import Updater, CommandHandler 

# API 설정
openai.api_key = os.environ.get("OPENAI_API_KEY")
upbit = pyupbit.Upbit(os.environ.get("UPBIT_ACCESS_KEY"), os.environ.get("UPBIT_SECRET_KEY"))
telegram_bot = Bot(token=os.environ.get("TELEGRAM_BOT_TOKEN"))
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID") 

open_positions, last_gpt_result = {}, {}
MIN_VOLUME = 500000 

# 거래 기록 함수
def log_trade(ticker, entry_price, current_price, strategy, result):
    month = datetime.datetime.now().strftime('%Y-%m')
    filename = f'trade_results_{month}.csv'
    df = pd.DataFrame([{
        "시간": datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "종목": ticker, "진입가": entry_price,
        "현재가": current_price, "전략이름": strategy, "성과": result
    }])
    df.to_csv(filename, mode='a', header=not os.path.exists(filename), index=False) 

# 텔레그램 전송 함수
def send_telegram_message(text):
    telegram_bot.send_message(chat_id=CHAT_ID, text=text) 

# GPT 진입평가 (확률 및 조건)
def gpt_entry_evaluation(ticker, strategy, price):
    prompt = f"""
    당신은 매우 보수적이고 정확한 암호화폐 투자 분석가입니다.
    종목: {ticker}, 전략: {strategy}, 가격: {price}원
    진입 성공확률(%), 추천 익절가(%), 손절가(%) 명확히 제시하세요.
    형식: 성공확률:[%] 익절가:[%] 손절가:[%]
    """
    response = openai.ChatCompletion.create(
        model="gpt-4-turbo",
        messages=[{"role":"system","content":"암호화폐 투자분석 시스템"},
                  {"role":"user","content":prompt}]
    )
    result = response.choices[0].message.content.strip()
    match = re.match(r'성공확률:(\d+)% 익절가:([\d.]+)% 손절가:([\d.]+)%', result)
    if match:
        probability, tp, sl = map(float, match.groups())
        return probability, tp, sl
    else:
        return 0, 0, 0 

# 비중 결정 (확률 기반)
def decide_ratio(probability):
    if probability >= 85: return 0.25
    elif 75 <= probability < 85: return 0.15
    elif 70 <= probability < 75: return 0.1
    else: return 0 

# 매수 실행 (확률 평가 포함)
def execute_buy(ticker, strategy):
    current_price = pyupbit.get_current_price(ticker)
    prob, tp, sl = gpt_entry_evaluation(ticker, strategy, current_price)
    ratio = decide_ratio(prob)
    if ratio == 0:
        send_telegram_message(f"[진입거절] {ticker}: 확률 {prob}%, 진입 안함.")
        return False
    krw_balance = upbit.get_balance("KRW")
    buy_amount = krw_balance * ratio
    if buy_amount < 5000:
        send_telegram_message(f"[매수실패] {ticker}: 최소금액 미달.")
        return False
    buy_result = upbit.buy_market_order(ticker, buy_amount)
    time.sleep(1)
    if upbit.get_balance(ticker):
        open_positions[ticker] = {
            "entry_price": current_price,
            "tp": tp, "sl": sl,
            "strategy": strategy, "last_checked": int(time.time())
        }
        log_trade(ticker, current_price, current_price, strategy, "진입")
        send_telegram_message(f"[매수완료] {ticker}: {buy_amount:.0f}원({ratio*100:.1f}%), 확률 {prob}% TP:{tp}% SL:{sl}%")
        return True
    else:
        send_telegram_message(f"[매수 실패] {ticker}: 체결오류 {buy_result}")
        return False 

# 매도 실행
def execute_trade(ticker, reason):
    balance = upbit.get_balance(ticker)
    if balance:
        sell_result = upbit.sell_market_order(ticker, balance)
        price = pyupbit.get_current_price(ticker)
        log_trade(ticker, open_positions[ticker]['entry_price'], price, open_positions[ticker]['strategy'], reason)
        send_telegram_message(f"[매도완료] {ticker}: {reason} 체결결과:{sell_result}")
        open_positions.pop(ticker, None)
    else:
        send_telegram_message(f"[매도실패] {ticker}: 잔고없음") 

# 텔레그램 명령어
def telegram_bot_commands():
    updater = Updater(token=os.environ.get("TELEGRAM_BOT_TOKEN"))
    dispatcher = updater.dispatcher
    dispatcher.add_handler(CommandHandler('자산', lambda u,c: u.message.reply_text(f"{upbit.get_balance('KRW'):,.0f}원")))
    updater.start_polling()
threading.Thread(target=telegram_bot_commands).start() 

# 메인 루프
ticker_list = pyupbit.get_tickers("KRW") 

while True:
    now = int(time.time())
    for ticker in ticker_list:
        if ticker not in open_positions:
            df = pyupbit.get_ohlcv(ticker, "minute1", 2)
            if df is not None and len(df) >= 2:
                volume = df['volume'][-1]*df['close'][-1]
                change = (df['close'][-1]-df['close'][-2])/df['close'][-2]*100
                if volume >= MIN_VOLUME and abs(change) >= 3:
                    execute_buy(ticker, "급등돌파추격") 

    for ticker, data in list(open_positions.items()):
        price = pyupbit.get_current_price(ticker)
        entry_price, tp, sl = data['entry_price'], data['tp'], data['sl']
        if price >= entry_price*(1+tp/100):
            execute_trade(ticker, "익절 체결")
        elif price <= entry_price*(1-sl/100):
            execute_trade(ticker, "손절 체결")
        elif now - data['last_checked'] >= 120:
            prob, new_tp, new_sl = gpt_entry_evaluation(ticker, data['strategy'], price)
            if (new_tp, new_sl) != (tp, sl):
                open_positions[ticker]['tp'], open_positions[ticker]['sl'] = new_tp, new_sl
                send_telegram_message(f"[긴급변경] {ticker}: 새 TP:{new_tp}% SL:{new_sl}% 적용")
            open_positions[ticker]['last_checked'] = now
    time.sleep(1)
