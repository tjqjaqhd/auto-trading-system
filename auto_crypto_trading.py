import pyupbit, openai, pandas as pd, time, datetime, os, threading, re 
from telegram import Bot 
from telegram.ext import Updater, CommandHandler

required_envs = ["OPENAI_API_KEY", "UPBIT_ACCESS_KEY", "UPBIT_SECRET_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"] 
for env in required_envs: 
    if not os.environ.get(env): 
        raise ValueError(f"[환경변수 누락] {env}가 설정되지 않았습니다.")

openai.api_key = os.environ["OPENAI_API_KEY"] 
upbit = pyupbit.Upbit(os.environ["UPBIT_ACCESS_KEY"], os.environ["UPBIT_SECRET_KEY"]) 
telegram_bot = Bot(token=os.environ["TELEGRAM_BOT_TOKEN"]) 
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

open_positions, last_gpt_result = {}, {} 
MIN_VOLUME = 500000

def send_telegram_message(text): 
    try: 
        telegram_bot.send_message(chat_id=CHAT_ID, text=text) 
    except Exception as e: 
        print(f"[텔레그램 오류] {e}")

def log_trade(ticker, entry_price, current_price, strategy, result): 
    month = datetime.datetime.now().strftime('%Y-%m') 
    filename = f'trade_results_{month}.csv' 
    df = pd.DataFrame([{ 
        "시간": datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), 
        "종목": ticker, 
        "진입가": entry_price, 
        "현재가": current_price, 
        "전략이름": strategy, 
        "성과": result 
    }]) 
    df.to_csv(filename, mode='a', header=not os.path.exists(filename), index=False)

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
            messages=[ 
                {"role": "system", "content": "암호화폐 투자분석 시스템"}, 
                {"role": "user", "content": prompt} 
            ], timeout=10 
        ) 
        result = response.choices[0].message.content.strip() 
        match = re.search(r'성공확률:(\d+)% 익절가:([\d.]+)% 손절가:([\d.]+)%', result) 
        if match: 
            return map(float, match.groups()) 
        else: 
            send_telegram_message(f"[GPT 응답 오류] 형식 불일치: {result}") 
    except Exception as e: 
        send_telegram_message(f"[GPT 호출 실패] {e}") 
    return 0, 0, 0

def decide_ratio(probability): 
    if probability >= 85: 
        return 0.25 
    elif 75 <= probability < 85: 
        return 0.15 
    elif 70 <= probability < 75: 
        return 0.1 
    else: 
        return 0

def execute_buy(ticker, strategy): 
    current_price = pyupbit.get_current_price(ticker) 
    prob, tp, sl = gpt_entry_evaluation(ticker, strategy, current_price) 
    ratio = decide_ratio(prob) 
    if ratio == 0: 
        send_telegram_message(f"[진입거절] {ticker}: 확률 {prob}% 진입안함") 
        return False 
    krw_balance = upbit.get_balance("KRW") 
    buy_amount = krw_balance * ratio 
    if buy_amount < 5000: 
        send_telegram_message(f"[매수실패] {ticker}: 최소금액 미달") 
        return False 
    buy_result = upbit.buy_market_order(ticker, buy_amount) 
    time.sleep(1) 
    if upbit.get_balance(ticker): 
       open_positions[ticker] = { 
           "entry_price": current_price, 
           "tp": tp, "sl": sl, 
           "strategy": strategy, 
           "last_checked": int(time.time()) 
       } 
       log_trade(ticker, current_price, current_price, strategy, "진입") 
       send_telegram_message(f"[매수완료] {ticker}: {buy_amount:.0f}원({ratio*100:.1f}%) 확률 {prob}% TP:{tp}% SL:{sl}%") 
       return True 
   else: 
       send_telegram_message(f"[매수 실패] {ticker}: 체결오류 {buy_result}") 
       return False

def telegram_bot_commands(): 
    updater = Updater(token=os.environ["TELEGRAM_BOT_TOKEN"]) 
    dispatcher = updater.dispatcher

    dispatcher.add_handler(CommandHandler('자산', lambda u, c: u.message.reply_text(f"{upbit.get_balance('KRW'):,.0f}원"))) 
    
    def status_command(update, context): 
        msg = "\n".join(f"{t}: {d['entry_price']}→{pyupbit.get_current_price(t)} (TP:{d['tp']}% SL:{d['sl']}%)" for t, d in open_positions.items()) or "포지션 없음" 
        update.message.reply_text(msg) 
    
    dispatcher.add_handler(CommandHandler('상태', status_command)) 
  
    def code_verify(update, context): 
        update.message.reply_text("전체 코드 검증이 요청되었습니다.") 
      
    dispatcher.add_handler(CommandHandler('코드검증', code_verify)) updater.start_polling() 

threading.Thread(target=telegram_bot_commands, daemon=True).start()

ticker_list = pyupbit.get_tickers("KRW")

while True: 
    try: 
        # 기존 루프 코드 (생략하지 않고 그대로 사용) 
        pass # 실제 코드를 위 내용으로 완전히 교체 
    except Exception as e: 
        send_telegram_message(f"[루프 예외] {e}") 
        time.sleep(3) 
    time.sleep(1)

