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
    "app-key": "android64",
    "env": "prod",
    "referer": "https://www.bilibili.com",
    "cookie": COOKIE_STR
}

# ==========================================
# 🖼️ 辅助引擎：真·BFS 动态图床
# ==========================================
async def upload_image_to_bfs(image_path: Path) -> dict:
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
async def upload_video_submission(video_path: Path, text_content: str) -> tuple[bool, str]:
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
            return True, str(bvid)
    except Exception as e:
        logger.error(f"\n❌ [发布崩溃] 视频投稿失败: {e}")
    return False, ""

# ==========================================
# 📝 通道二：降维打击图文发布
# ==========================================
async def publish_native_dynamic(text: str, image_paths: list = []) -> tuple[bool, str]:
    cfg = settings.publishers.bilibili
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

    dyn_req = {
        "content": {"contents": [{"raw_text": text, "type": 1, "biz_id": ""}]},
        "scene": 2,
        "attach_card": None,
        "upload_id": f"{DEDEUSERID}_{int(time.time())}_{random.randint(1000, 9999)}",
        "meta": {"app_meta": {"from": "create.dynamic.web", "mobi_app": "web"}}
    }
    
    if cfg.title:
        safe_title = cfg.title[:15]
        dyn_req["content"]["title"] = safe_title
        
    if uploaded_pics:
        dyn_req["pics"] = uploaded_pics

    if cfg.visibility == 1:
        dyn_req["option"] = {"private_pub": 1}
        
    payload = {"dyn_req": dyn_req}
    
    _debug_title = dyn_req.get("content", {}).get("title", "")
    logger.info(f"   -> [调试探针] 实际即将发送的标题: '{_debug_title}' | 字符数: {len(_debug_title)}")
    logger.info(f"   -> [执行] 正在发起 B站动态 POST 请求...")
    try:
        async with httpx.AsyncClient(headers=BILI_HEADERS) as client:
            response = await client.post(url, json=payload) 
            
            if response.status_code != 200:
                logger.error(f"\n❌ [发布失败] B站防火墙拦截 HTTP {response.status_code}: {response.text}")
                return False, ""
                
            res = response.json()
            if res.get("code") == 0:
                dyn_id_str = res["data"]["dyn_id_str"]
                logger.info(f"\n🎉 [发布成功] 成了！新动态 ID: {dyn_id_str}")
                return True, dyn_id_str
            else:
                logger.error(f"\n❌ [发布失败] B站拒绝了请求: {res.get('message')}")
    except Exception as e:
        logger.error(f"\n❌ [发布崩溃] 网络异常: {e}")
    return False, ""

# ==========================================
# 🔄 通道三：原生动态转发 (带评论)
# ==========================================
async def smart_repost(content: str, orig_dyn_id_str: str) -> tuple[bool, str]:
    cfg = settings.publishers.bilibili
    logger.info(f"   -> [执行] 正在发起 B站原生转发请求 (源动态ID: {orig_dyn_id_str})...")
    
    # 🚨 痛点修复：原生转发卡片不支持独立 title，必须优美地拼接到正文最上方
    repost_text = content
    if cfg.title:
        repost_text = f"【{cfg.title}】\n\n{content}"
    
    device_json = urllib.parse.quote('{"platform": "web", "device": "pc"}')
    web_json = urllib.parse.quote('{"spm_id": "333.999"}')
    url = f"https://api.bilibili.com/x/dynamic/feed/create/dyn?platform=web&csrf={BILI_JCT}&x-bili-device-req-json={device_json}&x-bili-web-req-json={web_json}"
    
    dyn_req = {
        "content": {"contents": [{"raw_text": repost_text, "type": 1, "biz_id": ""}]},
        "scene": 4, # 🚨 核心：Scene 4 触发原生的带评论转发
        "attach_card": None,
        "upload_id": f"{DEDEUSERID}_{int(time.time())}_{random.randint(1000, 9999)}",
        "meta": {"app_meta": {"from": "create.dynamic.web", "mobi_app": "web"}}
    }
    
    # 🚨 痛点修复：严格移除 visibility == 1 时的 "private_pub": 1 逻辑
    # B站转发接口强制公开，附带私密参数会导致请求直接被打回
        
    payload = {
        "dyn_req": dyn_req,
        "web_repost_src": {"dyn_id_str": orig_dyn_id_str}
    }
    
    try:
        async with httpx.AsyncClient(headers=BILI_HEADERS) as client:
            response = await client.post(url, json=payload)
            if response.status_code != 200:
                logger.error(f"❌ [原生转发失败] HTTP {response.status_code}: {response.text}")
                return False, ""
            
            res = response.json()
            if res.get("code") == 0:
                dyn_id_str = res["data"]["dyn_id_str"]
                logger.info(f"🎉 [原生转发成功] 新转发动态 ID: {dyn_id_str}")
                return True, dyn_id_str
            else:
                logger.error(f"❌ [原生转发失败] B站返回: {res}")
                return False, ""
    except Exception as e:
        logger.error(f"❌ [原生转发异常] {e}")
        return False, ""

# ==========================================
# 🚦 智能分发总路由
# ==========================================
async def smart_publish(text_content: str, media_files: list, video_type: str = "none") -> tuple[bool, str]:
    print("\n" + "="*50)
    logger.info(f"[B站发射井] 1/5: 开始读取 Config 载荷指令...")
    
    logger.info(f"\n[B站发射井] 2/5: 正在甄别本地素材文件...")
    videos = [Path(p) for p in media_files if str(p).lower().endswith(('.mp4', '.mov'))]
    images = [Path(p) for p in media_files if str(p).lower().endswith(('.jpg', '.jpeg', '.png'))]
    logger.info(f"   -> 找到 {len(videos)} 个视频文件，{len(images)} 张图片。")
    
    valid = await credential.check_valid()
    if not valid:
        logger.error("   ❌ [拦截] B 站 Cookies 已失效，凭证被打回！请重新抓取。")
        return False, ""

    logger.info(f"\n[B站发射井] 4/5: 智能路由投递...")
    if videos:
        return await upload_video_submission(videos[0], text_content)
    else:
        return await publish_native_dynamic(text_content, images)