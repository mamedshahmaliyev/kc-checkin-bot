import os, dotenv, requests, re, traceback
from zoneinfo import ZoneInfo
dotenv.load_dotenv(override=True)
from textwrap import dedent

from jira import JIRA

from aiogram.exceptions import TelegramForbiddenError

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

from aiogram import Bot, Dispatcher
from aiogram.filters import Command, CommandObject
from aiogram.types import Message, BotCommand, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

import calendar

action_to_icon = {
   "dayin": "➡️🚪",
   "lunchout": "➡️🍽️",
   "lunchin": "↩️🍽️",
   "dayout": "🔚🚪",
}

def create_action_keyboard(action: str) -> InlineKeyboardMarkup:
    """Create an inline keyboard with a button for the specified action"""
    button_text = f"{action_to_icon.get(action, '')} {action.upper().replace('IN', ' IN').replace('OUT', ' OUT')}"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=button_text, callback_data=f"action_{action}")
    ]])
    return keyboard

# Define FSM states for subscription
class SubscribeStates(StatesGroup):
    waiting_for_password = State()
    waiting_for_daily_schedule = State()
    waiting_for_timezone = State()
    waiting_for_bamboo_phpsessid = State()
    waiting_for_jira_credentials = State()
    waiting_for_jira_worklog = State()

async def on_startup(bot: Bot):
    await bot.set_my_commands([
        BotCommand(command="dayin", description="Clock IN for the DAY"),
        BotCommand(command="lunchout", description="Clock OUT for LUNCH"),
        BotCommand(command="lunchin", description="Clock IN for LUNCH"),
        BotCommand(command="dayout", description="Clock OUT for the DAY"),
        BotCommand(command="subscribe", description="Subscribe to get reminders for the day"),
        BotCommand(command="set_bamboo_phpsessid", description="Set Bamboo HR PHPSESSID"),
        BotCommand(command="unset_bamboo_phpsessid", description="Unset Bamboo HR PHPSESSID"),
        BotCommand(command="set_jira_credentials", description="Set Jira credentials"),
        BotCommand(command="unset_jira_credentials", description="Unset Jira credentials"),
        BotCommand(command="add_jira_worklog", description="Add Jira worklog"),
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
def is_subscribed(user_id) -> dict | None:
    if os.path.exists(f'subscribers/{user_id}.json'):
        return json.load(open(f'subscribers/{user_id}.json'))
    return None

def subscribe(message: Message):
    if not is_subscribed(user_id := message.from_user.id):
        json.dump({
            'id': message.from_user.id,
            'username': message.from_user.username,
            'first_name': message.from_user.first_name,
            'last_name': message.from_user.last_name,
            "log": {
                "dayin": "2000-01-01T09:00:00+00:00",
                "lunchout": "2000-01-01T13:00:00+00:00",
                "lunchin": "2000-01-01T14:00:00+00:00",
                "dayout": "2000-01-01T20:30:00+00:00"
            },
            "timezone": "UTC",
            "weekly_schedule": [
                "N/A",
                "N/A",
                "N/A",
                "N/A",
                "N/A",
                "N/A",
                "N/A"
            ]
        }, open(f'subscribers/{message.from_user.id}.json', "w"), indent=2, ensure_ascii=False)
        
def subscriber(user_id) -> dict:
    return json.load(open(f'subscribers/{user_id}.json')) if os.path.exists(f'subscribers/{user_id}.json') else None

def my_info(user_id) -> str:
    s = subscriber(user_id)
    return my_info_from_user_id(user_id)

def date_diff_in_hhmm(date1_str: str, date2_str: str) -> str:
    fmt = '%Y-%m-%d %H:%M:%S'
    delta = abs(datetime.strptime(date2_str, fmt) - datetime.strptime(date1_str, fmt))
    
    total_minutes = int(delta.total_seconds() // 60)
    hours = total_minutes // 60
    minutes = total_minutes % 60
    
    return f"{hours:02d}h:{minutes:02d}m"

def my_info_from_user_id(user_id: int) -> str:
    s = json.load(open(f'subscribers/{user_id}.json'))
    msg = ""
    # msg += f"👤 <b>{message.from_user.full_name}</b>\n\n"
    msg += f"⏱️ Daily Log for: {datetime.now(ZoneInfo(timezone := s.get('timezone', 'UTC'))).strftime('%Y-%m-%d, %a')}:\n"
    for k, v in s['log'].items():
        msg += f"  {action_to_icon[k.lower()]} {k.upper().replace('IN', ' IN').replace('OUT', ' OUT')} - <code>{datetime.fromisoformat(v).astimezone(ZoneInfo(timezone)).strftime('%H:%M:%S')}</code>\n" if v and datetime.fromisoformat(v).astimezone(ZoneInfo(timezone)).strftime('%Y-%m-%d') == datetime.now(ZoneInfo(timezone)).strftime('%Y-%m-%d') else f"  {action_to_icon[k.lower()]} {k.upper().replace('IN', ' IN').replace('OUT', ' OUT')} -\n"
    msg += f"\n📅 Weekly schedule [/set_daily_schedule]:\n"
    msg += "[week_day,day_in,lunch_out,day_out]\n"
    for i, v in enumerate(s.get('weekly_schedule', ['N/A']*7)):
        msg += f"<code>{v}</code> [{calendar.day_abbr[i]}]\n" if v else f"N/A [{calendar.day_abbr[i]}]\n"
    msg += f"\n🌍 Timezone: <code>{timezone}</code>\nUse /set_timezone to update\n"
    if t := s.get('bamboo_phpsessid'):
        msg += f"\n🔐 Bamboo HR PHPSESSID:\n<code>{t[:5]}**{t[-5:]}</code>\nUse /unset_bamboo_phpsessid to unset\nUse /set_bamboo_phpsessid to update\n"
        msg += f"\nBamboo HR Log:\n"
        if err := s.get('bamboo_status', {}).get('error'):
            msg += f" ❗{err}\n"
        for log in s.get('bamboo_status', {}).get('clockEntries', []):
            msg += f"  {log.get('start').split(' ')[1]} -> {log.get('end').split(' ')[1] if log.get('end') else 'now'}, {date_diff_in_hhmm(log.get('start'), log.get('end') or datetime.now(ZoneInfo(timezone)).strftime('%Y-%m-%d %H:%M:%S'))}\n"
    else:
        msg += f"\n🔐 Bamboo HR PHPSESSID: N/A\nuse /set_bamboo_phpsessid to set\n"
    if j := s.get('jira_credentials'):
        jemail, jtoken = j.split(',')
        jtoken = f"{jtoken[:5]}**{jtoken[-5:]}"
        j = f"{jemail},{jtoken}"
        msg += f"\n🐞 Jira Credentials:\n<code>{j}</code>\nUse /unset_jira_credentials to unset\nUse /set_jira_credentials to update\nUse /add_jira_worklog to add Jira worklog.\n"
        msg += f"\n🐞 Worklog for 2 days (/add_jira_worklog):\n"
        for jira_status in s.get('jira_status') or []:
            msg += f"    <code>{jira_status['issue_key']}</code> [{jira_status['time_spent']}]: {jira_status['comment']} [<i>{datetime.fromisoformat(jira_status['date']).astimezone(ZoneInfo(timezone)).strftime('%Y-%m-%d %H:%M')}</i>]\n"
    else:
        msg += f"\n🐞 Jira credentials: N/A\nuse /set_jira_credentials to set\n"
    msg += f"\nℹ️ Use /my_info to show your info."
    
    return msg.strip()

@dp.message(Command("start"))
async def command_start_handler(message: Message) -> None:
    await message.answer(dedent("""
                    Hello! 

                    ⏱️ I'm <b>KC Checkin Bot</b>. 
                    I can remind you to clock in and out for the day and lunch.

                    Use /subscribe and enter password to subscribe for reminders.

                    Use /set_timezone to set your timezone, default is UTC.
                    
                    Use /set_daily_schedule to set your daily schedule for a weekday.
                    
                    Use /set_bamboo_phpsessid to set your Bamboo HR PHPSESSID.

                    Use /set_jira_credentials to set your Jira credentials.
                    Use /add_jira_worklog to add Jira worklog.
                    
                    Use /my_info to show your current info.

                    Checkout ≡ menu for more commands.

                    ⚠️ Important:
                    If bamboo session id is not set clocking in/out in this Telegram bot does NOT register in Bamboo HR.
                    """).strip(), 
                    parse_mode="HTML"
    )
    
@dp.message(Command("cancel"))
async def command_cancel_handler(message: Message, state: FSMContext) -> None:
    current_state = await state.get_state()
    print(current_state, flush=True)
    if current_state:
        await state.clear()
        await message.answer("❌ Input cancelled.")
    else:
        await message.answer("ℹ️ Nothing to cancel.")
    
def update_bamboo_status(user: dict):
    if t := user.get('bamboo_phpsessid'):
        s = requests.Session()
        s.cookies.update({"PHPSESSID": t})
        r = s.get("https://knowledgecity.bamboohr.com/widget/timeTracking")
        try:
            user['bamboo_status'] = r.json()
        except:
            user['bamboo_status'] = {'error': 'PHPSESSID is invalid/expired'}
        json.dump(user, open(f'subscribers/{user['id']}.json', "w"), indent=2, ensure_ascii=False)
        
def bamboo_clock_in_out(user: dict, action: str) -> bool:
    if t := user.get('bamboo_phpsessid'):
        s = requests.Session()
        s.cookies.update({"PHPSESSID": t})
        csrf_token = re.search(r'var\s+CSRF_TOKEN\s*=\s*"([a-f0-9]{128})"', s.get("https://knowledgecity.bamboohr.com/home").text).group(1)
        in_out = "in" if action.lower().endswith("in") else "out"
        employee_id = user.get('bamboo_status', {}).get('employeeId')
        r = s.post(f"https://knowledgecity.bamboohr.com/timesheet/clock/{in_out}/{employee_id}", headers={"x-csrf-token": csrf_token})
        update_bamboo_status(user)
        return r.status_code == 200
    return True
  
def get_jira_credentials(user: dict) -> tuple[str, str]:
    if j := user.get('jira_credentials'):
        return [j.split(',')[0].strip(), j.split(',')[1].strip()]
    return None

def update_jira_status(user: dict):
    if jira_credentials := get_jira_credentials(user):
        jira_status = []
        try:
            jemail, jtoken = jira_credentials
            jira = JIRA(os.getenv('JIRA_SERVER'), basic_auth=tuple(jira_credentials))
            jql_query = f'''issuekey IN updatedBy("{jemail.strip()}", "-3d") ORDER BY created DESC'''
            issues = jira.search_issues(jql_query, maxResults=1000, fields='summary,updated,comment')  # Increase maxResults as needed
            tz = ZoneInfo(user.get('timezone', 'UTC'))
            for issue in issues:
                for wl in sorted(jira.worklogs(issue.key) or [], key=lambda w: w.started or w.created, reverse=True):
                    d = datetime.fromisoformat(wl.started or wl.created).astimezone(tz)
                    if d.date() >= datetime.now().astimezone(tz).date() - timedelta(days=2):
                        if wl.raw['author'].get('emailAddress', '').lower() == jemail.strip().lower():
                            jira_status.append({
                                "issue_key": issue.key,
                                "time_spent": wl.timeSpent or "0m",
                                "comment": wl.raw.get('comment', '').strip(),
                                "date": d.isoformat(),
                            })
        except Exception as e:
            jira_status.append({"comment": f"❌ Error fetching jira worklogs: {e}", "date": datetime.now(ZoneInfo(user.get('timezone', 'UTC'))).isoformat()})
        user['jira_status'] = sorted(jira_status, key=lambda x: x['date'], reverse=True)
        json.dump(user, open(f'subscribers/{user['id']}.json', "w"), indent=2, ensure_ascii=False)
   
            
@dp.callback_query(lambda c: c.data and c.data.startswith("action_"))
async def callback_action_handler(callback: CallbackQuery) -> None:
    """Handle inline button clicks for actions"""
    user_id = callback.from_user.id
    message = callback.message
    if not (s := is_subscribed(user_id)):
        await message.answer("❌ You're not subscribed! Please subscribe first using /subscribe.")
        return
    
    action = callback.data.replace("action_", "")
    if action in ["dayin", "dayout", "lunchin", "lunchout"]:
        loading_msg = await message.answer(f"⏳ Processing {action.upper().replace('IN', ' IN').replace('OUT', ' OUT')}...")
        
        if not bamboo_clock_in_out(s, action):
            await loading_msg.edit_text(f"{my_info(user_id)}", parse_mode='HTML')
            await message.answer("❌ Failed to clock in/out in Bamboo HR. Check Bamboo HR Log in /my_info.", parse_mode='HTML')
            return
        
        s = json.load(open(f'subscribers/{user_id}.json'))
        s['log'][action] = datetime.now(timezone.utc).isoformat()
        json.dump(s, open(f'subscribers/{user_id}.json', "w"), indent=2, ensure_ascii=False)
        
        await callback.answer(f"✅ {action.upper().replace('IN', ' IN').replace('OUT', ' OUT')} logged!", show_alert=False)
        
        await loading_msg.edit_text(f"{my_info(user_id)}", parse_mode='HTML')
        await message.answer(f"✅ {action.upper().replace('IN', ' IN').replace('OUT', ' OUT')} successfully logged!", parse_mode='HTML')
        if not s.get('bamboo_phpsessid'):
            await message.answer(f"⚠️ Don't forget to do the same in <b>Bamboo HR</b>!", parse_mode='HTML')
    else:
        await callback.answer("❌ Invalid action.", show_alert=True)
        
@dp.message(Command("dayin", "dayout", "lunchin", "lunchout", "log"))
async def command_action_handler(message: Message, command: CommandObject) -> None:
    if not (s := is_subscribed(user_id := message.from_user.id)):
        await message.answer("❌ You're not subscribed! Please /subscribe first.")
        return
    user_id = message.from_user.id
    cmd = command.command
    if cmd in ["dayin", "dayout", "lunchin", "lunchout"]:
        loading_msg = await message.answer(f"⏳ Processing {cmd.upper().replace('IN', ' IN').replace('OUT', ' OUT')}...")
        # if datetime.fromisoformat(s.get('log', {}).get(cmd, '2000-01-01T09:00:00+00:00')).astimezone(ZoneInfo(s['timezone'])).strftime('%Y-%m-%d') == datetime.now(ZoneInfo(s['timezone'])).strftime('%Y-%m-%d'):
        #     await message.answer(f"{my_info(user_id)}", parse_mode='HTML')
        #     await message.answer(f"ℹ️ ✅ You've already clocked {cmd.upper()} today at <code>{datetime.fromisoformat(s.get('log', {}).get(cmd, '2000-01-01T09:00:00+00:00')).astimezone(ZoneInfo(s['timezone'])).strftime('%H:%M:%S')}</code>.", parse_mode='HTML')
        #     await message.answer(f"⚠️ Don't forget to do the same in <b>Bamboo HR</b>!", parse_mode='HTML')
        #     return
        if not bamboo_clock_in_out(s, cmd):
            await loading_msg.edit_text(f"{my_info(user_id)}", parse_mode='HTML')
            await message.answer("❌ Failed to clock in/out in Bamboo HR. Check Bamboo HR Log in /my_info.", parse_mode='HTML')
            return
        s = json.load(open(f'subscribers/{user_id}.json'))
        s['log'][cmd] = datetime.now(timezone.utc).isoformat()
        json.dump(s, open(f'subscribers/{user_id}.json', "w"), indent=2, ensure_ascii=False)
        await loading_msg.edit_text(f"{my_info(user_id)}", parse_mode='HTML')
        await message.answer(f"✅ {cmd.upper().replace('IN', ' IN').replace('OUT', ' OUT')} successfully logged!", parse_mode='HTML')
        if not s.get('bamboo_phpsessid'):
            await message.answer(f"⚠️ Don't forget to do the same in <b>Bamboo HR</b>!", parse_mode='HTML')
        return
    await message.answer(f"{my_info(user_id)}", parse_mode='HTML')
    
@dp.message(Command("reset_day"))
async def command_reset_day_handler(message: Message) -> None:
    user_id = message.from_user.id
    s = subscriber(user_id)
    s['log'] = {
        "dayin": "2000-01-01T09:00:00+00:00",
        "lunchout": "2000-01-01T13:00:00+00:00",
        "lunchin": "2000-01-01T14:00:00+00:00",
        "dayout": "2000-01-01T20:30:00+00:00"
    }
    json.dump(s, open(f'subscribers/{message.from_user.id}.json', "w"), indent=2, ensure_ascii=False)
    await message.answer(f"✅ Daily log reseted!")
    await message.answer(f"{my_info(user_id)}", parse_mode='HTML')
    
@dp.message(Command("my_info"))
async def command_my_info_handler(message: Message) -> None:
    if not (s := is_subscribed(user_id := message.from_user.id)):
        await message.answer("❌ You're not subscribed! Please /subscribe first.")
        return
    
    loading_msg = await message.answer("⏳ Loading your info...")
    update_bamboo_status(s)
    update_jira_status(s)
    await loading_msg.edit_text(f"{my_info(user_id)}", parse_mode='HTML')
    
@dp.message(Command("subscribe", "follow"))
async def command_subscribe_handler(message: Message, state: FSMContext) -> None:
    if is_subscribed(user_id := message.from_user.id):
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
    await message.answer("🔐 Please enter your subscriber password (or /cancel to abort):")
    
@dp.message(Command("set_timezone"))
async def command_set_timezone_handler(message: Message, state: FSMContext) -> None:
    if not (s := is_subscribed(user_id := message.from_user.id)):
        await message.answer("❌ You're not subscribed! Please /subscribe first.")
        return
    await state.set_state(SubscribeStates.waiting_for_timezone)
    await message.answer(dedent("""
                🌍 <b>Please set your timezone</b>

                Enter your timezone in this format (tap to copy):

                <code>Asia/Dubai</code>
                <code>Europe/Warsaw</code>
                <code>America/New_York</code>
                <code>Asia/Tokyo</code>

                👉 You can find your exact timezone here: https://en.wikipedia.org/wiki/List_of_tz_database_time_zones

                If you don’t set one, the bot will use UTC (London time, no DST).
                
                /cancel to abort.
                """).strip(),
            parse_mode="HTML"
    )
    
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
    s = subscriber(user_id := message.from_user.id)
    if not is_valid_timezone(message.text.strip()):
        await message.answer("❌ Invalid timezone. Please enter a valid timezone (e.g: 'Asia/Dubai').")
        return
    s['timezone'] = message.text.strip()
    json.dump(s, open(f'subscribers/{message.from_user.id}.json', "w"), indent=2, ensure_ascii=False)
    await message.answer(f"✅ Timezone set to {message.text.strip()}")
    await message.answer(f"{my_info(user_id)}", parse_mode='HTML')
    
@dp.message(Command("set_bamboo_phpsessid"))
async def command_set_bamboo_phpsessid_handler(message: Message, state: FSMContext) -> None:
    if not (s := is_subscribed(user_id := message.from_user.id)):
        await message.answer("❌ You're not subscribed! Please /subscribe first.")
        return
    await state.set_state(SubscribeStates.waiting_for_bamboo_phpsessid)
    await message.answer("""
                    🔐 Please enter your Bamboo HR <b>PHPSESSID</b>
                    Use /cancel to abort.

                    Login into Bamboo HR and copy the PHPSESSID from the browser cookies:
                    <code>CTRL+SHIFT+I (Developer Tools) -> Application -> Cookies -> PHPSESSID</code>

                    PHPSESSID looks like this:
                    <i>mThcCZD%2N5wGtGkCsCNb1h6YIt7ML3lW</i>
                    
                    /cancel to abort.
                    """.replace('                    ','').strip(), 
                    parse_mode="HTML"
    )
    
@dp.message(SubscribeStates.waiting_for_bamboo_phpsessid)
async def process_bamboo_phpsessid_handler(message: Message, state: FSMContext) -> None:
    await state.clear()
    s = subscriber(user_id := message.from_user.id)
    if not message.text or not message.text.strip():
        await message.answer(f"{my_info(user_id)}", parse_mode='HTML')
        await message.answer("❌ Action aborted.")
        return
    s['bamboo_phpsessid'] = message.text.strip()
    json.dump(s, open(f'subscribers/{message.from_user.id}.json', "w"), indent=2, ensure_ascii=False)
    await message.answer(f"{my_info(user_id)}", parse_mode='HTML')
    await message.answer(f"✅ Bamboo HR PHPSESSID set successfully!")
    
@dp.message(Command("unset_bamboo_phpsessid"))
async def command_unset_bamboo_phpsessid_handler(message: Message, state: FSMContext) -> None:
    if not (s := is_subscribed(user_id := message.from_user.id)):
        await message.answer("❌ You're not subscribed! Please /subscribe first.")
        return
    await state.clear()
    s['bamboo_phpsessid'] = None
    json.dump(s, open(f'subscribers/{message.from_user.id}.json', "w"), indent=2, ensure_ascii=False)
    await message.answer(f"{my_info(user_id)}", parse_mode='HTML')
    await message.answer(f"✅ Bamboo HR PHPSESSID unset successfully!")
    
@dp.message(Command("set_jira_credentials"))
async def command_set_jira_credentials_handler(message: Message, state: FSMContext) -> None:
    if not (s := is_subscribed(user_id := message.from_user.id)):
        await message.answer("❌ You're not subscribed! Please /subscribe first.")
        return
    await state.set_state(SubscribeStates.waiting_for_jira_credentials)
    await message.answer(dedent("""
                    🐞 Please enter your Jira credentials in format:
                    <code>your_email,api_token</code>
                    
                    Use: https://id.atlassian.com/manage-profile/security/api-tokens to generate your API token.
                    
                    /cancel to abort.
                    """).strip(),
                    parse_mode="HTML"
    )
    
@dp.message(SubscribeStates.waiting_for_jira_credentials)
async def process_jira_credentials_handler(message: Message, state: FSMContext) -> None:
    await state.clear()
    s = subscriber(user_id := message.from_user.id)
    if len(message.text.strip().split(',')) != 2:
        await message.answer("❌ Invalid Jira credentials. Please enter a valid email and api_token in the format email,api_token.")
        return
    s['jira_credentials'] = message.text.strip()
    json.dump(s, open(f'subscribers/{message.from_user.id}.json', "w"), indent=2, ensure_ascii=False)
    await message.answer(f"{my_info(user_id)}", parse_mode='HTML')
    await message.answer(f"✅ Jira credentials set successfully!")
    
@dp.message(Command("unset_jira_credentials"))
async def command_unset_jira_credentials_handler(message: Message, state: FSMContext) -> None:
    if not (s := is_subscribed(user_id := message.from_user.id)):
        await message.answer("❌ You're not subscribed! Please /subscribe first.")
        return
    await state.clear()
    s['jira_credentials'] = None
    json.dump(s, open(f'subscribers/{message.from_user.id}.json', "w"), indent=2, ensure_ascii=False)
    await message.answer(f"{my_info(user_id)}", parse_mode='HTML')

@dp.message(Command("add_jira_worklog"))
async def command_add_jira_worklog_handler(message: Message, state: FSMContext) -> None:
    if not (s := subscriber(user_id := message.from_user.id)):
        await message.answer("❌ You're not subscribed! Please /subscribe first.")
        return
    await state.set_state(SubscribeStates.waiting_for_jira_worklog)
    examples = []
    examples.append(f"<code>KC-123,{datetime.now(ZoneInfo(s.get('timezone', 'UTC'))).strftime('%Y-%m-%d %H:%M')},45m,Worked on feature X</code>")
    examples.append(f"<code>KC-456,{datetime.now(ZoneInfo(s.get('timezone', 'UTC'))).strftime('%H:%M')},1h 5m,Meeting with Alice & Bob</code> (note: date is today if not provided)")
    for i, jira_status in enumerate((s.get('jira_status') or [])[:4]):
        fmt = '%H:%M'
        examples.append(f"<code>{jira_status['issue_key']},{datetime.fromisoformat(jira_status['date']).astimezone(ZoneInfo(s.get('timezone', 'UTC'))).strftime(fmt)},{jira_status['time_spent']},{jira_status['comment']}</code>")
    examples = '\n\n'.join(examples)
    await message.answer(dedent(f"""
                🐞 Please enter your Jira worklog in format:
                
                <code>issue_key,started_at,time_spent,comment</code>
                
                Examples (tap to copy):
                
                [examples]
                
                /cancel to abort.
                    """).replace('[examples]', examples).strip(),
                    parse_mode="HTML"
    )
    
@dp.message(SubscribeStates.waiting_for_jira_worklog)
async def process_jira_worklog_handler(message: Message, state: FSMContext) -> None:
    if not is_subscribed(user_id := message.from_user.id):
        await message.answer("❌ You're not subscribed! Please /subscribe first.")
        return
    s = subscriber(user_id)
    if not (jira_credentials := get_jira_credentials(s)):
        await message.answer("❌ Jira credentials not set. Please set Jira credentials first using /set_jira_credentials.")
        return
    await state.clear()
    arr = message.text.strip().split(',')
    if len(arr) < 4:
        await message.answer("❌ Invalid Jira worklog format.")
        return
    issue_id, started_at, time_spent, comment = arr[0].strip(), arr[1].strip(), arr[2].strip(), ','.join(arr[3:]).strip()
    try:
        if len(started_at) == 5:
            started_at = datetime.now(ZoneInfo(s['timezone'])).replace(hour=int(started_at.split(':')[0]), minute=int(started_at.split(':')[1]))
        else:
            started_at = datetime.strptime(started_at, '%Y-%m-%d %H:%M').astimezone(ZoneInfo(s['timezone']))
    except Exception as e:
        await message.answer(f"❌ Invalid started at format. Please enter a valid started at in the format yyyy-mm-dd hh:mm or in hh:mm format (e.g: 09:00). {e}")
        return
    jira = JIRA(options={'server': os.getenv('JIRA_SERVER')}, basic_auth=tuple(jira_credentials))
    jira.add_worklog(issue=issue_id, started=started_at, timeSpent=time_spent, comment=comment)
    update_jira_status(subscriber(user_id))
    await message.answer(f"{my_info(user_id)}", parse_mode='HTML')
    await message.answer(f"✅ Jira worklog added successfully!")
   
@dp.message(Command("set_daily_schedule"))
async def command_set_daily_schedule_handler(message: Message, state: FSMContext) -> None:
    if not (s := is_subscribed(user_id := message.from_user.id)):
        await message.answer("❌ You're not subscribed! Please /subscribe first.")
        return
    await state.set_state(SubscribeStates.waiting_for_daily_schedule)
    await message.answer(dedent("""
                         
                    🕒 Please enter your daily schedule for a weekday.

                    Add schedule in the format:
                    <code>week_day,day_in,lunch_out,day_out</code>

                    week_day: number between 1 and 7 (1 = Monday, 7 = Sunday)
                    day_in, lunch_out, day_out: hh:mm in 24-hour format

                    📋 Example (tap to copy):
                    <code>1,09:00,13:00,18:00</code>

                    → This sets Monday: in 09:00 · lunch 13:00 · out 18:00

                    To delete a day: <code>-1</code> (removes Monday)

                    Use /my_info to see your current schedule.
                    
                    /cancel to abort.
                    """).strip(),
                    parse_mode="HTML"
    )
    
def is_hh_mm(time_str: str) -> bool:
    try:
        datetime.strptime(time_str, "%H:%M")
        return True
    except ValueError:
        return False
    
@dp.message(SubscribeStates.waiting_for_daily_schedule)
async def process_daily_schedule_handler(message: Message, state: FSMContext) -> None:
    await state.clear()
    s = subscriber(user_id := message.from_user.id)
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
        await message.answer(f"{my_info(user_id)}", parse_mode='HTML')
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
    await message.answer(f"✅ Your updated weekly schedule is:\n{json.dumps(subscriber(message.from_user.id)['weekly_schedule'], indent=2, ensure_ascii=False)}\n\nSet another day with /set_daily_schedule")
    await message.answer(f"{my_info(user_id)}", parse_mode='HTML')
       
@dp.message(Command("unsubscribe", "unfollow"))
async def command_unsubscribe_handler(message: Message) -> None:
    if os.path.exists(f'subscribers/{message.from_user.id}.json'):
        os.remove(f'subscribers/{message.from_user.id}.json')
        await message.answer("✅ You've unsubscribed from reminders!")
    else:
        await message.answer("❌ You're not subscribed! Please /subscribe first.")

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
        await message.answer(f"{my_info(message.from_user.id)}", parse_mode='HTML')
        await message.answer("✅ Password correct! You've subscribed to reminders!")
        await state.clear()
    else:
        await message.answer("❌ Incorrect password. Please try again or type /cancel to abort.")
            
bot = Bot(token=os.getenv("BOT_TOKEN"))

last_reminder_messages = {}
async def check_reminders_loop():
    while True:
        for f in os.listdir('subscribers'):
            if f.endswith('.json'):
                try:
                    s = json.load(open(f'subscribers/{f}'))
                    update_bamboo_status(s)
                    update_jira_status(s)
                    n = datetime.now(ZoneInfo(s.get('timezone', 'UTC')))
                    week_day = n.isoweekday()
                    past = n - timedelta(hours=48)
                    ymd, hm =  n.strftime('%Y-%m-%d'), n.strftime('%H:%M')
                    schedule = [a for a in s.get('weekly_schedule', []) if a and a.split(',')[0] == str(week_day)]
                    if not schedule:
                        continue
                    target_day_in, target_lunch_out, target_day_out = schedule[0].split(',')[1:]
                    log = s.get('log', {})
                    has_dayin = datetime.fromisoformat(log.get('dayin', past.isoformat())).astimezone(ZoneInfo(s.get('timezone', 'UTC'))).strftime('%Y-%m-%d') == ymd
                    has_lunchout = (lunchout :=datetime.fromisoformat(log.get('lunchout', past.isoformat()))).astimezone(ZoneInfo(s.get('timezone', 'UTC'))).strftime('%Y-%m-%d') == ymd
                    has_lunchin = datetime.fromisoformat(log.get('lunchin', past.isoformat())).astimezone(ZoneInfo(s.get('timezone', 'UTC'))).strftime('%Y-%m-%d') == ymd
                    has_dayout = datetime.fromisoformat(log.get('dayout', past.isoformat())).astimezone(ZoneInfo(s.get('timezone', 'UTC'))).strftime('%Y-%m-%d') == ymd
                    message, action = None, None
                    if not has_dayin and hm >= target_day_in.strip().lower():
                        message = await bot.send_message(s['id'], f"Reminder: {action_to_icon['dayin']} Day IN!", reply_markup=create_action_keyboard('dayin'))
                        action = 'dayin'
                    if has_dayin and not has_lunchout and hm >= target_lunch_out.strip().lower():
                        message = await bot.send_message(s['id'], f"Reminder: {action_to_icon['lunchout']} Lunch OUT!", reply_markup=create_action_keyboard('lunchout'))
                        action = 'lunchout'
                    if has_dayin and has_lunchout and not has_lunchin and lunchout + timedelta(hours=1) <= n:
                        message = await bot.send_message(s['id'], f"Reminder: {action_to_icon['lunchin']} Lunch IN!", reply_markup=create_action_keyboard('lunchin'))
                        action = 'lunchin'
                    if has_dayin and has_lunchin and has_lunchout and not has_dayout and hm >= target_day_out.strip().lower():
                        message = await bot.send_message(s['id'], f"Reminder: {action_to_icon['dayout']} Day OUT!", reply_markup=create_action_keyboard('dayout'))
                        action = 'dayout'
                    if message and action:
                        if last_reminder_messages.get(f"{s['id']}") and last_reminder_messages[f"{s['id']}"].get('action') == action:
                            await bot.delete_message(s['id'], last_reminder_messages[f"{s['id']}"].get('id'))
                        last_reminder_messages[f"{s['id']}"] = {'id': message.message_id, 'action': action}
                except TelegramForbiddenError as e:
                    if os.path.exists(f'subscribers/{s['id']}.json'):
                        os.remove(f'subscribers/{s['id']}.json')
                        logger.error(f"Unsubscribed user due to blocking the bot {f}: {e}. {s}")
                except Exception as e:
                    traceback.print_exc()
                    logger.error(f"Error checking reminders for {f}: {e}. {s}")
                    
        await asyncio.sleep(60*5)

async def main() -> None:
    dp.startup.register(on_startup)
    asyncio.create_task(check_reminders_loop())
    logger.info("🤖 Bot is listening for messages...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
