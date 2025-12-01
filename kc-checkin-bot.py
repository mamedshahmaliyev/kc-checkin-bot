import os, dotenv
from zoneinfo import ZoneInfo
dotenv.load_dotenv(override=True)

from logging import Logger
import logging.config, atexit

logger:Logger = logging.getLogger('kc-checkin-bot')
os.makedirs('logs', exist_ok=True)
logging.config.dictConfig({
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "simple": {
            "format": "%(asctime)s [%(levelname)s] L%(lineno)d: %(message)s",
            "datefmt": "%Y-%m-%dT%H:%M:%S%z"
        }
    },
    "handlers": {
        "file": {
            "class": "logging.handlers.RotatingFileHandler",
            "formatter": "simple",
            "level": "INFO",
            "filename": "logs/kc-checkin-bot.log",
            "maxBytes": 3000000,
            "backupCount": 3
        },
        "stdout": {
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stdout",
            "formatter": "simple",
            "level": "INFO"
        },
        "queue_handler": {
            "class": "logging.handlers.QueueHandler",
            "handlers": ["file", "stdout"],
            "respect_handler_level": True,
        }
    },
    "loggers": {
        "root": {
            "handlers": ["queue_handler"],
            "level": "DEBUG"
        }
    }
})
if queueHandler := logging.getHandlerByName('queue_handler'):
    queueHandler.listener.start()
    atexit.register(queueHandler.listener.stop)

import asyncio, json
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, fields, asdict

from aiogram import Bot, Dispatcher
from aiogram.filters import Command, CommandObject
from aiogram.types import Message, BotCommand

async def on_startup(bot: Bot):
    await bot.set_my_commands([
        BotCommand(command="subscribe", description="Get reminders for the day"),
        BotCommand(command="dayin", description="Clock IN for the DAY"),
        BotCommand(command="dayout", description="Clock OUT for the DAY"),
        BotCommand(command="lunchin", description="Clock IN for LUNCH"),
        BotCommand(command="lunchout", description="Clock OUT for LUNCH"),
        BotCommand(command="reset", description="Reset all actions"),
        BotCommand(command="status", description="Show status of actions"),
    ])
dp = Dispatcher()

os.makedirs('subscribers', exist_ok=True)
def is_subscribed(message: Message) -> dict | None:
    if os.path.exists(f'subscribers/{message.from_user.id}.json'):
        return json.load(open(f'subscribers/{message.from_user.id}.json'))
    return None

def subscribe(message: Message):
    if not is_subscribed(message):
        json.dump({
            'id': message.from_user.id,
            'username': message.from_user.username,
            'first_name': message.from_user.first_name,
            'last_name': message.from_user.last_name,
            'log': {}
        }, open(f'subscribers/{message.from_user.id}.json', "w"), indent=2, ensure_ascii=False)
        
def subscriber(message: Message) -> dict:
    return json.load(open(f'subscribers/{message.from_user.id}.json'))

def log_action(message: Message, action: str):
    s = subscriber(message)
    s['log'][action] = datetime.now(timezone.utc).isoformat()
    json.dump(s, open(f'subscribers/{message.from_user.id}.json', "w"), indent=2, ensure_ascii=False)
    return subscriber(message)

def reset_actions(message: Message):
    s = subscriber(message)
    s['log'] = {}
    json.dump(s, open(f'subscribers/{message.from_user.id}.json', "w"), indent=2, ensure_ascii=False)
    return subscriber(message)

def status(message: Message) -> str:
    s = subscriber(message)
    msg = ""
    for k in ["dayin", "lunchout", "lunchin", "dayout"]:
        if k in s['log'] and datetime.fromisoformat(s['log'][k]).strftime('%Y-%m-%d') >= datetime.now(timezone.utc).strftime('%Y-%m-%d'):
            msg += f"{k.upper()}: {datetime.fromisoformat(s['log'][k]).astimezone(ZoneInfo('Asia/Baku')).strftime('%Y-%m-%d %H:%M:%S')}\n"
        else:
            msg += f"{k.upper()}:\n"
    return msg.strip()

@dp.message(Command("start"))
async def command_start_handler(message: Message) -> None:
    await message.answer("Hello! I'm KC Checkin Bot. I can remind you to clock in and out for the day and lunch.")
    
@dp.message(Command("dayin", "dayout", "lunchin", "lunchout", "status"))
async def command_action_handler(message: Message, command: CommandObject) -> None:
    if not (s := is_subscribed(message)):
        await message.answer("❌ You're not subscribed! Please subscribe first.")
        return
    cmd = command.command
    if cmd in ["dayin", "dayout", "lunchin", "lunchout"]:
        log_action(message, cmd)
    await message.answer(f"{status(message)}")
    
@dp.message(Command("reset"))
async def command_reset_handler(message: Message) -> None:
    if not (s := is_subscribed(message)):
        await message.answer("❌ You're not subscribed! Please subscribe first.")
        return
    reset_actions(message)
    await message.answer(f"{status(message)}")
    
@dp.message(Command("subscribe", "follow"))
async def command_subscribe_handler(message: Message) -> None:
    if is_subscribed(message):
        await message.answer("✅ You're already subscribed!")
        return
    
    try:
        if message.text.split(maxsplit=1)[1] == os.getenv('SUBSCRIBER_PASSWORD'):
            logger.info(f"/subscribe from {message.from_user.full_name} ({message.from_user.id})")
            subscribe(message)
            await message.answer("✅ Password correct! You've subscribed to reminders!")
        else:
            await message.answer("❌ Incorrect password. Please try again.")
    except IndexError:
        await message.answer("❌ Please provide a password: /subscribe YOUR_PASSWORD")
        return
            
bot = Bot(token=os.getenv("BOT_TOKEN"))

async def check_reminders_loop():
    while True:
        for f in os.listdir('subscribers'):
            if f.endswith('.json'):
                s = json.load(open(f'subscribers/{f}'))
                n = datetime.now(timezone.utc)
                if n.weekday() >= 5:
                    continue
                ymd, hm =  n.strftime('%Y-%m-%d'), n.strftime('%H:%M')
                log = s.get('log', {})
                dayin = datetime.fromisoformat(log.get('dayin', "2000-12-01T07:15:37.133310+00:00"))
                has_dayin = dayin.strftime('%Y-%m-%d') == ymd
                lunchout = datetime.fromisoformat(log.get('lunchout', "2000-12-01T07:15:37.133310+00:00"))
                has_lunchout = lunchout.strftime('%Y-%m-%d') == ymd
                lunchin = datetime.fromisoformat(log.get('lunchin', "2000-12-01T07:15:37.133310+00:00"))
                has_lunchin = lunchin.strftime('%Y-%m-%d') == ymd
                dayout = datetime.fromisoformat(log.get('dayout', "2000-12-01T07:15:37.133310+00:00"))
                has_dayout = dayout.strftime('%Y-%m-%d') == ymd
                if not has_dayin and hm >= '05:00':
                    await bot.send_message(s['id'], "Reminder: ➡️ 💼 Day IN!")
                if has_dayin and not has_lunchout and hm >= '11:00':
                    await bot.send_message(s['id'], "Reminder: ➡️ 🍽️ Lunch OUT!")
                if has_dayin and has_lunchout and not has_lunchin and lunchout + timedelta(hours=1) <= n:
                    await bot.send_message(s['id'], "Reminder: ↩️ 🍽️ Lunch IN!")
                if has_dayin and has_lunchin and has_lunchout and not has_dayout and hm >= '16:30':
                    await bot.send_message(s['id'], "Reminder: ↩️ 💼 Day OUT!")
        await asyncio.sleep(60*5)

async def main() -> None:
    dp.startup.register(on_startup)
    asyncio.create_task(check_reminders_loop())
    logger.info("🤖 Bot is listening for messages...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
          