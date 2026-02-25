import asyncio
import json
import os
import sys
import logging
import random
from datetime import datetime
from pathlib import Path

# 将项目根目录加入系统路径
sys.path.append(str(Path(__file__).resolve().parent.parent))

from playwright.async_api import async_playwright, Response
from common.config_loader import settings

# ==========================================
# 1. 基础配置
# ==========================================
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

AUTH_FILE = Path(__file__).resolve().parent.parent / "auth_store" / "twitter_auth.json"

# 🌟 GloBot 动态路径：根据配置表的 group_name 自动建立对应团体的文件夹！
DATA_DIR = Path(os.getenv("LOCAL_DATA_DIR", f"./GloBot_Data/{settings.targets.group_name}")) / "timeline_raw"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# ==========================================
# 2. 核心拦截器：【严格拉黑“为你推荐”，只截获“正在关注”】
# ==========================================
async def handle_response(response: Response):
    """精准拦截 HomeLatestTimeline (即'正在关注'的纯净时间线)"""
    if "graphql" in response.url and "HomeLatestTimeline" in response.url:
        try:
            json_data = await response.json()
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            save_path = DATA_DIR / f"timeline_following_{timestamp}.json"
            
            with open(save_path, "w", encoding="utf-8") as f:
                json.dump(json_data, f, ensure_ascii=False, indent=2)
                
            logger.info(f"🎯 成功截获纯净版【正在关注】信息流！已保存至 {save_path.name}")
        except Exception as e:
            logger.error(f"解析时间线 GraphQL 失败: {e}")

# ==========================================
# 3. 单次嗅探任务 (模拟真人点击切换 Tab)
# ==========================================
async def fetch_timeline():
    logger.info("🚀 开始潜入 X 主页提取最新动态...")
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,  
            args=[
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--no-sandbox",
                "--blink-settings=imagesEnabled=false", 
                "--js-flags='--max-old-space-size=512'"
            ]
        )
        
        context = await browser.new_context(
            storage_state=AUTH_FILE,
            viewport={'width': 1280, 'height': 800},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        
        page = await context.new_page()
        page.on("response", handle_response)
        
        try:
            await page.goto("https://x.com/home", timeout=60000)
            
            logger.info("🖱️ 正在强制切换到【正在关注】(Following) 页面...")
            await page.wait_for_selector('[role="tab"]', timeout=15000)
            tabs = page.locator('[role="tab"]')
            
            if await tabs.count() >= 2:
                await tabs.nth(1).click()
            else:
                logger.warning("⚠️ 标签页获取异常，推特UI可能发生变化。")
            
            logger.info("⏳ 正在监听底层纯净数据包...")
            await page.wait_for_timeout(8000)
            
            await page.mouse.wheel(0, 1500)
            await page.wait_for_timeout(3000)
            
        except Exception as e:
            logger.error(f"❌ 抓取过程发生异常: {e}")
        finally:
            logger.info("🧹 销毁本次浏览器环境，释放物理内存。")
            await context.close()
            await browser.close()

# ==========================================
# 4. 永动机：带人性化抖动 + 自动呼叫提纯下载 + 阅后即焚
# ==========================================
async def crawler_loop():
    if not AUTH_FILE.exists():
        logger.error("❌ 找不到免密通行证 auth.json！请先运行 login_auth.py")
        return

    logger.info(f"🟢 GloBot 监听矩阵已启动，目标集群: {settings.targets.group_name} ...")
    while True:
        await fetch_timeline()
        
        # 🌟 修改了这里的包名引用，完美适配 Bot_Crawler
        from Bot_Crawler.tweet_parser import parse_timeline_json
        raw_dir = DATA_DIR
        json_files = list(raw_dir.glob("*.json"))
        if json_files:
            latest_json = max(json_files, key=os.path.getmtime)
            logger.info("⚙️ 将最新的信息流移交至【解析与下载工厂】...")
            await parse_timeline_json(latest_json)  
            
            try:
                latest_json.unlink()
                logger.info(f"🔥 阅后即焚：已彻底销毁原始 JSON ({latest_json.name})，绝不浪费硬盘空间！")
            except Exception as e:
                logger.error(f"⚠️ 销毁 JSON 失败: {e}")
        
        sleep_time = random.randint(240, 420) 
        minutes = sleep_time // 60
        seconds = sleep_time % 60
        
        logger.info(f"💤 全链路操作完成。休眠 {minutes} 分 {seconds} 秒 ({sleep_time}s) 后发起下一次嗅探...\n")
        await asyncio.sleep(sleep_time)

if __name__ == "__main__":
    asyncio.run(crawler_loop())