import pyupbit, openai, pandas as pd, time, datetime, os, threading, re, asyncio, schedule
from telegram import Bot
from telegram.ext import Application, CommandHandler

required_envs = ["OPENAI_API_KEY", "UPBIT_ACCESS_KEY", "UPBIT_SECRET_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"]
for env in required_envs:
    if not os.environ.get(env):
        raise ValueError(f"[환경변수 누락] {env}가 설정되지 않았습니다.")

openai.api_key = os.environ["OPENAI_API_KEY"]
upbit = pyupbit.Upbit(os.environ["UPBIT_ACCESS_KEY"], os.environ["UPBIT_SECRET_KEY"])
telegram_bot = Bot(token=os.environ["TELEGRAM_BOT_TOKEN"])
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

open_positions, post_exit_tracking, blocked_strategies = {}, {}, set()
MIN_VOLUME = 500000
GPT_REEVALUATE_INTERVAL = 300
TRAILING_STOP_GAP = 1.5  # %

...

# GPT 전략 제안 (텔레그램 명령)
async def 전략생성(update, context):
    try:
        text = "전략 성과:
"
        stats = pd.read_csv("strategy_stats.csv")
        for _, row in stats.iterrows():
            text += f"- {row['전략']}: 익절 {row['익절']} / 손절 {row['손절']}\n"
        prompt = text + "\n위 전략 외에 현재 장세에서 수익 기대 가능한 전략 2개 추천해줘. 조건도 간략히 말해줘."
        response = openai.ChatCompletion.create(
            model="gpt-4-turbo",
            messages=[{"role": "system", "content": "전략 설계 전문가"}, {"role": "user", "content": prompt}]
        )
        idea = response.choices[0].message.content
        await update.message.reply_text(f"[GPT 전략제안]\n{idea}")
    except Exception as e:
        await update.message.reply_text(f"[전략 생성 오류] {e}")

# 자동 전략 제거 (수익률 기반)
def prune_strategies():
    try:
        df = pd.read_csv("trade_results_2025-04.csv")
        result = df.groupby("전략이름")["현재가"].agg(["count", "mean"])
        losers = result[result["mean"] < 0].index.tolist()
        blocked_strategies.update(losers)
    except: pass

# 청산 후 30분 흐름 체크 및 GPT 피드백
async def post_exit_analysis():
    for ticker in list(post_exit_tracking):
        exit_time, exit_price, strategy = post_exit_tracking[ticker]
        if time.time() - exit_time >= 1800:
            now_price = pyupbit.get_current_price(ticker)
            delta = round((now_price - exit_price) / exit_price * 100, 2)
            judgment = "적절" if delta < 0 else "미흡"
            prompt = f"전략: {strategy}, 매도가: {exit_price}, 30분후: {now_price}, 수익률: {delta}%\n매도는 왜 {'맞았는가' if judgment=='적절' else '틀렸는가'}? 어떻게 보완해야 하나?"
            try:
                response = openai.ChatCompletion.create(
                    model="gpt-4-turbo",
                    messages=[{"role": "user", "content": prompt}]
                )
                feedback = response.choices[0].message.content.strip()
                with open("post_exit_feedback.csv", "a", encoding="utf-8") as f:
                    f.write(f"{datetime.datetime.now()},{ticker},{exit_price},{now_price},{strategy},{judgment},{feedback}\n")
            except: pass
            del post_exit_tracking[ticker]

# 데일리 리포트
async def gpt_daily_report():
    try:
        stats = pd.read_csv("strategy_stats.csv")
        result = "오늘 전략 통계:\n"
        for _, r in stats.iterrows():
            result += f"- {r['전략']}: 익절 {r['익절']} / 손절 {r['손절']}\n"
        prompt = result + "오늘 전략 성과 분석 및 보완 방향 1문단으로 요약해줘."
        response = openai.ChatCompletion.create(
            model="gpt-4-turbo",
            messages=[{"role": "user", "content": prompt}]
        )
        summary = response.choices[0].message.content
        send_telegram_message(f"[GPT 데일리리포트]\n{summary}")
    except Exception as e:
        send_telegram_message(f"[GPT 데일리리포트 오류] {e}")

# 스케줄 등록
threading.Thread(target=lambda: schedule.every().day.at("23:59").do(asyncio.run, gpt_daily_report()), daemon=True).start()
threading.Thread(target=lambda: schedule.every(10).minutes.do(asyncio.run, post_exit_analysis()), daemon=True).start()
threading.Thread(target=lambda: schedule.every().day.at("00:05").do(prune_strategies), daemon=True).start()

def gpt_entry_evaluation(ticker, strategy, price):
    prompt = f"""
    당신은 정확한 판단을 내리는 암호화폐 투자 분석가입니다.
    종목: {ticker}, 전략: {strategy}, 현재가: {price}원
    아래 형식으로 성공확률(%), 익절가(%), 손절가(%), 추천 비중(%)을 제시하세요.
    형식: 성공확률:[%] 익절가:[%] 손절가:[%] 비중:[%]
    """
    try:
        response = openai.ChatCompletion.create(
            model="gpt-4-turbo",
            messages=[{"role": "user", "content": prompt}]
        )
        content = response.choices[0].message.content.strip()
        match = re.search(r"성공확률:(\d+(?:\.\d+)?)% 익절가:(\d+(?:\.\d+)?)% 손절가:(\d+(?:\.\d+)?)% 비중:(\d+(?:\.\d+)?)%", content)
        if match:
            return tuple(map(float, match.groups()))
        else:
            send_telegram_message(f"[GPT 응답 오류] {content}")
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

def evaluate_positions():
    for ticker in list(open_positions):
        try:
            position = open_positions[ticker]
            now = pyupbit.get_current_price(ticker)
            entry = position["entry_price"]
            tp = position["tp"]
            sl = position["sl"]
            high = position["high_price"]

            if now > high:
                position["high_price"] = now
            change = (now - entry) / entry * 100

            if change >= tp or (now < high * (1 - TRAILING_STOP_GAP / 100)):
                upbit.sell_market_order(ticker, upbit.get_balance(ticker))
                send_telegram_message(f"[익절/트레일] {ticker} +{change:.2f}%")
                post_exit_tracking[ticker] = (time.time(), now, position["strategy"])
                del open_positions[ticker]

            elif change <= -sl:
                upbit.sell_market_order(ticker, upbit.get_balance(ticker))
                send_telegram_message(f"[손절] {ticker} {change:.2f}%")
                post_exit_tracking[ticker] = (time.time(), now, position["strategy"])
                del open_positions[ticker]

            elif time.time() - position["last_checked"] >= GPT_REEVALUATE_INTERVAL and position["gpt_count"] < 5:
                prob, new_tp, new_sl, _ = gpt_entry_evaluation(ticker, position["strategy"], now)
                position.update({"tp": new_tp, "sl": new_sl, "last_checked": time.time()})
                position["gpt_count"] += 1
        except Exception as e:
            send_telegram_message(f"[평가오류] {ticker}: {e}")
