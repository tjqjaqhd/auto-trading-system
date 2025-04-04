import pyupbit, openai, pandas as pd, time, datetime, os, threading, re
from telegram import Bot
from telegram.ext import Updater, CommandHandler

# 환경변수 로딩 체크
required_envs = ["OPENAI_API_KEY", "UPBIT_ACCESS_KEY", "UPBIT_SECRET_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"]
for env_var in required_envs:
    if not os.environ.get(env_var):
        raise ValueError(f"필수 환경변수 {env_var}이(가) 설정되지 않았습니다.")

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
    df.to_csv(filename, mode='a', header=not os.path.exists(filename), index=False, encoding='utf-8-sig')

# 텔레그램 전송 함수
def send_telegram_message(text):
    try:
        telegram_bot.send_message(chat_id=CHAT_ID, text=text)
    except Exception as e:
        print(f"텔레그램 메시지 전송 실패: {e}")

# GPT 진입평가 (확률 및 조건)
def gpt_entry_evaluation(ticker, strategy, price):
    prompt = f"""
    당신은 매우 보수적이고 정확한 암호화폐 투자 분석가입니다.
    종목: {ticker}, 전략: {strategy}, 가격: {price}원
    진입 성공확률(%), 추천 익절가(%), 손절가(%) 명확히 제시하세요.
    형식: 성공확률:[%] 익절가:[%] 손절가:[%]
    """
    try:
        response = openai.ChatCompletion.create(
            model="gpt-4-turbo",
            messages=[{"role": "system", "content": "암호화폐 투자분석 시스템"}, {"role": "user", "content": prompt}],
            timeout=15
        )
        result = response.choices[0].message.content.strip()
        match = re.search(r'성공확률:(\d+)%\s*익절가:([\d.]+)%\s*손절가:([\d.]+)%', result)
        if match:
            return map(float, match.groups())
        else:
            send_telegram_message(f"[GPT 오류] {ticker} GPT 응답 형식오류:\n{result}")
            return 0, 0, 0
    except Exception as e:
        send_telegram_message(f"[GPT 예외] {ticker} 평가 예외발생: {str(e)}")
        return 0, 0, 0

# 비중 결정 (확률 기반)
def decide_ratio(probability):
    if probability >= 85: return 0.25
    elif 75 <= probability < 85: return 0.15
    elif 70 <= probability < 75: return 0.1
    else: return 0

# 매수 실행
def execute_buy(ticker, strategy):
    current_price = pyupbit.get_current_price(ticker)
    prob, tp, sl = gpt_entry_evaluation(ticker, strategy, current_price)
    ratio = decide_ratio(prob)
    if ratio == 0:
        send_telegram_message(f"[진입거절] {ticker}: 확률 {prob}%로 진입 취소.")
        return False
    krw_balance = upbit.get_balance("KRW")
    buy_amount = krw_balance * ratio
    if buy_amount < 5000:
        send_telegram_message(f"[매수실패] {ticker}: 최소금액 미달.")
        return False
    buy_result = upbit.buy_market_order(ticker, buy_amount)
    time.sleep(1)
    if upbit.get_balance(ticker):
        open_positions[ticker] = {"entry_price": current_price, "tp": tp, "sl": sl, "strategy": strategy, "last_checked": int(time.time())}
        log_trade(ticker, current_price, current_price, strategy, "진입")
        send_telegram_message(f"[매수완료] {ticker}: {buy_amount:.0f}원({ratio*100:.1f}%), 확률 {prob}% TP:{tp}% SL:{sl}%")
        return True
    else:
        send_telegram_message(f"[매수 실패] {ticker}: 체결오류 {buy_result}")
        return False

# 텔레그램 명령어 처리
def telegram_bot_commands():
    updater = Updater(token=os.environ["TELEGRAM_BOT_TOKEN"])
    dispatcher = updater.dispatcher
    dispatcher.add_handler(CommandHandler('balance', lambda u, c: u.message.reply_text(f"{upbit.get_balance('KRW'):,.0f}원")))

    def status(update, context):
        msg = "\n".join(
            f"{t}: {d['entry_price']}→{pyupbit.get_current_price(t)} (TP:{d['tp']}% SL:{d['sl']}%)"
            for t, d in open_positions.items()
        ) or "포지션 없음"
        update.message.reply_text(msg)

    dispatcher.add_handler(CommandHandler('status', status))
    updater.start_polling()

threading.Thread(target=telegram_bot_commands).start()

# 메인 루프 안정화 (try-except 포함)
ticker_list = pyupbit.get_tickers("KRW")
while True:
    try:
        now = int(time.time())
        for ticker in ticker_list:
            df = pyupbit.get_ohlcv(ticker, "minute1", 2)
            if df is not None and len(df) >= 2:
                volume = df['volume'][-1]*df['close'][-1]
                change = (df['close'][-1]-df['close'][-2])/df['close'][-2]*100
                if ticker not in open_positions and volume >= MIN_VOLUME and abs(change) >= 3:
                    execute_buy(ticker, "급등돌파추격")
        time.sleep(3)
    except Exception as e:
        send_telegram_message(f"[메인루프 오류] {e}")
        time.sleep(5)

