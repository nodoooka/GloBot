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
from bilibili_api import Credential, video_uploader

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

# 👇 核心转变：从本地扫码文件读取凭证，替代 .env
AUTH_FILE = Path(__file__).resolve().parent.parent / "auth_store" / "bili_auth.json"

def get_bili_auth():
    if not AUTH_FILE.exists():
        raise RuntimeError("AUTH_EXPIRED: 找不到 bili_auth.json，请运行扫码脚本")
    with open(AUTH_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

# ==========================================
# 🛡️ 1:1 复刻抓包：全局高权限 Headers
# ==========================================
def get_bili_headers():
    auth = get_bili_auth()
    cookie_parts = []
    for k, v in auth.items():
        if v:
            if k.lower() == 'sessdata': k = 'SESSDATA'
            elif k.lower() == 'dedeuserid': k = 'DedeUserID'
            cookie_parts.append(f"{k}={v}")
    
    return {
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "x-bili-mid": auth.get("dedeuserid", ""),
        "app-key": "android64",
        "env": "prod",
        "referer": "https://www.bilibili.com",
        "cookie": "; ".join(cookie_parts)
    }

# ==========================================
# 🕵️ 动态猎犬：视频 BV 号反查真实动态 ID
# ==========================================
async def get_dynamic_id_by_bvid(bvid: str) -> str:
    """反查个人主页前10条动态，使用强容错的链式提取寻找对应的数字 dyn_id"""
    auth = get_bili_auth()
    uid = auth.get("dedeuserid", "")
    if not uid:
        return None
        
    url = f"https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/space?host_mid={uid}"
    
    try:
        async with httpx.AsyncClient(headers=get_bili_headers()) as client:
            response = await client.get(url)
            if response.status_code != 200:
                return None
                
            res_json = response.json()
            if res_json.get("code") != 0:
                return None
                
            data_dict = res_json.get("data") or {}
            items = data_dict.get("items") or []
            
            for item in items:
                dyn_id = item.get("id_str", "")
                modules = item.get("modules") or {}
                module_dynamic = modules.get("module_dynamic") or {}
                major = module_dynamic.get("major") or {}
                archive = major.get("archive") or {}
                
                if archive.get("bvid", "") == bvid:
                    return dyn_id
    except Exception as e:
        logger.error(f"❌ [动态猎犬] 反查异常: {e}")
        
    return None

# ==========================================
# 🖼️ 辅助引擎：真·BFS 动态图床
# ==========================================
async def upload_image_to_bfs(image_path: Path) -> dict:
    auth = get_bili_auth()
    url = "https://api.bilibili.com/x/dynamic/feed/draw/upload_bfs"
    data = {"biz": "draw", "category": "daily", "csrf": auth.get("bili_jct", "")}
    
    try:
        async with httpx.AsyncClient(headers=get_bili_headers()) as client:
            with open(image_path, "rb") as f:
                files = {"file_up": (image_path.name, f, "image/jpeg")}
                response = await client.post(url, data=data, files=files)
                
                if response.status_code in [401, 403]:
                    raise RuntimeError(f"AUTH_EXPIRED: [B站图床] HTTP异常 {response.status_code} 防火墙拦截")
                
                res = response.json()
                if res.get("code") == -101:
                    raise RuntimeError("AUTH_EXPIRED: [B站图床] 返回 -101 账号未登录")
                    
                if res.get("code") == 0:
                    return {
                        "img_width": res["data"]["image_width"],
                        "img_height": res["data"]["image_height"],
                        "img_size": round(os.path.getsize(image_path) / 1024, 3),
                        "img_src": res["data"]["image_url"]
                    }
                else:
                    logger.error(f"   ❌ [B站图床] 业务报错: {res}")
    except RuntimeError as e:
        raise e
    except Exception as e:
        logger.error(f"   ❌ [B站图床] 图片上传异常: {e}")
    return None

# ==========================================
# 📝 通道二：降维打击图文发布
# ==========================================
async def publish_native_dynamic(text: str, image_paths: list = []) -> tuple[bool, str]:
    cfg = settings.publishers.bilibili
    auth = get_bili_auth()
    device_json = urllib.parse.quote('{"platform": "web", "device": "pc"}')
    web_json = urllib.parse.quote('{"spm_id": "333.999"}')
    url = f"https://api.bilibili.com/x/dynamic/feed/create/dyn?platform=web&csrf={auth.get('bili_jct', '')}&x-bili-device-req-json={device_json}&x-bili-web-req-json={web_json}"
    
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
        "upload_id": f"{auth.get('dedeuserid', '')}_{int(time.time())}_{random.randint(1000, 9999)}",
        "meta": {"app_meta": {"from": "create.dynamic.web", "mobi_app": "web"}}
    }
    
    if cfg.title:
        # 🚨 保护 API：原生图文标题强制安全截断，防止触发 HTTP 400 报错
        safe_title = cfg.title[:20]
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
        async with httpx.AsyncClient(headers=get_bili_headers()) as client:
            response = await client.post(url, json=payload) 
            
            if response.status_code in [401, 403]:
                raise RuntimeError(f"AUTH_EXPIRED: [图文发布] B站防火墙拦截 HTTP {response.status_code}")
                
            res = response.json()
            if res.get("code") == -101:
                raise RuntimeError("AUTH_EXPIRED: [图文发布] 返回 -101 账号未登录")
                
            if res.get("code") == 0:
                dyn_id_str = res["data"]["dyn_id_str"]
                logger.info(f"\n🎉 [发布成功] 成了！新动态 ID: {dyn_id_str}")
                return True, dyn_id_str
            else:
                logger.error(f"\n❌ [发布失败] B站拒绝了请求: {res.get('message')}")
    except RuntimeError as e:
        raise e
    except Exception as e:
        logger.error(f"\n❌ [发布崩溃] 网络异常: {e}")
    return False, ""

# ==========================================
# 🔄 通道三：原生动态转发 (带评论)
# ==========================================
async def smart_repost(content: str, orig_dyn_id_str: str) -> tuple[bool, str]:
    cfg = settings.publishers.bilibili
    auth = get_bili_auth()
    logger.info(f"   -> [执行] 正在发起 B站原生转发请求 (源动态ID: {orig_dyn_id_str})...")
    
    repost_text = content
    if cfg.title:
        # 🚨 痛点修复：利用纯文本无字数限制的优势释放完整长名字，且剥离外层的方括号！
        repost_text = f"{cfg.title}\n\n{content}"
    
    device_json = urllib.parse.quote('{"platform": "web", "device": "pc"}')
    web_json = urllib.parse.quote('{"spm_id": "333.999"}')
    url = f"https://api.bilibili.com/x/dynamic/feed/create/dyn?platform=web&csrf={auth.get('bili_jct', '')}&x-bili-device-req-json={device_json}&x-bili-web-req-json={web_json}"
    
    dyn_req = {
        "content": {"contents": [{"raw_text": repost_text, "type": 1, "biz_id": ""}]},
        "scene": 4, # 🚨 核心：Scene 4 触发原生的带评论转发
        "attach_card": None,
        "upload_id": f"{auth.get('dedeuserid', '')}_{int(time.time())}_{random.randint(1000, 9999)}",
        "meta": {"app_meta": {"from": "create.dynamic.web", "mobi_app": "web"}}
    }
    
    payload = {
        "dyn_req": dyn_req,
        "web_repost_src": {"dyn_id_str": orig_dyn_id_str}
    }
    
    try:
        async with httpx.AsyncClient(headers=get_bili_headers()) as client:
            response = await client.post(url, json=payload)
            if response.status_code in [401, 403]:
                raise RuntimeError(f"AUTH_EXPIRED: [原生转发] HTTP {response.status_code} 防火墙拦截")
            
            res = response.json()
            if res.get("code") == -101:
                raise RuntimeError("AUTH_EXPIRED: [原生转发] 返回 -101 账号未登录")
                
            if res.get("code") == 0:
                dyn_id_str = res["data"]["dyn_id_str"]
                logger.info(f"🎉 [原生转发成功] 新转发动态 ID: {dyn_id_str}")
                return True, dyn_id_str
            else:
                logger.error(f"❌ [原生转发失败] B站返回: {res}")
                return False, ""
    except RuntimeError as e:
        raise e
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
    images = [Path(p) for p in media_files if str(p).lower().endswith(('.jpg', '.jpeg', '.png'))]
    logger.info(f"   -> 找到 {len(images)} 张图片，即将走纯图文/动态发布通道。")
    
    logger.info(f"\n[B站发射井] 4/5: 智能路由投递...")
    return await publish_native_dynamic(text_content, images)