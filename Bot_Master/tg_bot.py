import os
import sys
import logging
import asyncio
import sqlite3
import json
from pathlib import Path
from datetime import datetime, time, timezone, timedelta
from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler, CallbackQueryHandler
from telegram.error import NetworkError

sys.path.append(str(Path(__file__).resolve().parent.parent))
from common.config_loader import settings

logger = logging.getLogger("GloBot_Telegram")
# 强制屏蔽 httpx 的底层心跳请求日志，只显示 WARNING 及以上的报错
logging.getLogger("httpx").setLevel(logging.WARNING)

TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
TG_CHAT_ID = os.getenv("TG_CHAT_ID")

# ==========================================
# 🚦 全局状态与异步桥梁
# ==========================================
class GloBotState:
    # 控制 main_loop 是否运行的阀门
    is_running = asyncio.Event() 
    
    # 视频发布的人工确认通道 (Future 对象)
    pending_video_approval = None 
    
    # 统计数据，用于每日简报
    daily_stats = {"success": 0, "failed": 0, "videos": 0}
    
    # 架构接管的核心变量
    main_loop_coro = None    
    crawler_task = None      
    
    # 🌟 新增：睡眠状态机与打断事件
    is_sleeping = False
    wake_up_event = asyncio.Event()

GloBotState.is_running.set()  # 默认允许运行
tg_app = None  # 全局 Telegram Application 实例

# ==========================================
# 📡 1. 主动推送接口 (供外部模块调用)
# ==========================================
async def send_tg_msg(text: str, reply_markup=None):
    """向主理人发送消息，自动处理网络异常"""
    if not TG_BOT_TOKEN or not TG_CHAT_ID or not tg_app: return
    try:
        await tg_app.bot.send_message(chat_id=TG_CHAT_ID, text=text, parse_mode='HTML', reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"❌ Telegram 推送失败: {e}")

async def send_tg_error(error_msg: str):
    """发送最高级别的红警报错"""
    text = f"🚨 <b>GloBot 核心总线异常拦截</b>\n<pre>{error_msg}</pre>"
    await send_tg_msg(text)

# ==========================================
# 🛑 2. 基础指令控制：启停与状态
# ==========================================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 欢迎连接 GloBot Matrix 控制台！\n使用 /help 查看可用指令。")

async def cmd_boot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if GloBotState.crawler_task and not GloBotState.crawler_task.done():
        await update.message.reply_text("⚠️ 爬虫引擎已经在运行中了！")
        return
    if GloBotState.main_loop_coro:
        GloBotState.crawler_task = asyncio.create_task(GloBotState.main_loop_coro())
        GloBotState.is_running.set() 
        await update.message.reply_text("🚀 <b>引擎已远程点火！</b>\n全自动流水线进程已启动。", parse_mode='HTML')
    else:
        await update.message.reply_text("❌ 找不到引擎入口，无法启动。")

async def cmd_kill(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if GloBotState.crawler_task and not GloBotState.crawler_task.done():
        GloBotState.crawler_task.cancel()
        await update.message.reply_text("🛑 <b>引擎已被强制拔除电源！</b>\n爬虫进程已彻底终止，直至您再次使用 /boot 唤醒。", parse_mode='HTML')
    else:
        await update.message.reply_text("⚠️ 引擎当前并未运行。")

async def cmd_pause(update: Update, context: ContextTypes.DEFAULT_TYPE):
    GloBotState.is_running.clear() # 关阀门
    await update.message.reply_text("⏸️ <b>已下达停机指令。</b>\n总线将在完成当前任务后进入挂起状态，停止发稿。", parse_mode='HTML')

async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    GloBotState.is_running.set() # 开阀门
    await update.message.reply_text("▶️ <b>已下达恢复指令。</b>\n总线封锁已解除，流水线重新启动！", parse_mode='HTML')

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    task_status = "🟢 正在运转" if (GloBotState.crawler_task and not GloBotState.crawler_task.done()) else "🔴 已被熄火"
    valve_status = "🟢 畅通" if GloBotState.is_running.is_set() else "🔴 截断"
    sleep_status = "💤 休眠中" if GloBotState.is_sleeping else "🔥 抓取/发布中"
    
    text = f"📊 <b>GloBot 实时状态</b>\n" \
           f"引擎进程: {task_status} (/boot /kill)\n" \
           f"发布阀门: {valve_status} (/pause /resume)\n" \
           f"当前工况: {sleep_status}\n" \
           f"今日成功发射: {GloBotState.daily_stats['success']} 条\n" \
           f"今日发射失败: {GloBotState.daily_stats['failed']} 条\n" \
           f"当前目标集群: {settings.targets.group_name}"
    await update.message.reply_text(text, parse_mode='HTML')

# ==========================================
# 🔗 3. 指令化强制爆破与唤醒机
# ==========================================
async def handle_memory_wipe(tweet_id: str) -> tuple[bool, str]:
    try:
        # 1. 深入 SQLite 数据库抹除记忆
        db_path = Path(os.getenv("LOCAL_DATA_DIR", f"./GloBot_Data/{settings.targets.group_name}")) / "processed_tweets.db"
        if db_path.exists():
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute("DELETE FROM tweets WHERE tweet_id = ?", (tweet_id,))
            conn.commit()
            conn.close()
            
        # 2. 深入 JSON 历史记录抹除记忆
        history_file = Path(os.getenv("LOCAL_DATA_DIR", f"./GloBot_Data/{settings.targets.group_name}")) / "history.json"
        if history_file.exists():
            with open(history_file, "r", encoding="utf-8") as f:
                history = set(json.load(f))
            if tweet_id in history:
                history.remove(tweet_id)
                with open(history_file, "w", encoding="utf-8") as f:
                    json.dump(list(history), f, ensure_ascii=False, indent=2)
        return True, ""
    except Exception as e:
        return False, str(e)

async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❌ 用法: /reset <推文ID>")
        return
    tweet_id = context.args[0]
    await update.message.reply_text(f"🔍 收到静默爆破指令，正在重置推文 [{tweet_id}] 的拦截记录...")
    success, err = await handle_memory_wipe(tweet_id)
    if success:
        await update.message.reply_text(f"🎯 <b>指令已下达！</b>\n推文 <code>{tweet_id}</code> 的防重复记忆已被彻底抹除。\n总线将在下一次自然巡视时处理。", parse_mode='HTML')
    else:
        await update.message.reply_text(f"❌ 抹除记忆失败: {err}")

async def cmd_force(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❌ 用法: /force <推文ID>")
        return
    tweet_id = context.args[0]
    
    if not GloBotState.is_sleeping:
        await update.message.reply_text("⚠️ 引擎当前正在高速运转处理任务，强制唤醒指令不生效。\n请等待其进入休眠状态后再试，或使用 /reset 仅抹除记忆。")
        return

    await update.message.reply_text(f"🔍 收到强制唤醒指令，正在重置推文 [{tweet_id}] ...")
    success, err = await handle_memory_wipe(tweet_id)
    if success:
        GloBotState.wake_up_event.set() # 🔥 核心：发送异步事件，彻底打断睡眠进程！
        await update.message.reply_text("⚡ <b>强制唤醒已触发！</b>\n流水线休眠被打断，正在火速启动新一轮抓取！", parse_mode='HTML')
    else:
        await update.message.reply_text(f"❌ 抹除记忆失败，唤醒中止: {err}")

# ==========================================
# 🎥 4. 视频发布人工介入 (一键面板升级版，含5图预览)
# ==========================================
WAIT_TITLE, WAIT_PRESET, WAIT_CONFIRM = range(3)

async def extract_video_frames(video_path: str, num_frames=5) -> list[str]:
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", video_path]
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    stdout, _ = await proc.communicate()
    try: duration = float(stdout.decode().strip())
    except: duration = 10.0
        
    timestamps = [duration * (i/(num_frames+1)) for i in range(1, num_frames+1)]
    output_files = []
    
    for i, ts in enumerate(timestamps):
        out_path = f"{video_path}_preview_{i}.jpg"
        cmd2 = ["ffmpeg", "-y", "-ss", str(ts), "-i", video_path, "-vframes", "1", "-q:v", "2", out_path]
        p2 = await asyncio.create_subprocess_exec(*cmd2, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await p2.communicate()
        if os.path.exists(out_path): output_files.append(out_path)
            
    return output_files

async def ask_video_approval(video_path: str, default_desc: str) -> dict:
    if not tg_app: return None
    
    frames = await extract_video_frames(video_path, 5)
    
    msg = (f"🎬 <b>【视频发布拦截】</b>有新视频等待定稿！\n\n"
           f"<b>📝 完整动态文案:</b>\n"
           f"<code>{default_desc}</code>\n\n"
           f"📁 视频实体: <code>{Path(video_path).name}</code>\n"
           f"👇 <i>为您抽取了 5 张视频画面供预览参考：</i>")
    await send_tg_msg(msg)
    
    if frames:
        media = [InputMediaPhoto(open(f, 'rb')) for f in frames if os.path.exists(f)]
        if media:
            try: await tg_app.bot.send_media_group(chat_id=TG_CHAT_ID, media=media)
            except Exception as e: logger.error(f"发送图集失败: {e}")
            finally:
                for f in frames:
                    try: os.remove(f)
                    except: pass
                    
    await send_tg_msg("👉 <b>请在对话框直接回复该视频的【B站标题】:</b>")
    
    GloBotState.pending_video_approval = asyncio.Future()
    result = await GloBotState.pending_video_approval
    GloBotState.pending_video_approval = None
    return result

async def video_hitl_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not GloBotState.pending_video_approval: return ConversationHandler.END
    context.user_data['video_title'] = update.message.text
    
    keyboard = []
    if hasattr(settings.publishers.bilibili, 'video_presets'):
        for idx, preset in enumerate(settings.publishers.bilibili.video_presets):
            keyboard.append([InlineKeyboardButton(preset.name, callback_data=f"preset_{idx}")])
        
    reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
    await update.message.reply_text("✅ 标题已确认。\n👉 <b>请点击下方按钮选择【投稿分区与标签】预设：</b>", reply_markup=reply_markup, parse_mode='HTML')
    return WAIT_PRESET

async def video_hitl_preset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    idx = int(query.data.split('_')[1])
    preset = settings.publishers.bilibili.video_presets[idx]
    
    context.user_data['video_tid'] = preset.tid
    context.user_data['video_tags'] = preset.tags
    
    summary = (
        f"📝 <b>【发车前最终确认】</b>\n"
        f"标题: {context.user_data['video_title']}\n"
        f"分区: {preset.tid} ({preset.name})\n"
        f"标签: {preset.tags}\n\n"
        f"👉 确认无误请点击下达发射指令："
    )
    keyboard = [
        [InlineKeyboardButton("🚀 确认发射！", callback_data="confirm_yes")],
        [InlineKeyboardButton("🔄 重新写标题", callback_data="confirm_no"), InlineKeyboardButton("🚫 取消发布", callback_data="confirm_cancel")]
    ]
    await query.edit_message_text(text=summary, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
    return WAIT_CONFIRM

async def video_hitl_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ans = query.data
    
    if ans == "confirm_yes":
        await query.edit_message_text("🚀 授权成功！总线已解除挂起，正在执行 B 站视频高速推流...")
        GloBotState.pending_video_approval.set_result(context.user_data.copy())
        context.user_data.clear()
        return ConversationHandler.END
    elif ans == "confirm_no":
        await query.edit_message_text("🔄 已重置。请直接在对话框中重新回复【B站标题】:")
        return WAIT_TITLE
    elif ans == "confirm_cancel":
        if GloBotState.pending_video_approval:
            GloBotState.pending_video_approval.set_result({})
        await query.edit_message_text("🚫 操作已终止，视频将保留在本地被跳过。")
        return ConversationHandler.END

async def video_hitl_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if GloBotState.pending_video_approval:
        GloBotState.pending_video_approval.set_result({}) 
    await update.message.reply_text("🚫 已强行取消本次发布任务。")
    return ConversationHandler.END

# ==========================================
# 📊 5. 每日简报任务 (严格锁定东京时间 22:00)
# ==========================================
# 定义东京时间 (UTC+9)
JST = timezone(timedelta(hours=9))

async def daily_report(context: ContextTypes.DEFAULT_TYPE):
    # 获取当前东京时间
    now_jst = datetime.now(JST)
    
    report = (
        f"🌙 <b>GloBot 每日夜间简报</b>\n"
        f"周期: 昨夜 22:00 - 今夜 22:00\n"
        f"日期: {now_jst.strftime('%Y-%m-%d')}\n"
        f"------------------------\n"
        f"✅ 成功搬运: {GloBotState.daily_stats['success']} 条\n"
        f"❌ 失败/拦截: {GloBotState.daily_stats['failed']} 条\n"
        f"🎬 发布视频: {GloBotState.daily_stats['videos']} 个\n\n"
        f"状态: 数据已清零归档，夜间自动值守已就绪！"
    )
    await send_tg_msg(report)
    
    # 播报完后立刻重置统计数据，迎接下一个 24 小时周期
    GloBotState.daily_stats = {"success": 0, "failed": 0, "videos": 0}

# ==========================================
# 🔇 全局静音异常拦截器 (防 TG 断网刷屏)
# ==========================================
async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    if isinstance(context.error, NetworkError):
        # 遇到网络闪断，只打印一行黄色警告，不再抛出几百行的红色报错
        logger.warning(f"📡 [TG中枢] 网络连接波动，正在自动重连: {context.error}")
    else:
        logger.error("🔥 [TG中枢] 发生未捕获异常:", exc_info=context.error)

# ==========================================
# 🧠 启动器
# ==========================================
async def start_telegram_bot():
    global tg_app
    if not TG_BOT_TOKEN:
        logger.warning("⚠️ 未配置 TG_BOT_TOKEN，Telegram 遥控器未激活。")
        return

    tg_app = ApplicationBuilder().token(TG_BOT_TOKEN).build()

    # 注册指令
    tg_app.add_handler(CommandHandler("start", cmd_start))
    tg_app.add_handler(CommandHandler("help", cmd_start))
    tg_app.add_handler(CommandHandler("boot", cmd_boot))
    tg_app.add_handler(CommandHandler("kill", cmd_kill))
    tg_app.add_handler(CommandHandler("pause", cmd_pause))
    tg_app.add_handler(CommandHandler("resume", cmd_resume))
    tg_app.add_handler(CommandHandler("status", cmd_status))
    
    # 🌟 注册重置与强制唤醒指令
    tg_app.add_handler(CommandHandler("reset", cmd_reset))
    tg_app.add_handler(CommandHandler("force", cmd_force))
    
    # 注册视频 HITL 审批对话机
    conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.TEXT & (~filters.COMMAND), video_hitl_title)],
        states={
            WAIT_TITLE: [MessageHandler(filters.TEXT & (~filters.COMMAND), video_hitl_title)],
            WAIT_PRESET: [CallbackQueryHandler(video_hitl_preset, pattern="^preset_")],
            WAIT_CONFIRM: [CallbackQueryHandler(video_hitl_confirm, pattern="^confirm_")],
        },
        fallbacks=[CommandHandler('cancel', video_hitl_cancel)]
    )
    tg_app.add_handler(conv_handler)

    # 👇 挂载静音拦截器
    tg_app.add_error_handler(global_error_handler)

    # 注册每日定时任务
    report_time = time(hour=22, minute=0, second=0, tzinfo=JST)
    tg_app.job_queue.run_daily(daily_report, time=report_time)

    logger.info("📡 Telegram 控制中枢已上线，正在监听指令...")
    await tg_app.initialize()
    await tg_app.start()
    await tg_app.updater.start_polling()