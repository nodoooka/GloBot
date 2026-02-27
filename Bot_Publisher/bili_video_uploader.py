import aiohttp
import asyncio
import os
import math
import logging
# 引入我们刚才写好的拦截器
from Bot_Master.tg_bot import ask_video_approval, GloBotState

logger = logging.getLogger("GloBot_VideoUp")

async def upload_video_bilibili(video_path: str, dynamic_title: str, dynamic_content: str, source_url: str, settings) -> tuple[bool, str]:
    """
    极客级 B 站 Web 端视频异步并发上传引擎 (环境变量安全版)
    """
    # 🔒 强制从 .env 环境变量读取敏感凭证，绝对禁止从 config 传入
    sessdata = os.getenv("BILI_SESSDATA") or os.getenv("SESSDATA")
    bili_jct = os.getenv("BILI_JCT") or os.getenv("BILI_JCT")
    
    if not bili_jct or not sessdata:
        logger.error("❌ 严重错误: 无法在 .env 中找到 BILI_SESSDATA 或 BILI_JCT，拒绝执行视频上传！")
        return False, ""

    cookies = {"SESSDATA": sessdata, "bili_jct": bili_jct}
    headers = {
        'User-Agent': "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/119.0.0.0 Safari/537.36",
        'Referer': "https://member.bilibili.com/"
    }
    
    async with aiohttp.ClientSession(cookies=cookies, headers=headers) as session:
        total_size = os.path.getsize(video_path)
        logger.info(f"📤 [视频引擎] 准备上传视频: {os.path.basename(video_path)} (大小: {total_size/1024/1024:.2f} MB)")
        
        # ==========================================
        # 1. Preupload 获取节点 (固定 bda2 线路防止探测失败)
        # ==========================================
        pre_url = "https://member.bilibili.com/preupload"
        params = {
            'os': 'upos', 'r': 'upos', 'profile': 'ugcupos/bup', 'ssl': 0,
            'version': '2.8.12', 'build': 2081200,
            'name': os.path.basename(video_path), 'size': total_size,
            'upcdn': 'bda2', 'probe_version': '20221109'
        }
        async with session.get(pre_url, params=params) as resp:
            ret = await resp.json()
            
        auth = ret['auth']
        endpoint = ret['endpoint']
        upos_uri = ret['upos_uri']
        biz_id = ret['biz_id']
        chunk_size = ret['chunk_size']
        
        upos_url = f"https:{endpoint}/{upos_uri.replace('upos://', '')}"
        upos_headers = {"X-Upos-Auth": auth}
        
        # ==========================================
        # 2. 初始化上传并开启高并发切片
        # ==========================================
        async with session.post(f"{upos_url}?uploads&output=json", headers=upos_headers) as resp:
            upload_id = (await resp.json())["upload_id"]
            
        parts = []
        chunks = math.ceil(total_size / chunk_size)
        sem = asyncio.Semaphore(3)  # 控制并发防风控
        
        async def upload_chunk(chunk_idx, chunk_data):
            chunk_params = {
                'partNumber': chunk_idx + 1, 'uploadId': upload_id, 'chunk': chunk_idx,
                'chunks': chunks, 'size': len(chunk_data),
                'start': chunk_idx * chunk_size, 'end': chunk_idx * chunk_size + len(chunk_data),
                'total': total_size
            }
            async with sem:
                for attempt in range(3): # 切片容错重试机制
                    try:
                        async with session.put(upos_url, params=chunk_params, data=chunk_data, headers=upos_headers) as r:
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
        
        # ==========================================
        # 3. 合并分片
        # ==========================================
        parts.sort(key=lambda x: x["partNumber"])
        comp_params = {
            'name': os.path.basename(video_path), 'uploadId': upload_id,
            'biz_id': biz_id, 'output': 'json', 'profile': 'ugcupos/bup'
        }
        async with session.post(upos_url, params=comp_params, json={"parts": parts}, headers=upos_headers) as resp:
            if (await resp.json()).get("OK") != 1:
                raise Exception("合并分片失败")
                
        bili_filename = upos_uri.split('/')[-1].split('.')[0]
        logger.info(f"✅ [视频引擎] 物理文件上传成功！视频特征码: {bili_filename}")
        
        # ==========================================
        # 👑 [新增] 呼叫 Telegram 进行人工审核定稿
        # ==========================================
        logger.info("⏸️ 正在挂起管线，等待主理人从 Telegram 下发视频元数据...")
        
        # 这个 await 会彻底卡住这个函数的执行，直到你在 TG 发送了 yes 确认
        hitl_data = await ask_video_approval(video_path, dynamic_content)
        
        if not hitl_data:
            logger.warning("🚫 主理人已在 Telegram 拒绝本次视频发布任务。")
            return False, ""
            
        GloBotState.daily_stats['videos'] += 1 # 统计发布的视频
        
        # 提取用户在 TG 手动配置的数据
        safe_title = hitl_data.get('video_title', dynamic_title)[:80]
        custom_tid = hitl_data.get('video_tid', getattr(bili_config, 'video_tid', 171))
        custom_tags = hitl_data.get('video_tags', getattr(bili_config, 'video_tags', "地下偶像"))
        
        safe_desc = dynamic_content[:2000]
        # ==========================================

        # 4. 提交视频稿件元数据 (接下来的 payload 用 custom_tid 和 custom_tags 替换掉原本写死的变量)
        submit_url = f"https://member.bilibili.com/x/vu/web/add?csrf={bili_jct}"
        
        visibility = 1 if getattr(bili_config, 'visibility', 1) == 1 else 0

        payload = {
            "copyright": getattr(bili_config, 'video_copyright', 2),
            "source": source_url if getattr(bili_config, 'video_copyright', 2) == 2 else "",
            "tid": custom_tid, # 👈 使用 TG 收到的 TID
            "cover": "", 
            "title": safe_title, # 👈 使用 TG 收到的标题
            "desc_format_id": 0,
            "desc": safe_desc,
            "dynamic": safe_desc,
            "subtitle": {"open": 0, "lan": ""},
            "tag": custom_tags, # 👈 使用 TG 收到的标签
            "videos": [{"title": safe_title, "filename": bili_filename, "desc": ""}],
            "is_only_self": visibility
        }
        
        logger.info("📡 [视频引擎] 正在提交稿件元数据...")
        async with session.post(submit_url, json=payload) as resp:
            result = await resp.json()
            if result.get("code") != 0:
                logger.error(f"❌ 稿件提交失败: {result}")
                return False, ""
            
            bvid = result.get('data', {}).get('bvid', '')
            logger.info(f"🎉 [视频引擎] 投稿成功！获得 BVID: {bvid}")
            return True, bvid