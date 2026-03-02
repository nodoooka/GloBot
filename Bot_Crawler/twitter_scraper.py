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

AUTH_FILE = Path(__file__).resolve().parent.parent / "auth_store" / "twitter_auth.json"

DATA_DIR = Path(os.getenv("LOCAL_DATA_DIR", f"./GloBot_Data/{settings.targets.group_name}")) / "timeline_raw"
DATA_DIR.mkdir(parents=True, exist_ok=True)

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
        
        # 🚨 止血点：抛弃第三方库，直接使用原生底层注入，抹除三大致命风控特征！
        await page.add_init_script("""
            // 1. 抹除无头浏览器最致命的 webdriver 标记
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            
            // 2. 伪装 Chrome 插件特征
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3] });
            
            // 3. 伪装 Chrome 运行时环境
            window.navigator.chrome = { runtime: {} };
        """)
        
        page.on("response", handle_response)
        
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
            
            current_url = page.url
            if "login" in current_url or "logout" in current_url or "suspended" in current_url:
                raise RuntimeError(f"TWITTER_AUTH_EXPIRED: 账号状态异常！当前页面被劫持到了: {current_url}")
            
            logger.info("🖱️ 正在强制切换到【正在关注】(Following) 页面...")
            await page.wait_for_selector('[role="tab"]', timeout=30000)
            tabs = page.locator('[role="tab"]')
            
            if await tabs.count() >= 2:
                await tabs.nth(1).click()
            
            logger.info("⏳ 正在注入混沌浏览行为特征...")
            await page.wait_for_timeout(random.randint(3000, 6000))
            await page.mouse.move(random.randint(200, 1000), random.randint(100, 700))
            await page.mouse.wheel(0, random.randint(800, 2500))
            await page.wait_for_timeout(random.randint(3000, 5000))
            await page.mouse.move(random.randint(200, 1000), random.randint(100, 700))
            await page.mouse.wheel(0, random.randint(-500, 800))
            await page.wait_for_timeout(random.randint(2000, 4000))
            
        except RuntimeError as e:
            raise e 
        except Exception as e:
            logger.error(f"⚠️ 抓取过程发生普通异常(可能引发静默失败): {e}")
            raise e 
        finally:
            await context.close()