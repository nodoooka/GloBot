import asyncio
import aiohttp
import json
import urllib.parse
from pathlib import Path

# 定义凭证的保存路径，已受 .gitignore 保护
AUTH_DIR = Path(__file__).resolve().parent.parent / "auth_store"
AUTH_FILE = AUTH_DIR / "bili_auth.json"

async def generate_bili_auth():
    AUTH_DIR.mkdir(parents=True, exist_ok=True)
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    async with aiohttp.ClientSession(headers=headers) as session:
        # 1. 访问首页，让 B站下发 buvid3 等基础防封指纹
        await session.get("https://www.bilibili.com")
        
        # 2. 申请专属的登录 QR 码
        async with session.get("https://passport.bilibili.com/x/passport-login/web/qrcode/generate") as resp:
            res = await resp.json()
            url = res["data"]["url"]
            qrcode_key = res["data"]["qrcode_key"]
            
        # 调用免费在线 API 将底层数据流转换为肉眼可见的二维码图片
        qr_api = f"https://api.qrserver.com/v1/create-qr-code/?size=400x400&data={urllib.parse.quote(url)}"
        
        print("=" * 60)
        print("🚀 B 站原生脱机扫码系统启动！")
        print("👉 请在 Mac 终端中【按住 Command 键并点击】下方链接，在浏览器中查看二维码：")
        print(f"\n   {qr_api}\n")
        print("📱 然后打开手机 Bilibili APP，使用右上角的【扫一扫】")
        print("=" * 60)
        
        # 3. 开始异步轮询，监听手机端的扫码动作
        while True:
            await asyncio.sleep(2)
            poll_url = f"https://passport.bilibili.com/x/passport-login/web/qrcode/poll?qrcode_key={qrcode_key}"
            async with session.get(poll_url) as resp:
                poll_res = await resp.json()
                code = poll_res["data"]["code"]
                
                if code == 86101:
                    pass # 还在静静等待扫码
                elif code == 86090:
                    print("✅ 二维码已扫描，请在手机端点击【确认登录】...")
                elif code == 86038:
                    print("❌ 二维码已过期，请重新运行本脚本。")
                    return
                elif code == 0:
                    print("🎉 登录成功！正在提取防风控终极凭证...")
                    
                    # 从底层 Cookie 池中暴力提取所有高价值指纹
                    cookies = {cookie.key: cookie.value for cookie in session.cookie_jar}
                    
                    auth_data = {
                        "sessdata": cookies.get("SESSDATA", ""),
                        "bili_jct": cookies.get("bili_jct", ""),
                        "dedeuserid": cookies.get("DedeUserID", ""),
                        "buvid3": cookies.get("buvid3", "ED64B292-54DF-D74E-4005-AEC1A5A3406C39800infoc"),
                        "ac_time_value": cookies.get("ac_time_value", "")
                    }
                    
                    with open(AUTH_FILE, "w", encoding="utf-8") as f:
                        json.dump(auth_data, f, indent=4)
                        
                    print(f"\n✅ 成了！B 站全套指纹凭证已安全保存至: {AUTH_FILE}")
                    print("🔒 该文件已被屏蔽，绝对不会泄露到 GitHub。现在您可以去运行视频上传了！")
                    return

if __name__ == "__main__":
    # 使用 Windows / Mac 兼容的异步事件循环
    asyncio.run(generate_bili_auth())