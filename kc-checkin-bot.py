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
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

import calendar

# Define FSM states for subscription
class SubscribeStates(StatesGroup):
    waiting_for_password = State()
    waiting_for_daily_schedule = State()
    waiting_for_timezone = State()

async def on_startup(bot: Bot):
    await bot.set_my_commands([
        BotCommand(command="dayin", description="Clock IN for the DAY"),
        BotCommand(command="lunchout", description="Clock OUT for LUNCH"),
        BotCommand(command="lunchin", description="Clock IN for LUNCH"),
        BotCommand(command="dayout", description="Clock OUT for the DAY"),
        BotCommand(command="subscribe", description="Subscribe to get reminders for the day"),
        BotCommand(command="my_info", description="Show your info"),
        BotCommand(command="set_daily_schedule", description="Set your daily schedule for a weekday"),
        BotCommand(command="set_timezone", description="Set your timezone, default is UTC"),
        BotCommand(command="reset_day", description="Reset log for the day"),
        BotCommand(command="log", description="Show log for the day"),
        BotCommand(command="unsubscribe", description="Unsubscribe from reminders"),
        BotCommand(command="cancel", description="Cancel the current action"),
    ])

dp = Dispatcher(storage=MemoryStorage())

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

def my_info(message: Message) -> str:
    s = subscriber(message)
    msg = f"👤 <b>{message.from_user.full_name}</b>:\n"
    msg += f"Daily log:\n"
    for k, v in s['log'].items():
        msg += f"  {k.upper()}: <code>{datetime.fromisoformat(v).astimezone(ZoneInfo(s.get('timezone', 'UTC'))).strftime('%Y-%m-%d %H:%M:%S')}</code>\n" if v and datetime.fromisoformat(v).astimezone(ZoneInfo(s.get('timezone', 'UTC'))).strftime('%Y-%m-%d %H:%M:%S') >= datetime.now(ZoneInfo(s.get('timezone', 'UTC'))).strftime('%Y-%m-%d') else f"  {k.upper()}:\n"
    msg += f"Timezone: <code>{s.get('timezone', 'UTC')}</code>\n"
    msg += f"Weekly schedule:\n"
    msg += "[week_day,day_in,lunch_out,day_out]\n"
    for i, v in enumerate(s.get('weekly_schedule', ['N/A']*7)):
        msg += f"<code>{v}</code> [{calendar.day_abbr[i]}]\n" if v else f"N/A [{calendar.day_abbr[i]}]\n"
    return msg.strip()

@dp.message(Command("start"))
async def command_start_handler(message: Message) -> None:
    await message.answer("""
Hello! I'm KC Checkin Bot. 
I can remind you to clock in and out for the day and lunch.

Use /subscribe to subscribe to reminders.
Use /my_info to show your current info.

Use /set_daily_schedule to set your daily schedule for a weekday.
Use /set_timezone to set your timezone, default is UTC.

Checkout menu for more commands.

⚠️ Important: Clocking in/out in this Telegram bot does NOT register in Bamboo HR.
Please remember to also clock in/out in Bamboo HR itself!
""".strip())
    
@dp.message(Command("dayin", "dayout", "lunchin", "lunchout", "log"))
async def command_action_handler(message: Message, command: CommandObject) -> None:
    if not (s := is_subscribed(message)):
        await message.answer("❌ You're not subscribed! Please subscribe first.")
        return
    cmd = command.command
    if cmd in ["dayin", "dayout", "lunchin", "lunchout"]:
        log_action(message, cmd)
    await message.answer(f"{my_info(message)}", parse_mode='HTML')
    
@dp.message(Command("reset_day"))
async def command_reset_handler(message: Message) -> None:
    if not (s := is_subscribed(message)):
        await message.answer("❌ You're not subscribed! Please subscribe first.")
        return
    reset_actions(message)
    await message.answer(f"{my_info(message)}", parse_mode='HTML')
    
@dp.message(Command("my_info"))
async def command_my_info_handler(message: Message) -> None:
    if not (s := is_subscribed(message)):
        await message.answer("❌ You're not subscribed! Please subscribe first.")
        return
    await message.answer(f"{my_info(message)}", parse_mode='HTML')
    
@dp.message(Command("subscribe", "follow"))
async def command_subscribe_handler(message: Message, state: FSMContext) -> None:
    if is_subscribed(message):
        await message.answer("✅ You're already subscribed!")
        return
    
    # Check if password was provided inline (backward compatibility)
    try:
        password = message.text.split(maxsplit=1)[1]
        if password == os.getenv('SUBSCRIBER_PASSWORD'):
            logger.info(f"/subscribe from {message.from_user.full_name} ({message.from_user.id})")
            subscribe(message)
            await message.answer("✅ Password correct! You've subscribed to reminders!")
        else:
            await message.answer("❌ Incorrect password. Please try again.")
        return
    except IndexError:
        pass  # No inline password, proceed to ask for it
    
    # Ask for password in next message
    await state.set_state(SubscribeStates.waiting_for_password)
    await message.answer("🔐 Please enter your subscriber password:")
    
@dp.message(Command("set_timezone"))
async def command_set_timezone_handler(message: Message, state: FSMContext) -> None:
    await state.set_state(SubscribeStates.waiting_for_timezone)
    await message.answer("""🌍 <b>Please set your timezone</b>

Enter your timezone in this format (tap to copy):

<code>Asia/Dubai</code>
<code>Europe/Warsaw</code>
<code>America/New_York</code>
<code>Asia/Tokyo</code>

👉 You can find your exact timezone here: https://en.wikipedia.org/wiki/List_of_tz_database_time_zones

If you don’t set one, the bot will use UTC (London time, no DST).

Just copy your city and send it!""",
parse_mode="HTML")
    
def is_valid_timezone(tz_name: str) -> bool:
    """Returns True if the timezone string exists in the IANA database"""
    if not tz_name or not isinstance(tz_name, str):
        return False
    try:
        ZoneInfo(tz_name.strip())
        return True
    except Exception as e:
        return False  

@dp.message(SubscribeStates.waiting_for_timezone)
async def process_timezone_handler(message: Message, state: FSMContext) -> None:
    await state.clear()
    s = subscriber(message)
    if not is_valid_timezone(message.text.strip()):
        await message.answer("❌ Invalid timezone. Please enter a valid timezone (e.g: 'Asia/Dubai').")
        return
    s['timezone'] = message.text.strip()
    json.dump(s, open(f'subscribers/{message.from_user.id}.json', "w"), indent=2, ensure_ascii=False)
    await message.answer(f"✅ Timezone set to {message.text.strip()}")
    await message.answer(f"{my_info(message)}", parse_mode='HTML')
    
@dp.message(Command("set_daily_schedule"))
async def command_set_daily_schedule_handler(message: Message, state: FSMContext) -> None:
    await state.set_state(SubscribeStates.waiting_for_daily_schedule)
    await message.answer("""🕒 Please enter your daily schedule for a weekday.

Add schedule in the format:
<code>week_day,day_in,lunch_out,day_out</code>

week_day: number between 1 and 7 (1 = Monday, 7 = Sunday)
day_in, lunch_out, day_out: hh:mm in 24-hour format

📋 Example (tap to copy):
<code>1,09:00,13:00,18:00</code>

→ This sets Monday: in 09:00 · lunch 13:00 · out 18:00

To delete a day: <code>-1</code> (removes Monday)

Use /my_info to see your current schedule.""",
parse_mode="HTML")
    
def is_hh_mm(time_str: str) -> bool:
    try:
        datetime.strptime(time_str, "%H:%M")
        return True
    except ValueError:
        return False
    
@dp.message(SubscribeStates.waiting_for_daily_schedule)
async def process_daily_schedule_handler(message: Message, state: FSMContext) -> None:
    await state.clear()
    s = subscriber(message)
    msg = message.text.strip()
    if msg.startswith('-'):
        week_day = int(msg.split(',')[0].strip('-'))
        if not 1 <= week_day <= 7:
            await message.answer("❌ Invalid week day number. Please enter a number between 1 and 7. 1 is Monday, 7 is Sunday.")
            return
        arr = s.get('weekly_schedule', [])
        if len(arr) != 7:
            arr = ['N/A' for i in range(7)]
        arr[week_day - 1] = 'N/A'
        s['weekly_schedule'] = arr
        json.dump(s, open(f'subscribers/{message.from_user.id}.json', "w"), indent=2, ensure_ascii=False)
        await message.answer(f"✅ Weekly schedule updated.")
        await message.answer(f"{my_info(message)}", parse_mode='HTML')
        return
    week_day, day_in, lunch_out, day_out = message.text.split(',')
    if not 1 <= int(week_day := week_day.strip()) <= 7:
        await message.answer("❌ Invalid week day number. Please enter a number between 1 and 7. 1 is Monday, 7 is Sunday.")
        return
    week_day = int(week_day)
    if not is_hh_mm(day_in := day_in.strip()):
        await message.answer("❌ Invalid day in time. Please enter a valid time in the format hh:mm.")
        return
    if not is_hh_mm(lunch_out := lunch_out.strip()):
        await message.answer("❌ Invalid lunch out time. Please enter a valid time in the format hh:mm.")
        return
    if not is_hh_mm(day_out := day_out.strip()):
        await message.answer("❌ Invalid day out time. Please enter a valid time in the format hh:mm.")
        return
    arr = s.get('weekly_schedule', [])
    if len(arr) != 7:
        arr = ['N/A' for i in range(7)]
    arr[week_day - 1] = f"{week_day},{day_in},{lunch_out},{day_out}"
    s['weekly_schedule'] = arr
    json.dump(s, open(f'subscribers/{message.from_user.id}.json', "w"), indent=2, ensure_ascii=False)
    await message.answer(f"✅ Your updated weekly schedule is:\n{json.dumps(subscriber(message)['weekly_schedule'], indent=2, ensure_ascii=False)}")
    await message.answer(f"{my_info(message)}", parse_mode='HTML')
    

    
    
@dp.message(Command("unsubscribe", "unfollow"))
async def command_unsubscribe_handler(message: Message) -> None:
    if os.path.exists(f'subscribers/{message.from_user.id}.json'):
        os.remove(f'subscribers/{message.from_user.id}.json')
        await message.answer("✅ You've unsubscribed from reminders!")
    else:
        await message.answer("❌ You're not subscribed! Please subscribe first.")

@dp.message(Command("cancel"))
async def command_cancel_handler(message: Message, state: FSMContext) -> None:
    current_state = await state.get_state()
    if current_state:
        await state.clear()
        await message.answer("❌ Input cancelled.")
    else:
        await message.answer("ℹ️ Nothing to cancel.")

@dp.message(SubscribeStates.waiting_for_password)
async def process_password_handler(message: Message, state: FSMContext) -> None:
    # Check if user wants to cancel
    if message.text and message.text.strip().lower() in ['/cancel', 'cancel']:
        await state.clear()
        await message.answer("❌ Subscription cancelled.")
        return
    
    password = message.text.strip()
    
    if password == os.getenv('SUBSCRIBER_PASSWORD'):
        logger.info(f"/subscribe from {message.from_user.full_name} ({message.from_user.id})")
        subscribe(message)
        await message.answer("✅ Password correct! You've subscribed to reminders!")
        await state.clear()
    else:
        await message.answer("❌ Incorrect password. Please try again or type /cancel to abort.")
            
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
          