import os
import sys
import logging
from pathlib import Path

# 将项目根目录加入系统路径，以便导入 common 模块
sys.path.append(str(Path(__file__).resolve().parent.parent))

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from common.config_loader import settings, TG_BOT_TOKEN, TG_CHAT_ID
import redis

# ==========================================
# 1. 基础配置：日志与 Redis 连接
# ==========================================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# 连接到 Redis 总线 (如果本地还没装 Redis，它会捕获异常但不会让 Bot 崩溃)
try:
    # 暂时指向 localhost，后续用 Docker 跑起来后改成 redis-bus
    redis_client = redis.Redis(host='localhost', port=6379, db=0, decode_responses=True)
    redis_client.ping()
    logger.info("✅ 成功连接到 Redis 消息总线。")
except redis.ConnectionError:
    logger.warning("⚠️ 无法连接到 Redis。请确保 Redis 服务已启动。状态切换功能将暂时失效。")
    redis_client = None

# ==========================================
# 2. 权限校验拦截器 (只响应老板的指令)
# ==========================================
def auth_required(func):
    """装饰器：拦截非老板 (TG_CHAT_ID) 的消息"""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if str(update.effective_chat.id) != str(TG_CHAT_ID):
            logger.warning(f"🚨 陌生人试图访问: {update.effective_user.username} (ID: {update.effective_chat.id})")
            return
        return await func(update, context)
    return wrapper

# ==========================================
# 3. 核心指令处理逻辑 (已换用更稳定的 HTML 解析)
# ==========================================
@auth_required
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /start 指令"""
    welcome_text = (
        f"🤖 <b>{settings.app.name} v{settings.app.version}</b> 已上线！\n\n"
        f"老板你好，我是你的 Master_OC 中枢管家。目前监控组 <b>{settings.targets.group_name}</b> 状态正常。\n\n"
        f"🛠️ <b>可用指令</b>：\n"
        f"/status - 查看系统当前状态\n"
        f"/pause - 紧急挂起所有爬虫与发布任务\n"
        f"/resume - 恢复运行"
    )
    await update.message.reply_text(welcome_text, parse_mode='HTML')

@auth_required
async def pause_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /pause 指令 (软开关)"""
    if redis_client:
        redis_client.set("system_status", "PAUSED")
        await update.message.reply_text("🛑 <b>已下发挂起指令</b>！\n爬虫和发布节点完成当前手头任务后将进入待机状态。", parse_mode='HTML')
    else:
        await update.message.reply_text("⚠️ Redis 未连接，无法下发状态。")

@auth_required
async def resume_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /resume 指令"""
    if redis_client:
        redis_client.set("system_status", "RUNNING")
        await update.message.reply_text("🟢 <b>已下发恢复指令</b>！\n矩阵节点重新开始接单。", parse_mode='HTML')
    else:
        await update.message.reply_text("⚠️ Redis 未连接，无法下发状态。")

@auth_required
async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /status 指令"""
    status = redis_client.get("system_status") if redis_client else "未知 (Redis 离线)"
    if status is None:
        status = "RUNNING (默认)"
        
    status_text = (
        f"📊 <b>系统状态报告</b>\n"
        f"-------------------\n"
        f"🚦 业务状态: <code>{status}</code>\n"
        f"🎯 监控账号数: {len(settings.targets.x_accounts)} 个\n"
        f"🔥 压制质量阈值: {settings.media_engine.hardware_encode_quality}\n"
    )
    await update.message.reply_text(status_text, parse_mode='HTML')
    
# ==========================================
# 4. 启动 Bot 引擎
# ==========================================
def main():
    if not TG_BOT_TOKEN:
        logger.error("❌ 未在 .env 中找到 TG_BOT_TOKEN，程序退出。")
        sys.exit(1)

    # 构建并运行 Application
    application = Application.builder().token(TG_BOT_TOKEN).build()

    # 注册指令路由
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("pause", pause_command))
    application.add_handler(CommandHandler("resume", resume_command))
    application.add_handler(CommandHandler("status", status_command))

    logger.info("🚀 Master_OC Telegram 管家正在启动，开始长轮询...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()