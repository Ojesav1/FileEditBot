import os
import json
import uuid
import asyncio
import logging
from datetime import datetime
from flask import Flask, request

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from telegram.constants import ParseMode
from PIL import Image

# ============================================
# ENVIRONMENT VARIABLES (Set in Bot Business)
# ============================================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
OWNER_ID = int(os.environ.get("OWNER_ID", "0"))
ADMIN_IDS_STR = os.environ.get("ADMIN_IDS", "")
ADMIN_IDS = [int(x.strip()) for x in ADMIN_IDS_STR.split(",") if x.strip()]
LOG_CHANNEL_ID = int(os.environ.get("LOG_CHANNEL_ID", "0"))
PORT = int(os.environ.get("PORT", "8080"))
# ============================================

MAX_FILE_SIZE = 20000000000
DAILY_LIMIT_FREE = 2000
DAILY_LIMIT_PREMIUM = 20000
TEMP_DIR = "temp_files"
THUMB_DIR = "thumbnails"

# Emojis
ROCKET = "🚀"
STAR = "⭐"
CROWN = "👑"
SPARKLES = "✨"
CHECK = "✅"
CROSS = "❌"
WARNING = "⚠️"
FOLDER = "📁"
IMAGE = "🖼️"
PACKAGE = "📦"
GEAR = "⚙️"
PENCIL = "✏️"
TRASH = "🗑️"
LOCK = "🔒"
GLOBE = "🌐"
PEOPLE = "👥"
CHART = "📊"
ROBOT = "🤖"

# Logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Create directories
os.makedirs(TEMP_DIR, exist_ok=True)
os.makedirs(THUMB_DIR, exist_ok=True)

# ============================================
# DATABASE CLASS
# ============================================
class Database:
    def __init__(self):
        self.user_file = "users.json"
        self.file_file = "files.json"
        self.admin_file = "admin.json"
        self.users = self.load(self.user_file, {
            "users": {}, 
            "banned": {}, 
            "premium": {}, 
            "daily": {}
        })
        self.files = self.load(self.file_file, {
            "files": {}, 
            "total": 0, 
            "daily": {}
        })
        self.admin = self.load(self.admin_file, {
            "logs": [], 
            "maintenance": False, 
            "settings": {
                "max_size": MAX_FILE_SIZE,
                "daily_free": DAILY_LIMIT_FREE,
                "daily_premium": DAILY_LIMIT_PREMIUM
            }
        })
    
    def load(self, path, default):
        try:
            if os.path.exists(path):
                with open(path, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception as e:
            logger.error(f"Error loading {path}: {e}")
        self.save(path, default)
        return default
    
    def save(self, path, data=None):
        try:
            if data is None:
                basename = os.path.basename(path).replace('.json', '')
                data = getattr(self, basename, {})
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Error saving {path}: {e}")
    
    def save_all(self):
        self.save(self.user_file, self.users)
        self.save(self.file_file, self.files)
        self.save(self.admin_file, self.admin)

db = Database()

# ============================================
# IN-MEMORY STORAGE
# ============================================
user_thumbs = {}
user_rename = {}

# ============================================
# KEYBOARDS
# ============================================
def main_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"{ROCKET} Start Processing", callback_data='menu_process')],
        [
            InlineKeyboardButton(f"{PENCIL} Rename File", callback_data='menu_rename'),
            InlineKeyboardButton(f"{IMAGE} Thumbnail", callback_data='menu_thumb')
        ],
        [
            InlineKeyboardButton(f"{FOLDER} My Files", callback_data='menu_files'),
            InlineKeyboardButton(f"{GEAR} Settings", callback_data='menu_settings')
        ],
        [InlineKeyboardButton(f"{GLOBE} Help", callback_data='menu_help')]
    ])

def admin_panel_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"{PEOPLE} Users", callback_data='admin_users'),
            InlineKeyboardButton(f"{CHART} Stats", callback_data='admin_stats')
        ],
        [
            InlineKeyboardButton(f"{WARNING} Bans", callback_data='admin_bans'),
            InlineKeyboardButton(f"{STAR} Premium", callback_data='admin_premium')
        ],
        [
            InlineKeyboardButton(f"{TRASH} Cleanup", callback_data='admin_cleanup'),
            InlineKeyboardButton(f"{LOCK} Maintenance", callback_data='admin_maintenance')
        ],
        [InlineKeyboardButton(f"{GLOBE} Back", callback_data='back_main')]
    ])

def file_actions_keyboard(file_uid):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"{ROCKET} Send Now", callback_data=f'send_{file_uid}'),
            InlineKeyboardButton(f"{PENCIL} Rename", callback_data=f'rename_{file_uid}')
        ],
        [
            InlineKeyboardButton(f"{IMAGE} Thumbnail", callback_data=f'thumb_{file_uid}'),
            InlineKeyboardButton(f"{TRASH} Delete", callback_data=f'delete_{file_uid}')
        ],
        [InlineKeyboardButton(f"{GLOBE} Back", callback_data='back_main')]
    ])

# ============================================
# HELPER FUNCTIONS
# ============================================
def is_admin(user_id):
    return user_id in ADMIN_IDS or user_id == OWNER_ID

def is_owner(user_id):
    return user_id == OWNER_ID

async def log_to_channel(context, uid, file_name, new_name=None, file_size=None, action="processed"):
    try:
        user = await context.bot.get_chat(uid)
        user_mention = f"<a href='tg://user?id={uid}'>{user.first_name}</a>"
        size_str = f"\n📦 Size: {file_size/1024/1024:.2f} MB" if file_size else ""
        rename_str = f"\n📝 Renamed: {new_name}" if new_name else ""
        log_text = (
            f"{PACKAGE} <b>File {action.title()}</b>\n\n"
            f"👤 User: {user_mention}\n"
            f"🆔 ID: <code>{uid}</code>\n"
            f"📁 File: <code>{file_name}</code>{rename_str}{size_str}\n"
            f"⏰ Time: <code>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</code>\n"
            f"🤖 Bot: @FileEditBot"
        )
        await context.bot.send_message(
            chat_id=LOG_CHANNEL_ID,
            text=log_text,
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.error(f"Log error: {e}")

async def forward_to_channel(update, context):
    try:
        user_info = (
            f"👤 User: {update.effective_user.first_name}\n"
            f"🆔 ID: <code>{update.effective_user.id}</code>"
        )
        if update.message.document:
            await context.bot.send_document(
                chat_id=LOG_CHANNEL_ID,
                document=update.message.document.file_id,
                caption=f"{PACKAGE} <b>New Document</b>\n\n{user_info}",
                parse_mode=ParseMode.HTML
            )
        elif update.message.video:
            await context.bot.send_video(
                chat_id=LOG_CHANNEL_ID,
                video=update.message.video.file_id,
                caption=f"🎬 <b>New Video</b>\n\n{user_info}",
                parse_mode=ParseMode.HTML
            )
        elif update.message.audio:
            await context.bot.send_audio(
                chat_id=LOG_CHANNEL_ID,
                audio=update.message.audio.file_id,
                caption=f"🎵 <b>New Audio</b>\n\n{user_info}",
                parse_mode=ParseMode.HTML
            )
        elif update.message.photo:
            await context.bot.send_photo(
                chat_id=LOG_CHANNEL_ID,
                photo=update.message.photo[-1].file_id,
                caption=f"🖼️ <b>New Photo</b>\n\n{user_info}",
                parse_mode=ParseMode.HTML
            )
    except Exception as e:
        logger.error(f"Forward error: {e}")

# ============================================
# COMMAND HANDLERS
# ============================================
async def start(update, context):
    try:
        user = update.effective_user
        uid = str(user.id)
        
        if uid not in db.users['users']:
            db.users['users'][uid] = {
                'name': user.first_name,
                'username': user.username,
                'joined': datetime.now().isoformat(),
                'files': 0
            }
            db.save_all()
        
        if uid in db.users['banned']:
            await update.message.reply_text(f"{CROSS} You are banned!")
            return
        
        if db.admin['maintenance'] and not is_admin(user.id):
            await update.message.reply_text(f"{GEAR} Bot is under maintenance.")
            return
        
        is_prem = uid in db.users['premium']
        has_thumb = uid in user_thumbs
        
        welcome = (
            f"{ROCKET} <b>Welcome to FileEditBot!</b>\n\n"
            f"Hey {user.first_name}!\n\n"
            f"{SPARKLES} I can help you:\n"
            f"• Rename files\n"
            f"• Add custom thumbnails\n"
            f"• Process videos & documents\n\n"
            f"{IMAGE} Thumbnail: {'✅ Set' if has_thumb else '❌ Not set'}\n"
            f"{STAR} Premium: {'Active' if is_prem else 'Free'}\n"
            f"{FOLDER} Daily Limit: {DAILY_LIMIT_PREMIUM if is_prem else DAILY_LIMIT_FREE}\n\n"
            f"Send me a file to start!"
        )
        await update.message.reply_text(
            welcome,
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu_keyboard()
        )
    except Exception as e:
        logger.error(f"Start error: {e}")

async def help_cmd(update, context):
    try:
        user_id = update.effective_user.id
        admin_check = is_admin(user_id)
        
        help_text = (
            f"{GLOBE} <b>Help Menu</b>\n\n"
            f"<b>User Commands:</b>\n"
            f"/start - Main menu\n"
            f"/help - This help message\n"
            f"/stats - Bot statistics\n\n"
            f"<b>How to use:</b>\n"
            f"1. Send me any file (document, video, audio)\n"
            f"2. Use buttons to rename or add thumbnail\n"
            f"3. Click Send Now to get your file\n\n"
        )
        
        if admin_check:
            help_text += (
                f"{CROWN} <b>Admin Commands:</b>\n"
                f"/admin - Admin panel\n"
                f"/ban [id] [reason] - Ban user\n"
                f"/unban [id] - Unban user\n"
                f"/broadcast [msg] - Broadcast to all users\n"
                f"/addpremium [id] - Add premium user\n"
                f"/rempremium [id] - Remove premium user\n"
            )
        
        await update.message.reply_text(
            help_text,
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu_keyboard()
        )
    except Exception as e:
        logger.error(f"Help error: {e}")

async def stats_cmd(update, context):
    try:
        total_users = len(db.users['users'])
        total_files = db.files['total']
        stats_text = (
            f"{CHART} <b>Bot Stats</b>\n\n"
            f"{PEOPLE} Users: <code>{total_users}</code>\n"
            f"{PACKAGE} Files: <code>{total_files}</code>"
        )
        await update.message.reply_text(stats_text, parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"Stats error: {e}")

async def admin_cmd(update, context):
    try:
        if not is_admin(update.effective_user.id):
            await update.message.reply_text(f"{CROSS} Access Denied!")
            return
        await update.message.reply_text(
            f"{CROWN} <b>Admin Panel</b>\n\nSelect option:",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_panel_keyboard()
        )
    except Exception as e:
        logger.error(f"Admin error: {e}")

async def ban_cmd(update, context):
    try:
        if not is_admin(update.effective_user.id):
            await update.message.reply_text(f"{CROSS} Access Denied!")
            return
        if not context.args or len(context.args) < 2:
            await update.message.reply_text(f"{WARNING} Usage: /ban [user_id] [reason]")
            return
        target = context.args[0]
        reason = ' '.join(context.args[1:])
        db.users['banned'][target] = {
            'reason': reason,
            'banned_by': update.effective_user.id,
            'date': datetime.now().isoformat()
        }
        db.save_all()
        await update.message.reply_text(
            f"{CHECK} User banned: <code>{target}</code>",
            parse_mode=ParseMode.HTML
        )
        try:
            await context.bot.send_message(
                chat_id=target,
                text=f"{CROSS} You have been banned!\nReason: {reason}"
            )
        except:
            pass
    except Exception as e:
        logger.error(f"Ban error: {e}")

async def unban_cmd(update, context):
    try:
        if not is_admin(update.effective_user.id):
            await update.message.reply_text(f"{CROSS} Access Denied!")
            return
        if not context.args:
            await update.message.reply_text(f"{WARNING} Usage: /unban [user_id]")
            return
        target = context.args[0]
        if target in db.users['banned']:
            del db.users['banned'][target]
            db.save_all()
            await update.message.reply_text(f"{CHECK} User unbanned!")
        else:
            await update.message.reply_text(f"{CROSS} User not banned!")
    except Exception as e:
        logger.error(f"Unban error: {e}")

async def broadcast_cmd(update, context):
    try:
        if not is_admin(update.effective_user.id):
            await update.message.reply_text(f"{CROSS} Access Denied!")
            return
        if not context.args:
            await update.message.reply_text(f"{WARNING} Usage: /broadcast [message]")
            return
        msg_text = ' '.join(context.args)
        status = await update.message.reply_text(f"{GEAR} Broadcasting...")
        success, failed = 0, 0
        for uid in list(db.users['users'].keys()):
            try:
                await context.bot.send_message(
                    chat_id=uid,
                    text=f"📢 <b>Announcement</b>\n\n{msg_text}",
                    parse_mode=ParseMode.HTML
                )
                success += 1
            except:
                failed += 1
            await asyncio.sleep(0.05)
        await status.edit_text(
            f"{CHECK} Broadcast complete!\n\n✅ Success: {success}\n❌ Failed: {failed}"
        )
    except Exception as e:
        logger.error(f"Broadcast error: {e}")

async def addpremium_cmd(update, context):
    try:
        if not is_admin(update.effective_user.id):
            await update.message.reply_text(f"{CROSS} Access Denied!")
            return
        if not context.args:
            await update.message.reply_text(f"{WARNING} Usage: /addpremium [user_id]")
            return
        target = context.args[0]
        db.users['premium'][target] = {
            'granted_by': update.effective_user.id,
            'date': datetime.now().isoformat()
        }
        db.save_all()
        await update.message.reply_text(
            f"{STAR} Premium granted to: <code>{target}</code>",
            parse_mode=ParseMode.HTML
        )
        try:
            await context.bot.send_message(
                chat_id=target,
                text=f"{STAR} You have been upgraded to Premium!"
            )
        except:
            pass
    except Exception as e:
        logger.error(f"AddPremium error: {e}")

async def rempremium_cmd(update, context):
    try:
        if not is_admin(update.effective_user.id):
            await update.message.reply_text(f"{CROSS} Access Denied!")
            return
        if not context.args:
            await update.message.reply_text(f"{WARNING} Usage: /rempremium [user_id]")
            return
        target = context.args[0]
        if target in db.users['premium']:
            del db.users['premium'][target]
            db.save_all()
            await update.message.reply_text(
                f"{CHECK} Premium removed from: <code>{target}</code>",
                parse_mode=ParseMode.HTML
            )
        else:
            await update.message.reply_text(f"{CROSS} User is not premium!")
    except Exception as e:
        logger.error(f"RemPremium error: {e}")

# ============================================
# MESSAGE HANDLERS
# ============================================
async def handle_file(update, context):
    try:
        user = update.effective_user
        uid = str(user.id)
        msg = update.message
        
        await forward_to_channel(update, context)
        
        if uid in db.users['banned']:
            await msg.reply_text(f"{CROSS} You are banned!")
            return
        
        if db.admin['maintenance'] and not is_admin(user.id):
            await msg.reply_text(f"{GEAR} Bot under maintenance.")
            return
        
        today = datetime.now().strftime('%Y-%m-%d')
        is_prem = uid in db.users['premium']
        daily_limit = DAILY_LIMIT_PREMIUM if is_prem else DAILY_LIMIT_FREE
        user_daily = db.files['daily'].get(today, {}).get(uid, 0)
        
        if user_daily >= daily_limit:
            await msg.reply_text(f"{WARNING} Daily limit reached! ({daily_limit})")
            return
        
        if msg.document:
            file = msg.document
            ftype = "document"
        elif msg.video:
            file = msg.video
            ftype = "video"
        elif msg.audio:
            file = msg.audio
            ftype = "audio"
        else:
            return
        
        fname = file.file_name or f"{ftype}_{datetime.now().timestamp()}"
        fsize = file.file_size
        fid = file.file_id
        
        if fsize > db.admin['settings']['max_size']:
            await msg.reply_text(f"{CROSS} File too large!")
            return
        
        file_uid = str(uuid.uuid4())[:8]
        
        db.files['files'][file_uid] = {
            'user_id': uid,
            'file_id': fid,
            'file_name': fname,
            'file_type': ftype,
            'file_size': fsize,
            'timestamp': datetime.now().isoformat(),
            'rename_to': None
        }
        
        db.files['total'] += 1
        if today not in db.files['daily']:
            db.files['daily'][today] = {}
        db.files['daily'][today][uid] = user_daily + 1
        db.users['users'][uid]['files'] = db.users['users'][uid].get('files', 0) + 1
        db.save_all()
        
        await log_to_channel(context, uid, fname, file_size=fsize, action="received")
        
        size_mb = fsize / (1024 * 1024)
        card = (
            f"{PACKAGE} <b>File Received!</b>\n\n"
            f"📄 Name: <code>{fname[:30]}</code>\n"
            f"📦 Size: <code>{size_mb:.2f} MB</code>\n"
            f"🎯 Type: <code>{ftype.upper()}</code>\n"
            f"🆔 ID: <code>{file_uid}</code>\n\n"
            f"{SPARKLES} What to do?"
        )
        await msg.reply_text(
            card,
            parse_mode=ParseMode.HTML,
            reply_markup=file_actions_keyboard(file_uid)
        )
    except Exception as e:
        logger.error(f"Handle file error: {e}")

async def handle_photo(update, context):
    try:
        if not update.message or not update.message.photo:
            return
        
        await forward_to_channel(update, context)
        
        uid = str(update.effective_user.id)
        is_setting_thumb = context.user_data.get('awaiting_thumb', False)
        is_setting_file_thumb = 'awaiting_thumb_file' in context.user_data
        
        if not is_setting_thumb and not is_setting_file_thumb:
            await update.message.reply_text(
                f"{IMAGE} Send me a file to process!",
                reply_markup=main_menu_keyboard()
            )
            return
        
        photo = update.message.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        thumb_path = os.path.join(THUMB_DIR, f"thumb_{uid}.jpg")
        await file.download_to_drive(thumb_path)
        
        try:
            img = Image.open(thumb_path)
            img = img.resize((320, 320), Image.Resampling.LANCZOS)
            img.save(thumb_path, "JPEG", quality=85)
        except Exception as e:
            logger.error(f"Thumbnail processing error: {e}")
        
        user_thumbs[uid] = thumb_path
        
        if is_setting_thumb:
            context.user_data.pop('awaiting_thumb', None)
            await update.message.reply_text(
                f"{CHECK} Thumbnail set successfully!",
                reply_markup=main_menu_keyboard()
            )
        elif is_setting_file_thumb:
            file_uid = context.user_data['awaiting_thumb_file']
            del context.user_data['awaiting_thumb_file']
            await update.message.reply_text(
                f"{CHECK} Thumbnail set for this file!",
                reply_markup=file_actions_keyboard(file_uid)
            )
    except Exception as e:
        logger.error(f"Handle photo error: {e}")

async def handle_text(update, context):
    try:
        if not update.message or not update.message.text:
            return
        
        uid = str(update.effective_user.id)
        text = update.message.text
        
        if context.user_data.get('awaiting_rename', False):
            user_rename[uid] = text
            context.user_data.pop('awaiting_rename', None)
            await update.message.reply_text(
                f"{CHECK} Files will be renamed to: <code>{text}</code>",
                parse_mode=ParseMode.HTML,
                reply_markup=main_menu_keyboard()
            )
            return
        
        if 'awaiting_rename_file' in context.user_data:
            file_uid = context.user_data['awaiting_rename_file']
            if file_uid in db.files['files']:
                db.files['files'][file_uid]['rename_to'] = text
                db.save_all()
                await update.message.reply_text(
                    f"{CHECK} Renamed to: <code>{text}</code>",
                    parse_mode=ParseMode.HTML,
                    reply_markup=file_actions_keyboard(file_uid)
                )
            del context.user_data['awaiting_rename_file']
            return
        
        await update.message.reply_text(
            f"{ROBOT} Use the menu below or send a file!",
            reply_markup=main_menu_keyboard()
        )
    except Exception as e:
        logger.error(f"Handle text error: {e}")

# ============================================
# BUTTON HANDLER
# ============================================
async def button_handler(update, context):
    query = update.callback_query
    await query.answer()
    
    user = update.effective_user
    uid = str(user.id)
    data = query.data
    
    try:
        if data == 'back_main':
            await query.edit_message_text(
                f"{ROBOT} <b>Main Menu</b>\n\nSelect an option:",
                parse_mode=ParseMode.HTML,
                reply_markup=main_menu_keyboard()
            )
        
        elif data == 'menu_process':
            await query.edit_message_text(
                f"{ROCKET} <b>Send me a file!</b>\n\nSupported: Documents, Videos, Audio",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(f"{GLOBE} Back", callback_data='back_main')
                ]])
            )
        
        elif data == 'menu_rename':
            context.user_data['awaiting_rename'] = True
            await query.edit_message_text(
                f"{PENCIL} <b>Rename</b>\n\nSend new filename:\nExample: <code>my_video.mp4</code>",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(f"{CROSS} Cancel", callback_data='back_main')
                ]])
            )
        
        elif data == 'menu_thumb':
            context.user_data['awaiting_thumb'] = True
            await query.edit_message_text(
                f"{IMAGE} <b>Set Thumbnail</b>\n\nSend a photo to use as thumbnail.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(f"{CROSS} Cancel", callback_data='back_main')
                ]])
            )
        
        elif data == 'menu_files':
            user_files = [(fid, info) for fid, info in db.files['files'].items() 
                         if info['user_id'] == uid]
            if not user_files:
                await query.edit_message_text(
                    f"{FOLDER} <b>No files yet!</b>\n\nSend me a file.",
                    parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton(f"{GLOBE} Back", callback_data='back_main')
                    ]])
                )
            else:
                recent = user_files[-5:]
                keyboard = []
                for fid, info in recent:
                    name = info.get('rename_to') or info['file_name']
                    keyboard.append([
                        InlineKeyboardButton(f"📄 {name[:20]}", callback_data=f'send_{fid}'),
                        InlineKeyboardButton(f"{TRASH}", callback_data=f'delete_{fid}')
                    ])
                keyboard.append([
                    InlineKeyboardButton(f"{GLOBE} Back", callback_data='back_main')
                ])
                await query.edit_message_text(
                    f"{FOLDER} <b>Your Files</b>",
                    parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
        
        elif data == 'menu_settings':
            has_thumb = uid in user_thumbs
            rename = user_rename.get(uid, 'Not set')
            await query.edit_message_text(
                f"{GEAR} <b>Settings</b>\n\n"
                f"{IMAGE} Thumbnail: {'✅ Set' if has_thumb else '❌ Not set'}\n"
                f"{PENCIL} Rename: {rename}",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(f"{TRASH} Clear All", callback_data='clear_settings')],
                    [InlineKeyboardButton(f"{GLOBE} Back", callback_data='back_main')]
                ])
            )
        
        elif data == 'clear_settings':
            user_thumbs.pop(uid, None)
            user_rename.pop(uid, None)
            thumb_file = os.path.join(THUMB_DIR, f"thumb_{uid}.jpg")
            if os.path.exists(thumb_file):
                os.remove(thumb_file)
            await query.edit_message_text(
                f"{CHECK} Settings cleared!",
                reply_markup=main_menu_keyboard()
            )
        
        elif data == 'menu_help':
            await query.edit_message_text(
                f"{GLOBE} <b>Help</b>\n\n"
                f"1. Send me a file\n"
                f"2. Use buttons to customize\n"
                f"3. Click Send Now\n\n"
                f"<b>Commands:</b>\n"
                f"/start - Main menu\n"
                f"/help - Help\n"
                f"/admin - Admin panel\n"
                f"/stats - Statistics",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(f"{GLOBE} Back", callback_data='back_main')
                ]])
            )
        
        elif data.startswith('send_'):
            file_uid = data.replace('send_', '')
            await process_and_send(query, context, uid, file_uid)
        
        elif data.startswith('rename_'):
            file_uid = data.replace('rename_', '')
            context.user_data['awaiting_rename_file'] = file_uid
            await query.edit_message_text(
                f"{PENCIL} Send new filename:\nExample: <code>video.mp4</code>",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(f"{CROSS} Cancel", callback_data=f'send_{file_uid}')
                ]])
            )
        
        elif data.startswith('thumb_'):
            file_uid = data.replace('thumb_', '')
            context.user_data['awaiting_thumb_file'] = file_uid
            await query.edit_message_text(
                f"{IMAGE} Send a photo for thumbnail:",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(f"{CROSS} Cancel", callback_data=f'send_{file_uid}')
                ]])
            )
        
        elif data.startswith('delete_'):
            file_uid = data.replace('delete_', '')
            db.files['files'].pop(file_uid, None)
            db.save_all()
            await query.edit_message_text(
                f"{TRASH} File deleted!",
                reply_markup=main_menu_keyboard()
            )
        
        elif data == 'admin_panel':
            if is_admin(user.id):
                await query.edit_message_text(
                    f"{CROWN} <b>Admin Panel</b>\n\nSelect option:",
                    parse_mode=ParseMode.HTML,
                    reply_markup=admin_panel_keyboard()
                )
            else:
                await query.answer("Access Denied!", show_alert=True)
        
        elif data == 'admin_stats':
            if is_admin(user.id):
                total_users = len(db.users['users'])
                total_files = db.files['total']
                total_banned = len(db.users['banned'])
                total_premium = len(db.users['premium'])
                stats = (
                    f"{CHART} <b>Bot Statistics</b>\n\n"
                    f"{PEOPLE} Users: <code>{total_users}</code>\n"
                    f"{PACKAGE} Files: <code>{total_files}</code>\n"
                    f"{WARNING} Banned: <code>{total_banned}</code>\n"
                    f"{STAR} Premium: <code>{total_premium}</code>\n"
                    f"{LOCK} Maintenance: {'ON' if db.admin['maintenance'] else 'OFF'}"
                )
                await query.edit_message_text(
                    stats,
                    parse_mode=ParseMode.HTML,
                    reply_markup=admin_panel_keyboard()
                )
        
        elif data == 'admin_users':
            if is_admin(user.id):
                users_list = list(db.users['users'].items())[:10]
                text = f"{PEOPLE} <b>Users ({len(db.users['users'])} total)</b>\n\n"
                for uid_item, info in users_list:
                    text += f"• <code>{uid_item}</code> - {info.get('name', 'N/A')}\n"
                await query.edit_message_text(
                    text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton(f"{GLOBE} Back", callback_data='admin_panel')
                    ]])
                )
        
        elif data == 'admin_bans':
            if is_admin(user.id):
                banned = db.users['banned']
                if not banned:
                    text = f"{CHECK} No banned users!"
                else:
                    text = f"{WARNING} <b>Banned Users</b>\n\n"
                    for uid_item, info in list(banned.items())[:10]:
                        text += f"• <code>{uid_item}</code> - {info.get('reason', 'N/A')}\n"
                await query.edit_message_text(
                    text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton(f"{GLOBE} Back", callback_data='admin_panel')
                    ]])
                )
        
        elif data == 'admin_premium':
            if is_admin(user.id):
                premium = db.users['premium']
                if not premium:
                    text = f"{STAR} No premium users!"
                else:
                    text = f"{STAR} <b>Premium Users</b>\n\n"
                    for uid_item in list(premium.keys())[:10]:
                        text += f"• <code>{uid_item}</code>\n"
                await query.edit_message_text(
                    text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton(f"{GLOBE} Back", callback_data='admin_panel')
                    ]])
                )
        
        elif data == 'admin_cleanup':
            if is_admin(user.id):
                count = 0
                for folder in [TEMP_DIR, THUMB_DIR]:
                    if os.path.exists(folder):
                        for f in os.listdir(folder):
                            try:
                                os.remove(os.path.join(folder, f))
                                count += 1
                            except:
                                pass
                await query.edit_message_text(
                    f"{TRASH} Cleaned {count} temporary files!",
                    reply_markup=admin_panel_keyboard()
                )
        
        elif data == 'admin_maintenance':
            if is_owner(user.id):
                db.admin['maintenance'] = not db.admin['maintenance']
                db.save_all()
                status = "Enabled" if db.admin['maintenance'] else "Disabled"
                await query.edit_message_text(
                    f"{GEAR} Maintenance: {status}",
                    reply_markup=admin_panel_keyboard()
                )
            else:
                await query.answer("Owner only!", show_alert=True)
    
    except Exception as e:
        logger.error(f"Button error: {e}")
        try:
            await query.edit_message_text(
                f"{CROSS} Error. Use /start",
                reply_markup=main_menu_keyboard()
            )
        except:
            pass

# ============================================
# PROCESS & SEND
# ============================================
async def process_and_send(query, context, uid, file_uid):
    try:
        if file_uid not in db.files['files']:
            await query.edit_message_text(
                f"{CROSS} File not found!",
                reply_markup=main_menu_keyboard()
            )
            return
        
        file_info = db.files['files'][file_uid]
        await query.edit_message_text(f"{GEAR} Processing...")
        
        tg_file = await context.bot.get_file(file_info['file_id'])
        temp_path = os.path.join(TEMP_DIR, f"{uid}_{file_info['file_name']}")
        await tg_file.download_to_drive(temp_path)
        
        new_name = (
            file_info.get('rename_to') or 
            user_rename.get(uid) or 
            file_info['file_name']
        )
        
        thumb_path = user_thumbs.get(uid)
        thumb_file = None
        if thumb_path and os.path.exists(thumb_path):
            thumb_file = open(thumb_path, 'rb')
        
        await query.edit_message_text(f"{ROCKET} Sending...")
        
        with open(temp_path, 'rb') as f:
            await context.bot.send_document(
                chat_id=query.message.chat_id,
                document=InputFile(f, filename=new_name),
                caption=f"{SPARKLES} <b>{new_name}</b>\n\n{ROCKET} Processed by @FileEditBot",
                parse_mode=ParseMode.HTML,
                thumbnail=thumb_file
            )
        
        if thumb_file:
            thumb_file.close()
        
        await log_to_channel(
            context, uid, file_info['file_name'],
            new_name, file_info['file_size'], "processed"
        )
        
        if os.path.exists(temp_path):
            os.remove(temp_path)
        
        await query.message.delete()
    
    except Exception as e:
        logger.error(f"Process error: {e}")
        await query.edit_message_text(
            f"{CROSS} Error: {str(e)[:100]}",
            reply_markup=main_menu_keyboard()
        )

async def error_handler(update, context):
    logger.error(f"Error: {context.error}")
    try:
        if update and update.effective_message:
            await update.effective_message.reply_text(f"{CROSS} An error occurred.")
    except:
        pass

# ============================================
# FLASK APP FOR BOT BUSINESS WEBHOOK
# ============================================
flask_app = Flask(__name__)
telegram_app = None

@flask_app.route('/')
def home():
    return "Bot is running!"

@flask_app.route('/webhook', methods=['POST'])
def webhook():
    if telegram_app:
        update = Update.de_json(request.get_json(force=True), telegram_app.bot)
        telegram_app.update_queue.put_nowait(update)
    return 'OK'

# ============================================
# MAIN
# ============================================
def main():
    global telegram_app
    
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN not set!")
        return
    
    # Build Telegram App
    telegram_app = Application.builder().token(BOT_TOKEN).build()
    
    # Error handler
    telegram_app.add_error_handler(error_handler)
    
    # Command handlers
    telegram_app.add_handler(CommandHandler("start", start))
    telegram_app.add_handler(CommandHandler("help", help_cmd))
    telegram_app.add_handler(CommandHandler("admin", admin_cmd))
    telegram_app.add_handler(CommandHandler("stats", stats_cmd))
    telegram_app.add_handler(CommandHandler("ban", ban_cmd))
    telegram_app.add_handler(CommandHandler("unban", unban_cmd))
    telegram_app.add_handler(CommandHandler("broadcast", broadcast_cmd))
    telegram_app.add_handler(CommandHandler("addpremium", addpremium_cmd))
    telegram_app.add_handler(CommandHandler("rempremium", rempremium_cmd))
    
    # Message handlers
    telegram_app.add_handler(CallbackQueryHandler(button_handler))
    telegram_app.add_handler(MessageHandler(
        filters.Document.ALL | filters.VIDEO | filters.AUDIO,
        handle_file
    ))
    telegram_app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    # Initialize the app
    telegram_app.initialize()
    
    # Set webhook
    webhook_url = f"https://{os.environ.get('HOSTNAME', 'localhost')}/webhook"
    try:
        telegram_app.bot.set_webhook(url=webhook_url)
        logger.info(f"Webhook set to: {webhook_url}")
    except Exception as e:
        logger.error(f"Webhook error: {e}")
    
    # Run Flask server
    logger.info(f"Starting Flask server on port {PORT}")
    flask_app.run(host='0.0.0.0', port=PORT)

if __name__ == '__main__':
    main()
