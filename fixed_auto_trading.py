import pyupbit
import openai
import pandas as pd
import time, datetime, os, threading, re, asyncio, schedule
from telegram import Bot
from telegram.ext import Application, CommandHandler

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

# ✅ GPT 전략 제안 (텔레그램 명령)
async def 전략생성(update, context):
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

# ✅ 수익률 낮은 전략 자동 제거
def prune_strategies():
    try:
        df = pd.read_csv("trade_results_2025-04.csv")
        result = df.groupby("전략")["현재가"].agg(["count", "mean"])
        losers = result[result["mean"] < 0].index.tolist()
        blocked_strategies.update(losers)
    except Exception as e:
        print(f"[전략 제거 실패] {e}")

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

# ✅ 전략 실행: 진입 판단 및 매수
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
        send_telegram_message(
            f"[매수완료] {ticker} 비중:{ratio}% TP:{tp}% SL:{sl}%"
        )
        return True
    else:
        send_telegram_message(f"[매수실패] {ticker}: {result}")
        return False
