import os
import pyupbit
from telegram.ext import Updater, CommandHandler

# 환경변수에서 키값 설정
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
UPBIT_ACCESS_KEY = os.environ.get("UPBIT_ACCESS_KEY")
UPBIT_SECRET_KEY = os.environ.get("UPBIT_SECRET_KEY")

# API 연결
upbit = pyupbit.Upbit(UPBIT_ACCESS_KEY, UPBIT_SECRET_KEY)

# 텔레그램 명령어: 잔고확인
def balance(update, context):
    try:
        krw_balance = upbit.get_balance("KRW")
        if krw_balance is not None:
            msg = f"KRW 자산: {krw_balance:,.0f}원"
        else:
            msg = "잔고 조회 실패"
    except Exception as e:
        msg = f"잔고 조회 중 오류 발생: {e}"
    update.message.reply_text(msg)

# 텔레그램 명령어: 특정 종목 현재가 확인
def price(update, context):
    try:
        ticker = context.args[0].upper()
        current_price = pyupbit.get_current_price(ticker)
        if current_price:
            msg = f"{ticker} 현재가: {current_price:,.0f}원"
        else:
            msg = f"{ticker} 종목을 찾을 수 없습니다."
    except IndexError:
        msg = "사용법: /price [종목명] (예시: /price KRW-BTC)"
    except Exception as e:
        msg = f"가격 조회 중 오류 발생: {e}"
    update.message.reply_text(msg)

# 텔레그램 명령어: 포지션 상태 확인 (임시 더미데이터)
open_positions = {}  # 실제 운용 중인 포지션 데이터를 여기서 불러와야 함
def status(update, context):
    try:
        if open_positions:
            msg = "\n".join(
                f"{t}: 진입가 {d['entry_price']}원, TP:{d['tp']}%, SL:{d['sl']}%"
                for t, d in open_positions.items()
            )
        else:
            msg = "현재 보유중인 포지션이 없습니다."
    except Exception as e:
        msg = f"포지션 상태 조회 중 오류: {e}"
    update.message.reply_text(msg)

# 텔레그램 명령어: 코드 검증 요청 메시지
def codecheck(update, context):
    update.message.reply_text("전체 코드 검증 요청을 완료했습니다.")

# 명령어 등록 및 텔레그램 봇 가동
def run_telegram_bot():
    updater = Updater(token=TELEGRAM_BOT_TOKEN)
    dispatcher = updater.dispatcher

    dispatcher.add_handler(CommandHandler('balance', balance))
    dispatcher.add_handler(CommandHandler('price', price))
    dispatcher.add_handler(CommandHandler('status', status))
    dispatcher.add_handler(CommandHandler('codecheck', codecheck))

    updater.start_polling()
    print("텔레그램 봇이 실행중입니다.")

if __name__ == "__main__":
    run_telegram_bot()
