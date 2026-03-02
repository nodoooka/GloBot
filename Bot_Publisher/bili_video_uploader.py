import aiohttp
import asyncio
import os
import math
import logging
import json
from pathlib import Path
from Bot_Master.tg_bot import ask_video_approval, GloBotState, send_tg_msg

logger = logging.getLogger("GloBot_VideoUp")

AUTH_FILE = Path(__file__).resolve().parent.parent / "auth_store" / "bili_auth.json"

async def upload_video_bilibili(vid_candidates: dict, dynamic_title: str, dynamic_content: str, source_url: str, settings, bypass_tg: bool = False) -> tuple[bool, str]:
    avail_trans = vid_candidates.get("translated")
    avail_orig = vid_candidates.get("original")
    
    preview_path = avail_trans if avail_trans else avail_orig
    if not preview_path:
        logger.error("❌ 没有合法的视频路径可以发布。")
        return False, ""

    if not bypass_tg:
        logger.info("⏸️ 正在挂起管线，等待主理人从 Telegram 预览截帧并选择投递版本...")
        hitl_data = await ask_video_approval(vid_candidates, dynamic_content)
        
        if not hitl_data or 'selected_path' not in hitl_data or not hitl_data['selected_path']:
            logger.warning("🚫 主理人已在 Telegram 拒绝本次视频发布任务。")
            await send_tg_msg("🚫 <b>已取消</b>\n该视频投递任务已被您手动取消。")
            return False, ""
            
        video_path = hitl_data['selected_path']
    else:
        logger.info("🧪 [脱机测试模式] 触发跳过指令！将不呼叫 Telegram，直接使用入参强行发车...")
        video_path = preview_path
        hitl_data = {
            'video_title': dynamic_title,
            'video_tid': getattr(settings.publishers.bilibili, 'video_tid', 171),
            'video_tags': getattr(settings.publishers.bilibili, 'video_tags', "GloBot,测试")
        }
        
    GloBotState.daily_stats['videos'] += 1 

    bili_config = settings.publishers.bilibili
    safe_title = hitl_data.get('video_title', dynamic_title)[:80]
    custom_tid = hitl_data.get('video_tid', getattr(bili_config, 'video_tid', 171))
    custom_tags = hitl_data.get('video_tags', getattr(bili_config, 'video_tags', "地下偶像"))
    
    safe_desc = dynamic_content[:240]
    safe_dynamic = dynamic_content[:220]

    if not AUTH_FILE.exists():
        err = "找不到 bili_auth.json，请运行扫码脚本！"
        logger.error(f"❌ {err}")
        if not bypass_tg: await send_tg_msg(f"⚠️ <b>凭证缺失</b>\n{err}")
        raise RuntimeError(f"AUTH_EXPIRED: {err}")
        
    try:
        with open(AUTH_FILE, "r", encoding="utf-8") as f:
            auth_data = json.load(f)
            
        cookie_parts = []
        for k, v in auth_data.items():
            if v:
                if k.lower() == 'sessdata': k = 'SESSDATA'
                elif k.lower() == 'dedeuserid': k = 'DedeUserID'
                cookie_parts.append(f"{k}={v}")
        cookie_str = "; ".join(cookie_parts)
        bili_jct = auth_data.get('bili_jct', '')
    except Exception as e:
        raise RuntimeError(f"AUTH_EXPIRED: 凭证解析失败: {e}")

    headers = {
        'accept': '*/*',
        'accept-language': 'zh-CN,zh;q=0.9,en;q=0.8',
        'origin': 'https://member.bilibili.com',
        'referer': 'https://member.bilibili.com/platform/upload/video/frame',
        'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36',
        'cookie': cookie_str 
    }

    async with aiohttp.ClientSession(headers=headers) as session:
        total_size = os.path.getsize(video_path)
        logger.info(f"📤 [视频引擎] 准备上传视频: {os.path.basename(video_path)}")

        # 🚨 止血点 1：为申请节点接口加装防抖循环
        pre_url = "https://member.bilibili.com/preupload"
        params = {
            'os': 'upos', 'r': 'upos', 'profile': 'ugcupos/bup', 'ssl': 0,
            'version': '2.8.12', 'build': 2081200,
            'name': os.path.basename(video_path), 'size': total_size,
            'upcdn': 'bda2', 'probe_version': '20221109'
        }
        
        for attempt in range(3):
            try:
                async with session.get(pre_url, params=params, timeout=15) as resp:
                    if resp.status in [401, 403]:
                        raise RuntimeError(f"AUTH_EXPIRED: 申请节点遭遇 HTTP {resp.status} 拦截！")
                    ret = await resp.json()
                    if ret.get("code") == -101:
                        raise RuntimeError("AUTH_EXPIRED: Preupload 返回 -101 账号未登录。")
                    break # 请求成功，跳出重试
            except RuntimeError as e: raise e
            except Exception as e:
                if attempt == 2: raise Exception(f"申请节点彻底失败: {e}")
                logger.warning(f"⚠️ 节点申请网络波动，准备重试: {e}")
                await asyncio.sleep(2)

        auth = ret['auth']
        upos_uri = ret['upos_uri']
        biz_id = ret['biz_id']
        chunk_size = ret['chunk_size']
        upos_url = f"https:{ret['endpoint']}/{upos_uri.replace('upos://', '')}"
        upos_headers = {"X-Upos-Auth": auth, "User-Agent": headers["user-agent"]}

        # 高并发分片上传（已自带 attempt 防抖机制）
        async with session.post(f"{upos_url}?uploads&output=json", headers=upos_headers) as resp:
            upload_id = (await resp.json())["upload_id"]

        parts = []
        chunks = math.ceil(total_size / chunk_size)
        sem = asyncio.Semaphore(3)

        async def upload_chunk(chunk_idx, chunk_data):
            chunk_params = {
                'partNumber': chunk_idx + 1, 'uploadId': upload_id, 'chunk': chunk_idx,
                'chunks': chunks, 'size': len(chunk_data),
                'start': chunk_idx * chunk_size, 'end': chunk_idx * chunk_size + len(chunk_data),
                'total': total_size
            }
            async with sem:
                for attempt in range(3):
                    try:
                        async with session.put(upos_url, params=chunk_params, data=chunk_data, headers=upos_headers, timeout=60) as r:
                            r.raise_for_status()
                            parts.append({"partNumber": chunk_idx + 1, "eTag": "etag"})
                            return
                    except Exception:
                        await asyncio.sleep(2)
                raise Exception(f"切片 {chunk_idx+1} 上传彻底失败！")

        tasks = []
        with open(video_path, 'rb') as f:
            for i in range(chunks):
                tasks.append(upload_chunk(i, f.read(chunk_size)))

        logger.info(f"🚀 [视频引擎] 正在高并发传输 {chunks} 个切片...")
        await asyncio.gather(*tasks)

        parts.sort(key=lambda x: x["partNumber"])
        comp_params = {
            'name': os.path.basename(video_path), 'uploadId': upload_id,
            'biz_id': biz_id, 'output': 'json', 'profile': 'ugcupos/bup'
        }
        async with session.post(upos_url, params=comp_params, json={"parts": parts}, headers=upos_headers) as resp:
            if (await resp.json()).get("OK") != 1:
                raise Exception("合并分片失败")

        bili_filename = upos_uri.split('/')[-1].split('.')[0]
        logger.info(f"✅ [视频引擎] 物理文件上传成功！特征码: {bili_filename}")

        # 🚨 止血点 2：提交最终元数据防抖循环
        submit_url = f"https://member.bilibili.com/x/vu/web/add?csrf={bili_jct}"
        visibility = 1 if getattr(bili_config, 'visibility', 1) == 1 else 0
        
        payload = {
            "copyright": getattr(bili_config, 'video_copyright', 2),
            "source": source_url if getattr(bili_config, 'video_copyright', 2) == 2 else "",
            "tid": custom_tid,
            "cover": "",  
            "title": safe_title,
            "desc_format_id": 0,
            "desc": safe_desc,
            "dynamic": safe_dynamic,
            "subtitle": {"open": 0, "lan": ""},
            "tag": custom_tags,
            "videos": [{"title": safe_title, "filename": bili_filename, "desc": ""}],
            "is_only_self": visibility
        }
        
        for attempt in range(3):
            try:
                logger.info(f"📡 [视频引擎] 正在提交稿件元数据 (尝试 {attempt+1}/3)...")
                async with session.post(submit_url, json=payload, timeout=20) as resp:
                    result = await resp.json()
                    if result.get("code") == -101:
                        raise RuntimeError("AUTH_EXPIRED: 提交稿件时返回 -101 账号未登录。")
                    if result.get("code") == 0:
                        bvid = result.get('data', {}).get('bvid', '')
                        logger.info(f"🎉 [视频引擎] 投稿成功！获得 BVID: {bvid}")
                        if not bypass_tg: 
                            await send_tg_msg(f"✅ <b>视频投稿成功！</b>\n\n📌 <b>标题:</b> {safe_title}\n📺 <b>BVID:</b> <code>{bvid}</code>")
                        return True, bvid
                    else:
                        raise Exception(f"B站接口业务拒绝: {result}")
            except RuntimeError as e: raise e
            except Exception as e:
                if attempt == 2:
                    logger.error(f"❌ 稿件提交彻底失败: {e}")
                    if not bypass_tg: await send_tg_msg(f"❌ <b>视频投稿遭拒！</b>\n接口返回:\n<code>{e}</code>")
                    return False, ""
                logger.warning(f"⚠️ 提交稿件时网络波动: {e}，3秒后重试...")
                await asyncio.sleep(3)