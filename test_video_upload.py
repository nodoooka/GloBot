import asyncio
import os
import logging
from dotenv import load_dotenv

# 1. 强制加载 .env 文件，确保拿到 SESSDATA 和 BILI_JCT
load_dotenv()

# 2. 引入全局配置和刚刚写好的视频引擎
from common.config_loader import settings
from Bot_Publisher.bili_video_uploader import upload_video_bilibili

# 初始化独立日志
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("GloBot_VideoTest")

async def run_test():
    # ==========================================
    # 🧪 测试配置区
    # ==========================================
    # ⚠️ 请将此处的路径替换为你本地真实存在的测试 MP4 文件路径
    # 根据你之前的报错日志，你本地好像有一个叫 test_dummy.mp4 的文件？
    test_video_path = "/Users/tgmesmer/GloBot/GloBot_Data/iLiFE/ready_to_publish/final_test_pipeline_dummy.mp4" 

    if not os.path.exists(test_video_path):
        logger.error(f"❌ 找不到测试视频文件: {test_video_path}，请先准备一个测试用的小体积 MP4。")
        return

    # 🚨 绝对安全防御：代码级锁定“仅自己可见”，无视 config.yaml 里的配置
    settings.publishers.bilibili.visibility = 1
    settings.publishers.bilibili.video_tid = 171
    settings.publishers.bilibili.video_copyright = 2

    # 📝 构造极其逼真的测试稿件元数据
    test_title = "【GloBot 引擎测试】并发分片上传验证"
    test_content = (
        "这是一条由 GloBot 视频引擎发送的沙盒测试动态。\n\n"
        "引擎状态：异步多线程 UPOS 极速传输已激活。\n"
        "【原文】\n動画のアップロードテストです！\n\n#GloBot测试#"
    )
    test_source_url = "https://x.com/iLiFE_official/status/1234567890123456789"

    logger.info("=" * 50)
    logger.info("🚀 启动视频投稿独立沙盒测试...")
    logger.info(f"🔒 当前安全级别: 仅自己可见 (visibility={settings.publishers.bilibili.visibility})")
    logger.info(f"📁 目标视频: {test_video_path}")
    logger.info("=" * 50)

    try:
        # ==========================================
        # 📞 呼叫核心上传引擎
        # ==========================================
        success, bvid = await upload_video_bilibili(
            video_path=test_video_path,
            dynamic_title=test_title,
            dynamic_content=test_content,
            source_url=test_source_url,
            settings=settings
        )

        if success and bvid:
            logger.info("=" * 50)
            logger.info(f"✅ 沙盒测试圆满成功！")
            logger.info(f"🎉 成功获取到视频稿件 BVID: {bvid}")
            logger.info(f"👉 请立即前往 B 站创作中心 (稿件管理) 查看是否处于【仅自己可见】状态。")
        else:
            logger.error("❌ 测试失败，请仔细检查上方抛出的 HTTP 错误或参数提示。")

    except Exception as e:
        logger.error(f"💥 发生未捕获的致命异常: {e}")

if __name__ == "__main__":
    # 启动异步事件循环跑测试
    asyncio.run(run_test())