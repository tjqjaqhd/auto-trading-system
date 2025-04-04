from telegram.ext import Updater, CommandHandler
import os, pyupbit, pandas as pd

upbit = pyupbit.Upbit(os.environ["UPBIT_ACCESS_KEY"], os.environ["UPBIT_SECRET_KEY"])

open_positions = {}

def balance(update, context):
    krw_balance = upbit.get_balance("KRW")
    update.message.reply_text(f"KRW balance: {krw_balance:,.0f} KRW")

def status(update, context):
    if open_positions:
        msg = "\n".join([f"{t}: entry {d['entry_price']} → now {pyupbit.get_current_price(t)} (TP:{d['tp']}%, SL:{d['sl']}%)"
                         for t, d in open_positions.items()])
    else:
        msg = "No positions."
    update.message.reply_text(msg)

def price(update, context):
    try:
        ticker = context.args[0].upper()
        current_price = pyupbit.get_current_price(ticker)
        update.message.reply_text(f"{ticker} current price: {current_price} KRW")
    except IndexError:
        update.message.reply_text("Usage: /price [TICKER] (ex: /price BTC)")
    except Exception as e:
        update.message.reply_text(f"Error: {e}")

def buy(update, context):
    try:
        ticker, amount = context.args[0].upper(), float(context.args[1])
        result = upbit.buy_market_order(ticker, amount)
        update.message.reply_text(f"Buy order placed: {result}")
    except IndexError:
        update.message.reply_text("Usage: /buy [TICKER] [AMOUNT] (ex: /buy BTC 5000)")
    except Exception as e:
        update.message.reply_text(f"Error: {e}")

def sell(update, context):
    try:
        ticker, amount = context.args[0].upper(), float(context.args[1])
        result = upbit.sell_market_order(ticker, amount)
        update.message.reply_text(f"Sell order placed: {result}")
    except IndexError:
        update.message.reply_text("Usage: /sell [TICKER] [AMOUNT] (ex: /sell BTC 0.01)")
    except Exception as e:
        update.message.reply_text(f"Error: {e}")

def positions(update, context):
    if open_positions:
        details = "\n".join([f"{t}: {data}" for t, data in open_positions.items()])
        update.message.reply_text(f"Open positions:\n{details}")
    else:
        update.message.reply_text("No open positions.")

def logs(update, context):
    filename = f"trade_results_{pd.Timestamp.now().strftime('%Y-%m')}.csv"
    if os.path.exists(filename):
        df = pd.read_csv(filename)
        recent_logs = df.tail(5).to_string(index=False)
        update.message.reply_text(f"Recent trades:\n{recent_logs}")
    else:
        update.message.reply_text("No recent logs.")

def stop(update, context):
    try:
        ticker = context.args[0].upper()
        open_positions.pop(ticker, None)
        update.message.reply_text(f"Stopped automated trading for {ticker}.")
    except IndexError:
        update.message.reply_text("Usage: /stop [TICKER] (ex: /stop BTC)")
    except Exception as e:
        update.message.reply_text(f"Error: {e}")

def telegram_bot_commands():
    updater = Updater(token=os.environ["TELEGRAM_BOT_TOKEN"])
    dispatcher = updater.dispatcher

    dispatcher.add_handler(CommandHandler('balance', balance))
    dispatcher.add_handler(CommandHandler('status', status))
    dispatcher.add_handler(CommandHandler('price', price))
    dispatcher.add_handler(CommandHandler('buy', buy))
    dispatcher.add_handler(CommandHandler('sell', sell))
    dispatcher.add_handler(CommandHandler('positions', positions))
    dispatcher.add_handler(CommandHandler('logs', logs))
    dispatcher.add_handler(CommandHandler('stop', stop))

    updater.start_polling()
    updater.idle()

telegram_bot_commands()
