import asyncio
from playwright.async_api import async_playwright
from pathlib import Path

AUTH_DIR = Path(__file__).resolve().parent.parent / "auth_store"
AUTH_FILE = AUTH_DIR / "twitter_auth.json"

async def generate_auth():
    AUTH_DIR.mkdir(parents=True, exist_ok=True)
    
    print("🚀 正在通过后门端口连接到你刚才打开的 Chrome...")
    async with async_playwright() as p:
        try:
            browser = await p.chromium.connect_over_cdp("http://localhost:9222")
            
            context = browser.contexts[0]
            page = context.pages[0] if context.pages else await context.new_page()
            
            print("\n👉 连接成功！请在那个 Chrome 窗口中，手动输入账号密码登录推特！")
            
            await page.goto("https://x.com/login", wait_until="domcontentloaded")
            
            await page.wait_for_url("https://x.com/home", timeout=300000)
            await page.wait_for_timeout(5000)
            
            await context.storage_state(path=AUTH_FILE)
            
            print(f"\n✅ 登录状态提取成功！")
            print(f"🎉 免密通行证已永久保存至: {AUTH_FILE}")
            
        except Exception as e:
            print(f"\n❌ 劫持失败: {e}")
        finally:
            print("\n🧹 提取完毕。你可以手动把那个带后门的 Chrome 关掉了！")

if __name__ == "__main__":
    asyncio.run(generate_auth())