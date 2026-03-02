import asyncio
import json
import os
import sys
import logging
import random
from datetime import datetime
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

from playwright.async_api import async_playwright, Response
from common.config_loader import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# 旧版 Cookie 文件，仅用作兜底导入
AUTH_FILE = Path(__file__).resolve().parent.parent / "auth_store" / "twitter_auth.json"

DATA_DIR = Path(os.getenv("LOCAL_DATA_DIR", f"./GloBot_Data/{settings.targets.group_name}")) / "timeline_raw"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# 👇 划定专属磁盘区域：Playwright 的原生缓存目录
BROWSER_CACHE_DIR = Path(os.getenv("LOCAL_DATA_DIR", f"./GloBot_Data/{settings.targets.group_name}")) / "browser_profile"
BROWSER_CACHE_DIR.mkdir(parents=True, exist_ok=True)

async def handle_response(response: Response):
    if "graphql" in response.url and "HomeLatestTimeline" in response.url:
        try:
            json_data = await response.json()
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            save_path = DATA_DIR / f"timeline_following_{timestamp}.json"
            with open(save_path, "w", encoding="utf-8") as f:
                json.dump(json_data, f, ensure_ascii=False, indent=2)
            logger.info(f"🎯 成功截获纯净版【正在关注】信息流！")
        except Exception as e:
            pass

async def fetch_timeline():
    logger.info("🚀 唤醒隐身拟人内核，潜入 X 主页提取最新动态...")
    
    async with async_playwright() as p:
        # 🚨 核心重构：启用持久化上下文，原生加载图片，利用 "--disk-cache-size" 将物理缓存锁死在 200MB 以内
        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(BROWSER_CACHE_DIR),
            headless=True,
            args=[
                "--disable-dev-shm-usage",
                "--no-sandbox",
                "--disk-cache-size=209715200", 
                "--headless=new" 
            ],
            viewport={'width': 1280, 'height': 800},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        
        page = context.pages[0] if context.pages else await context.new_page()
        page.on("response", handle_response)
        
        # 将旧版 auth.json 的 cookie 手动过渡到这个持久化大脑中
        if AUTH_FILE.exists():
            try:
                with open(AUTH_FILE, "r") as f:
                    auth_data = json.load(f)
                    if "cookies" in auth_data:
                        await context.add_cookies(auth_data["cookies"])
            except: pass

        try:
            await page.goto("https://x.com/home", timeout=60000)
            await page.wait_for_timeout(random.randint(2000, 4000))
            
            # 🚨 T0 级防线：如果内核发现被重定向到了异常页面，立即抛出致命异常交由总线熔断！
            current_url = page.url
            if "login" in current_url or "logout" in current_url or "suspended" in current_url:
                raise RuntimeError(f"TWITTER_AUTH_EXPIRED: 账号状态异常！当前页面被劫持到了: {current_url}")
            
            logger.info("🖱️ 正在强制切换到【正在关注】(Following) 页面...")
            # 放宽了容错时长
            await page.wait_for_selector('[role="tab"]', timeout=30000)
            tabs = page.locator('[role="tab"]')
            
            if await tabs.count() >= 2:
                await tabs.nth(1).click()
            
            logger.info("⏳ 正在注入混沌浏览行为特征...")
            # 🚨 核心重构：废除死板的滑行，引入真随机贝塞尔/齿轮扰动
            await page.wait_for_timeout(random.randint(3000, 6000))
            
            await page.mouse.move(random.randint(200, 1000), random.randint(100, 700))
            await page.mouse.wheel(0, random.randint(800, 2500))
            
            await page.wait_for_timeout(random.randint(3000, 5000))
            
            await page.mouse.move(random.randint(200, 1000), random.randint(100, 700))
            await page.mouse.wheel(0, random.randint(-500, 800))
            
            await page.wait_for_timeout(random.randint(2000, 4000))
            
        except RuntimeError as e:
            raise e # 必须把封禁异常原样丢给 main.py
        except Exception as e:
            logger.error(f"⚠️ 抓取过程发生普通异常(可能引发静默失败): {e}")
            raise e 
        finally:
            # 持久化上下文不会清空你的硬盘缓存，它只负责安全下线。
            await context.close()