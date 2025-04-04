import pyupbit
import openai
import pandas as pd
import time, datetime, os, threading, re, schedule
from telegram import Bot, Update
from telegram.ext import Application, CommandHandler, ContextTypes

# 환경 변수 확인
required_envs = [
    "OPENAI_API_KEY", "UPBIT_ACCESS_KEY", "UPBIT_SECRET_KEY",
    "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"
]
for env in required_envs:
    if not os.environ.get(env):
        raise ValueError(f"[환경변수 누락] {env}가 설정되지 않았습니다.")

openai.api_key = os.environ["OPENAI_API_KEY"]
upbit = pyupbit.Upbit(
    os.environ["UPBIT_ACCESS_KEY"],
    os.environ["UPBIT_SECRET_KEY"]
)
telegram_bot = Bot(token=os.environ["TELEGRAM_BOT_TOKEN"])
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

open_positions, post_exit_tracking, blocked_strategies = {}, {}, set()
MIN_VOLUME = 500000
GPT_REEVALUATE_INTERVAL = 300
TRAILING_STOP_GAP = 1.5  # %

def new_func():
    return datetime.datetime.now().strftime('%Y-%m')

def log_trade(ticker, entry_price, current_price, strategy, result):
    month = new_func()
    filename = f"trade_results_{month}.csv"
    df = pd.DataFrame([{
        "시간": datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "종목": ticker, "진입가": entry_price,
        "현재가": current_price, "전략": strategy, "성과": result
    }])
    df.to_csv(filename, mode='a', header=not os.path.exists(filename), index=False)

def send_telegram_message(text):
    try:
        telegram_bot.send_message(chat_id=CHAT_ID, text=text)
    except Exception as e:
        print(f"[텔레그램 전송 실패] {e}")

def gpt_entry_evaluation(ticker, strategy, price):
    prompt = f"""
    당신은 정확한 암호화폐 전략 판단가입니다.
    종목: {ticker}, 전략: {strategy}, 현재가: {price}원
    성공확률, 익절가, 손절가, 추천 비중을 아래 형식으로 제시하세요.
    형식: 성공확률:[%] 익절가:[%] 손절가:[%] 비중:[%]
    """
    try:
        response = openai.ChatCompletion.create(
            model="gpt-4-turbo",
            messages=[{"role": "user", "content": prompt}]
        )
        result = response.choices[0].message.content.strip()
        match = re.search(
            r"성공확률:(\d+(?:\.\d+)?)% 익절가:(\d+(?:\.\d+)?)% 손절가:(\d+(?:\.\d+)?)% 비중:(\d+(?:\.\d+)?)%",
            result
        )
        if match:
            return tuple(map(float, match.groups()))
        else:
            send_telegram_message(f"[GPT 응답 오류] {result}")
    except Exception as e:
        send_telegram_message(f"[GPT 호출 실패] {e}")
    return 0, 0, 0, 0

def execute_buy(ticker, strategy):
    current_price = pyupbit.get_current_price(ticker)
    prob, tp, sl, ratio = gpt_entry_evaluation(ticker, strategy, current_price)

    if strategy in blocked_strategies:
        send_telegram_message(f"[차단전략] {strategy} 제외됨")
        return False

    if ratio == 0 or prob < 70:
        send_telegram_message(f"[진입거절] {ticker}: 확률 {prob}% 비중 {ratio}%")
        return False

    total_val = sum([pyupbit.get_current_price(t) * upbit.get_balance(t) for t in open_positions])
    krw = upbit.get_balance("KRW")
    total_eq = total_val + krw
    used_ratio = total_val / total_eq
    if used_ratio >= 0.7:
        send_telegram_message("[진입제한] 전체 자산의 70% 초과")
        return False

    buy_amount = min(krw * ratio / 100, total_eq * 0.25)
    if buy_amount < 5000:
        send_telegram_message(f"[금액부족] {ticker}: {buy_amount:.0f}원")
        return False

    result = upbit.buy_market_order(ticker, buy_amount)
    time.sleep(1)
    if upbit.get_balance(ticker):
        open_positions[ticker] = {
            "entry_price": current_price,
            "tp": tp,
            "sl": sl,
            "strategy": strategy,
            "last_checked": time.time(),
            "gpt_count": 0,
            "high_price": current_price
        }
        send_telegram_message(f"[매수완료] {ticker} 비중:{ratio}% TP:{tp}% SL:{sl}%")
        return True
    else:
        send_telegram_message(f"[매수실패] {ticker}: {result}")
        return False

def check_exit_conditions():
    for ticker, info in list(open_positions.items()):
        current_price = pyupbit.get_current_price(ticker)
        entry = info["entry_price"]
        tp_price = entry * (1 + info["tp"] / 100)
        sl_price = entry * (1 - info["sl"] / 100)

        if current_price > info["high_price"]:
            info["high_price"] = current_price

        trail_sl_price = info["high_price"] * (1 - TRAILING_STOP_GAP / 100)

        if current_price >= tp_price:
            upbit.sell_market_order(ticker, upbit.get_balance(ticker))
            send_telegram_message(f"[익절매도] {ticker} / 현재가:{current_price}")
            log_trade(ticker, entry, current_price, info["strategy"], "익절")
            del open_positions[ticker]
        elif current_price <= sl_price or current_price <= trail_sl_price:
            upbit.sell_market_order(ticker, upbit.get_balance(ticker))
            send_telegram_message(f"[손절매도] {ticker} / 현재가:{current_price}")
            log_trade(ticker, entry, current_price, info["strategy"], "손절")
            del open_positions[ticker]

def generate_daily_report():
    try:
        today = datetime.datetime.now().strftime("%Y-%m")
        filename = f"trade_results_{today}.csv"
        if not os.path.exists(filename):
            send_telegram_message("[리포트 없음] 오늘 거래 기록이 없습니다.")
            return

        df = pd.read_csv(filename)
        today_df = df[df['시간'].str.startswith(datetime.datetime.now().strftime('%Y-%m-%d'))]
        summary = today_df.groupby("전략")["현재가"].agg(['count', 'mean'])

        prompt = f"""
        다음은 오늘 암호화폐 매매 전략별 성과 요약입니다:
        {summary.to_string()}
        다음을 포함한 일일 리포트를 작성하세요:
        - 오늘 요약
        - 문제점
        - 내일 전략 방향성
        - 매매 종료 종목을 30분간 더 추적했다면 전략 타당성은 어땠을지 평가
        - 전반적인 전략의 정확도 평가
        """
        response = openai.ChatCompletion.create(
            model="gpt-4-turbo",
            messages=[{"role": "user", "content": prompt}]
        )
        result = response.choices[0].message.content.strip()
        send_telegram_message("[📊 GPT 일일 리포트]\n" + result)
    except Exception as e:
        send_telegram_message(f"[리포트 생성 오류] {e}")

def run_all():
    schedule.every(10).seconds.do(check_exit_conditions)
    schedule.every().day.at("23:00").do(generate_daily_report)
    while True:
        schedule.run_pending()
        time.sleep(1)

# 텔레그램 명령
async def 시작(update: Update, context: ContextTypes.DEFAULT_TYPE):
    threading.Thread(target=run_all).start()
    await update.message.reply_text("✅ 자동매매 루프 시작됨")

async def 전략생성(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        text = "📊 전략 성과:\n"
        stats = pd.read_csv("strategy_stats.csv")
        for _, row in stats.iterrows():
            text += f"- {row['전략']}: 익절 {row['익절']} / 손절 {row['손절']}\n"
        prompt = text + "위 통계 외에 현재 장세에서 유망한 전략 2개를 제안해줘. 조건도 간단히 설명해줘."
        response = openai.ChatCompletion.create(
            model="gpt-4-turbo",
            messages=[
                {"role": "system", "content": "전략 설계 전문가"},
                {"role": "user", "content": prompt}
            ]
        )
        idea = response.choices[0].message.content
        await update.message.reply_text(f"[GPT 전략제안]\n{idea}")
    except Exception as e:
        await update.message.reply_text(f"[전략 생성 오류] {e}")

async def 잔고(update: Update, context: ContextTypes.DEFAULT_TYPE):
    balance = upbit.get_balance("KRW")
    await update.message.reply_text(f"💰 현재 잔고: {balance:,.0f} KRW")

async def 수동매수(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("형식: /매수 티커 전략명")
        return
    ticker, strategy = context.args[0], context.args[1]
    result = execute_buy(ticker, strategy)
    await update.message.reply_text(f"🛒 매수 결과: {result}")

def main():
    application = Application.builder().token(os.environ["TELEGRAM_BOT_TOKEN"]).build()
    application.add_handler(CommandHandler("시작", 시작))
    application.add_handler(CommandHandler("전략생성", 전략생성))
    application.add_handler(CommandHandler("잔고", 잔고))
    application.add_handler(CommandHandler("매수", 수동매수))
    application.run_polling()

if __name__ == "__main__":
    threading.Thread(target=run_all).start()
    main()
