import asyncio
import os
import logging
from dotenv import load_dotenv

load_dotenv()

from common.config_loader import settings
from Bot_Publisher.bili_video_uploader import upload_video_bilibili

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("GloBot_VideoTest")

async def run_test():
    # ==========================================
    # 🧪 测试配置区
    # ==========================================
    test_video_path = "/Users/tgmesmer/GloBot/GloBot_Data/iLiFE/ready_to_publish/test2292.mp4" 

    if not os.path.exists(test_video_path):
        logger.error(f"❌ 找不到测试视频文件: {test_video_path}")
        return

    # 🚨 绝对安全防御：代码级锁定“仅自己可见”
    settings.publishers.bilibili.visibility = 1
    settings.publishers.bilibili.video_tid = 171
    settings.publishers.bilibili.video_copyright = 2

    # 📝 这是由于开启了 bypass_tg，代码将强行使用的本地硬编码参数
    test_title = "【中字】aisu第一次的电视剧体验"
    test_content = (
        "测试动态。\n\n"
        "✨ 引擎直飞 B 站。\n"
        "【原文】\n動画のアップロードテストです！\n\n#GloBot测试#"
    )
    test_source_url = "https://x.com/iLiFE_official/status/1234567890"

    logger.info("=" * 50)
    logger.info("🚀 启动 [纯本地凭证直连 + B站无感上传] 脱机沙盒测试...")
    logger.info(f"🔒 当前安全级别: 仅自己可见 (visibility={settings.publishers.bilibili.visibility})")
    logger.info("=" * 50)

    try:
        logger.info("⚡ 测试模式激活：跳过 Telegram 唤醒，直接下发本地参数...")

        # 🚨 核心改动：传入 bypass_tg=True
        success, bvid = await upload_video_bilibili(
            video_path=test_video_path,
            dynamic_title=test_title,
            dynamic_content=test_content,
            source_url=test_source_url,
            settings=settings,
            bypass_tg=True 
        )

        if success and bvid:
            logger.info("=" * 50)
            logger.info(f"✅ 脱机测试圆满成功！防风控系统与扫码凭证完全有效。")
            logger.info(f"🎉 成功获取到视频稿件 BVID: {bvid}")
            logger.info(f"👉 请前往 B 站创作中心 (稿件管理) 查看。")
        else:
            logger.error("❌ 测试遭遇失败。请根据上方日志排查。")

    except Exception as e:
        logger.error(f"🔥 测试发生致命异常: {e}")

if __name__ == "__main__":
    try:
        asyncio.run(run_test())
    except KeyboardInterrupt:
        logger.info("\n🛑 手动中断测试。")