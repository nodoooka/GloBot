import os
import sys
import logging
import asyncio
import re
import sqlite3
import json
from pathlib import Path
from datetime import datetime, time, timezone, timedelta
from telegram import Update, Bot
from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup # 👈 增加了按钮库
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler, CallbackQueryHandler # 👈 增加了 Callback 处理

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

GloBotState.is_running.set()  # 默认允许运行
tg_app = None  # 全局 Telegram Application 实例

# ==========================================
# 📡 1. 主动推送接口 (供外部模块调用)
# ==========================================
async def send_tg_msg(text: str):
    """向主理人发送消息，自动处理网络异常"""
    if not TG_BOT_TOKEN or not TG_CHAT_ID or not tg_app:
        return
    try:
        await tg_app.bot.send_message(chat_id=TG_CHAT_ID, text=text, parse_mode='HTML')
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

async def cmd_pause(update: Update, context: ContextTypes.DEFAULT_TYPE):
    GloBotState.is_running.clear() # 关阀门
    await update.message.reply_text("⏸️ <b>已下达停机指令。</b>\n总线将在完成当前任务后进入挂起状态，停止嗅探新动态。", parse_mode='HTML')

async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    GloBotState.is_running.set() # 开阀门
    await update.message.reply_text("▶️ <b>已下达恢复指令。</b>\n总线封锁已解除，流水线重新启动！", parse_mode='HTML')

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = "🟢 运行中" if GloBotState.is_running.is_set() else "🔴 已挂起"
    text = f"📊 <b>GloBot 实时状态</b>\n" \
           f"当前引擎状态: {state}\n" \
           f"今日成功发射: {GloBotState.daily_stats['success']} 条\n" \
           f"今日发射失败: {GloBotState.daily_stats['failed']} 条\n" \
           f"当前目标集群: {settings.targets.group_name}"
    await update.message.reply_text(text, parse_mode='HTML')

# ==========================================
# 🎥 3. 视频发布人工介入 (一键面板升级版)
# ==========================================
WAIT_TITLE, WAIT_PRESET, WAIT_CONFIRM = range(3) # 状态机简化为 3 步

async def ask_video_approval(video_path: str, default_desc: str) -> dict:
    if not tg_app: return None
    msg = (f"🎬 <b>【视频发布拦截】</b>有新视频等待定稿！\n"
           f"📁 <code>{Path(video_path).name}</code>\n\n"
           f"👉 <b>请在对话框直接回复该视频的【B站标题】:</b>")
    await send_tg_msg(msg)
    GloBotState.pending_video_approval = asyncio.Future()
    result = await GloBotState.pending_video_approval
    GloBotState.pending_video_approval = None
    return result

async def video_hitl_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not GloBotState.pending_video_approval: return ConversationHandler.END
    context.user_data['video_title'] = update.message.text
    
    # 🌟 从配置中动态生成按钮键盘！
    keyboard = []
    for idx, preset in enumerate(settings.publishers.bilibili.video_presets):
        keyboard.append([InlineKeyboardButton(preset.name, callback_data=f"preset_{idx}")])
        
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("✅ 标题已确认。\n👉 <b>请点击下方按钮选择【投稿分区与标签】预设：</b>", reply_markup=reply_markup, parse_mode='HTML')
    return WAIT_PRESET

async def video_hitl_preset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    # 提取用户点击的选项
    idx = int(query.data.split('_')[1])
    preset = settings.publishers.bilibili.video_presets[idx]
    
    context.user_data['video_tid'] = preset.tid
    context.user_data['video_tags'] = preset.tags
    
    # 渲染最终确认面板
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
# 🔗 4. 强制指定推特链接发推 (单点爆破)
# ==========================================
async def handle_twitter_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text
    match = re.search(r'status/(\d+)', url)
    if not match:
        await update.message.reply_text("❌ 未能识别出推文 ID，请发送完整的 X.com 推文链接。")
        return
        
    tweet_id = match.group(1)
    await update.message.reply_text(f"🔍 收到强制爆破指令，正在重置推文 [{tweet_id}] 的拦截记录...")
    
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
                    
        success_msg = (
            f"🎯 <b>指令已下达！</b>\n"
            f"推文 <code>{tweet_id}</code> 的防重复记忆已被彻底抹除。\n\n"
            f"💡 只要它还存在于推特首页的时间流中，总线将在下一次巡视（几分钟内）自动将其捕获并重新触发发布流水线！"
        )
        await update.message.reply_text(success_msg, parse_mode='HTML')
        
    except Exception as e:
        await update.message.reply_text(f"❌ 抹除记忆失败: {e}")

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
    tg_app.add_handler(CommandHandler("pause", cmd_pause))
    tg_app.add_handler(CommandHandler("resume", cmd_resume))
    tg_app.add_handler(CommandHandler("status", cmd_status))
    
    # 注册视频 HITL 审批对话机 (一键面板升级版)
    conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.TEXT & (~filters.COMMAND) & filters.Regex(r'^(?!http).*$'), video_hitl_title)],
        states={
            WAIT_TITLE: [MessageHandler(filters.TEXT & (~filters.COMMAND), video_hitl_title)],
            WAIT_PRESET: [CallbackQueryHandler(video_hitl_preset, pattern="^preset_")],
            WAIT_CONFIRM: [CallbackQueryHandler(video_hitl_confirm, pattern="^confirm_")],
        },
        fallbacks=[CommandHandler('cancel', video_hitl_cancel)]
    )
    tg_app.add_handler(conv_handler)
    
    # 注册推特链接解析
    tg_app.add_handler(MessageHandler(filters.Regex(r'x\.com|twitter\.com'), handle_twitter_link))

   # 注册每日定时任务：严格指定在东京时间的 22:00:00 触发
    report_time = time(hour=22, minute=0, second=0, tzinfo=JST)
    tg_app.job_queue.run_daily(daily_report, time=report_time)

    logger.info("📡 Telegram 控制中枢已上线，正在监听指令...")
    await tg_app.initialize()
    await tg_app.start()
    await tg_app.updater.start_polling()
    
    await send_tg_msg("🟢 <b>GloBot Matrix 已上线</b>\n总线连接正常，准备接受调度。")