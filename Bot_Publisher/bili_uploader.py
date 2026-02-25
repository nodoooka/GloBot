import os
import asyncio
import logging
from pathlib import Path
import sys
import httpx
import json
import urllib.parse
import time
import random
from datetime import datetime

sys.path.append(str(Path(__file__).resolve().parent.parent))
from common.config_loader import settings
from dotenv import load_dotenv
from bilibili_api import Credential, video_uploader

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

load_dotenv()
SESSDATA = urllib.parse.unquote(os.getenv("BILI_SESSDATA", "").strip())
BILI_JCT = os.getenv("BILI_JCT", "").strip()
BUVID3 = os.getenv("BILI_BUVID3", "").strip()
DEDEUSERID = os.getenv("BILI_DEDEUSERID", "").strip()

credential = Credential(sessdata=SESSDATA, bili_jct=BILI_JCT, buvid3=BUVID3, dedeuserid=DEDEUSERID)

# ==========================================
# 🛡️ 1:1 复刻抓包：全局高权限 Headers
# ==========================================
COOKIE_STR = f"SESSDATA={SESSDATA}; bili_jct={BILI_JCT}; DedeUserID={DEDEUSERID}; buvid3={BUVID3}"
BILI_HEADERS = {
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "x-bili-mid": DEDEUSERID,
    "app-key": "android64",  # 🚨 抓包核心：跨端伪装键
    "env": "prod",           # 🚨 抓包核心：生产环境标识
    "referer": "https://www.bilibili.com",
    "cookie": COOKIE_STR
}

# ==========================================
# 🖼️ 辅助引擎：真·BFS 动态图床
# ==========================================
async def upload_image_to_bfs(image_path: Path) -> dict:
    # 🚨 完美对齐你的抓包 URL：upload_bfs
    url = "https://api.bilibili.com/x/dynamic/feed/draw/upload_bfs"
    data = {"biz": "draw", "category": "daily", "csrf": BILI_JCT}
    
    try:
        async with httpx.AsyncClient(headers=BILI_HEADERS) as client:
            with open(image_path, "rb") as f:
                files = {"file_up": (image_path.name, f, "image/jpeg")}
                response = await client.post(url, data=data, files=files)
                
                if response.status_code != 200:
                    logger.error(f"   ❌ [B站图床] HTTP异常 {response.status_code}: {response.text}")
                    return None
                    
                res = response.json()
                if res.get("code") == 0:
                    # 🚨 1:1 对齐抓包数据结构
                    return {
                        "img_width": res["data"]["image_width"],
                        "img_height": res["data"]["image_height"],
                        "img_size": round(os.path.getsize(image_path) / 1024, 3),
                        "img_src": res["data"]["image_url"]
                    }
                else:
                    logger.error(f"   ❌ [B站图床] 业务报错: {res}")
    except Exception as e:
        logger.error(f"   ❌ [B站图床] 图片上传异常: {e}")
    return None

# ==========================================
# 📺 通道一：重型视频投稿
# ==========================================
async def upload_video_submission(video_path: Path, text_content: str):
    cfg = settings.publishers.bilibili
    logger.info(f"   -> [执行] 开始上传视频主体 (较慢，请稍候)...")
    
    tid = 130 
    tags = settings.targets.keywords + ["地下偶像", "日偶"]
    safe_title = cfg.title if cfg.title else (text_content[:60] if text_content else f"【{settings.targets.group_name}】最新搬运")
    
    dtime = None
    if cfg.schedule_time:
        try:
            dtime = int(datetime.strptime(cfg.schedule_time, "%Y-%m-%d %H:%M:%S").timestamp())
        except:
            pass
            
    try:
        upload_result = await video_uploader.upload(
            video_path=str(video_path),
            title=safe_title,
            tid=tid,
            tag=",".join(tags[:10]),
            desc="视频由 GloBot AI 自动搬运压制\n\n" + text_content,
            source="X/Twitter 搬运",
            thread_pool_workers=3,
            credential=credential,
            dynamic=text_content,
            copyright=cfg.creation_declare,
            dtime=dtime
        )
        bvid = upload_result.get('bvid', '')
        if bvid:
            logger.info(f"\n🎉 [发布成功] 视频投稿已提交！链接: https://www.bilibili.com/video/{bvid}")
            return True
    except Exception as e:
        logger.error(f"\n❌ [发布崩溃] 视频投稿失败: {e}")
    return False

# ==========================================
# 📝 通道二：降维打击 1:1 抓包复刻版
# ==========================================
async def publish_native_dynamic(text: str, image_paths: list = []):
    cfg = settings.publishers.bilibili
    
    # 🚨 完美复刻抓包里的终极设备指纹 URL
    device_json = urllib.parse.quote('{"platform": "web", "device": "pc"}')
    web_json = urllib.parse.quote('{"spm_id": "333.999"}')
    url = f"https://api.bilibili.com/x/dynamic/feed/create/dyn?platform=web&csrf={BILI_JCT}&x-bili-device-req-json={device_json}&x-bili-web-req-json={web_json}"
    
    uploaded_pics = []
    if image_paths:
        logger.info(f"   -> [执行] 正在向 B站图床 批量推流 {len(image_paths)} 张图片...")
        tasks = [upload_image_to_bfs(Path(p)) for p in image_paths]
        results = await asyncio.gather(*tasks)
        uploaded_pics = [r for r in results if r]
        logger.info(f"   -> [执行] 图床推流完成，成功 {len(uploaded_pics)} 张。")

    # 🚨 严格对齐抓包数据的 JSON 结构
    dyn_req = {
        "content": {
            "contents": [{"raw_text": text, "type": 1, "biz_id": ""}]
        },
        "scene": 2,
        "attach_card": None,
        "upload_id": f"{DEDEUSERID}_{int(time.time())}_{random.randint(1000, 9999)}",
        "meta": {
            "app_meta": {
                "from": "create.dynamic.web",
                "mobi_app": "web"
            }
        }
    }
    
    # 标题挂载
    if cfg.title:
        dyn_req["content"]["title"] = cfg.title
        
    # 图片挂载
    if uploaded_pics:
        dyn_req["pics"] = uploaded_pics

    # 🔐 传说中的真·私密键 private_pub
    if cfg.visibility == 1:
        dyn_req["option"] = {"private_pub": 1}
        
    payload = {
        "dyn_req": dyn_req
    }
    
    logger.info(f"   -> [执行] 正在发起 B站动态 POST 请求...")
    try:
        async with httpx.AsyncClient(headers=BILI_HEADERS) as client:
            response = await client.post(url, json=payload) 
            
            if response.status_code != 200:
                logger.error(f"\n❌ [发布失败] B站防火墙拦截 HTTP {response.status_code}: {response.text}")
                return False
                
            res = response.json()
            logger.info(f"[B站发射井] 5/5: B站服务器响应 -> {json.dumps(res, ensure_ascii=False)}")
            
            if res.get("code") == 0:
                logger.info("\n🎉 [发布成功] 成了！这是你亲自抓包打通的胜利！快去看看客户端的私密动态！")
                return True
            else:
                logger.error(f"\n❌ [发布失败] B站拒绝了请求: {res.get('message')}")
    except Exception as e:
        logger.error(f"\n❌ [发布崩溃] 网络异常: {e}")
    return False

# ==========================================
# 🚦 智能分发总路由
# ==========================================
async def smart_publish(text_content: str, media_files: list, video_type: str = "none"):
    print("\n" + "="*50)
    logger.info(f"[B站发射井] 1/5: 开始读取 Config 载荷指令...")
    cfg = settings.publishers.bilibili
    
    logger.info(f"\n[B站发射井] 2/5: 正在甄别本地素材文件...")
    videos = [Path(p) for p in media_files if str(p).lower().endswith(('.mp4', '.mov'))]
    images = [Path(p) for p in media_files if str(p).lower().endswith(('.jpg', '.jpeg', '.png'))]
    logger.info(f"   -> 找到 {len(videos)} 个视频文件，{len(images)} 张图片。")
    
    logger.info(f"\n[B站发射井] 3/5: 系统总闸与规则匹配校验...")
    valid = await credential.check_valid()
    if not valid:
        logger.error("   ❌ [拦截] B 站 Cookies 已失效，凭证被打回！请重新抓取。")
        return False

    logger.info(f"\n[B站发射井] 4/5: 智能路由投递...")
    if videos:
        return await upload_video_submission(videos[0], text_content)
    else:
        return await publish_native_dynamic(text_content, images)

if __name__ == "__main__":
    async def run_test():
        test_text = "终于要成功了吧！！"
        # ⚠️ 请确保下面的路径里有一张真实的图片
        test_files = ["/Users/tgmesmer/GloBot/test_image.jpg"]
        await smart_publish(test_text, test_files, video_type="none")
    asyncio.run(run_test())