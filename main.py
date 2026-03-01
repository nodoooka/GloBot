import os
import json
import time
import logging
import asyncio
import random
import html
from Bot_Master.tg_bot import start_telegram_bot, send_tg_msg, send_tg_error, GloBotState
import traceback
from pathlib import Path
from datetime import datetime

from common.config_loader import settings
from Bot_Crawler.twitter_scraper import fetch_timeline
from Bot_Crawler.tweet_parser import parse_timeline_json
from Bot_Media.llm_translator import translate_text
from Bot_Media.media_pipeline import dispatch_media
from Bot_Publisher.bili_uploader import smart_publish, smart_repost, get_dynamic_id_by_bvid
from common.text_sanitizer import sanitize_for_bilibili

# ==========================================
# 🔇 全局日志静音配置 (防刷屏)
# ==========================================
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("telegram.ext.Updater").setLevel(logging.CRITICAL)

from Bot_Publisher.bili_video_uploader import upload_video_bilibili 

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("GloBot_Main")

DATA_DIR = Path(os.getenv("LOCAL_DATA_DIR", f"./GloBot_Data/{settings.targets.group_name}"))
RAW_DIR = DATA_DIR / "timeline_raw"
HISTORY_FILE = DATA_DIR / "history.json"
DYN_MAP_FILE = DATA_DIR / "dyn_map.json"
FIRST_RUN_FLAG_FILE = DATA_DIR / ".first_run_completed"

def load_history():
    if not HISTORY_FILE.exists(): return set()
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f: return set(json.load(f))
    except: return set()

def save_history(history_set):
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(list(history_set), f, ensure_ascii=False, indent=2)

def load_dyn_map():
    if not DYN_MAP_FILE.exists(): return {}
    try:
        with open(DYN_MAP_FILE, "r", encoding="utf-8") as f: return json.load(f)
    except: return {}

def save_dyn_map(dyn_map):
    DYN_MAP_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(DYN_MAP_FILE, "w", encoding="utf-8") as f:
        json.dump(dyn_map, f, ensure_ascii=False, indent=2)

# ==========================================
# 🧹 自动化媒体垃圾回收机制
# ==========================================
def cleanup_old_media(retention_days=2.0):
    """定期清理过期的原始媒体文件，防止硬盘爆炸"""
    media_dir = DATA_DIR / "media"
    if not media_dir.exists(): return
    
    current_time = time.time()
    cutoff_time = current_time - (retention_days * 24 * 3600)
    
    deleted_files = 0
    for file_path in media_dir.rglob('*'):
        if file_path.is_file():
            if file_path.stat().st_mtime < cutoff_time:
                try:
                    file_path.unlink()
                    deleted_files += 1
                except Exception as e:
                    logger.error(f"❌ 无法删除过期文件 {file_path.name}: {e}")
                    
    for member_dir in media_dir.iterdir():
        if member_dir.is_dir() and not any(member_dir.iterdir()):
            try: member_dir.rmdir()
            except: pass
            
    if deleted_files > 0:
        logger.info(f"🧹 [空间管理] 触发自动清理！已永久销毁 {deleted_files} 个超过 {retention_days} 天的陈旧媒体文件。")

async def process_media_files(media_list):
    final_paths = []
    video_type = "none"
    for mf in media_list:
        if str(mf).lower().endswith(('.mp4', '.mov')):
            logger.info(f"   -> 正在启动媒体管线压制视频...")
            source_file = Path(mf)
            PUBLISH_DIR = DATA_DIR / "ready_to_publish"
            PUBLISH_DIR.mkdir(parents=True, exist_ok=True)
            output_file = PUBLISH_DIR / f"final_{source_file.name}"
            
            await dispatch_media(str(source_file))
            if output_file.exists():
                final_paths.append(str(output_file))
                video_type = "translated" if settings.media_engine.enable_ai_translation else "original"
            else:
                final_paths.append(str(source_file)) 
        else:
            final_paths.append(mf)
    return final_paths, video_type

def cleanup_media(media_paths):
    for f in media_paths:
        if "ready_to_publish" in str(f):
            try: Path(f).unlink()
            except: pass

# ==========================================
# 🧩 智能防扁平化排版引擎 (双轨兜底制)
# ==========================================
def build_repost_context(prev_tw_id, dyn_map, settings_obj):
    if not prev_tw_id or prev_tw_id not in dyn_map:
        return ""
        
    prev_info = dyn_map[prev_tw_id]
    if not isinstance(prev_info, dict):
        return "" # 兼容你的老版本纯字符串格式
        
    p_handle = prev_info.get("author_handle", "")
    p_disp = prev_info.get("author_display_name", p_handle)
    p_is_reply = prev_info.get("is_reply", False)
    p_dt = prev_info.get("dt_str", "")
    p_trans = prev_info.get("translated_text", "")
    p_raw = prev_info.get("raw_text", "")
    
    # 1. 户口本检索兜底双轨裁决
    p_name = settings_obj.targets.account_title_map.get(p_handle, p_disp)
    # 2. 提取搬运号ID (可通过 config.yaml 下的 publishers.bilibili.account_id 配置)
    my_account = getattr(settings_obj.publishers.bilibili, 'account_id', 'GloBot搬运')
    
    if p_is_reply:
        # 单行极致压缩形态
        c_trans = p_trans.replace('\n', ' ')
        c_raw = p_raw.replace('\n', ' ')
        return f"\n//@{my_account}: 💬{p_name}回复说： {c_trans} 【原文】 {c_raw}"
    else:
        # 1:1 多行还原形态
        return f"\n//@{my_account}: {p_name}\n\n{p_dt}\n\n{p_trans}\n\n【原文】\n{p_raw}"

async def process_pipeline(tweet: dict, dyn_map: dict, preprocessing_cache: dict) -> tuple[bool, str]:
    logger.info(f"\n" + "="*50)
    logger.info(f"🚀 开始处理推文树... 目标终点成员: @{tweet['author']}")
    
    id_retention_level = getattr(settings.publishers.bilibili, 'tweet_id_retention', 0)
    
    prev_dyn_id = None
    prev_tw_id = None # 核心纽带：用于上下游节点通过本地全息字典寻址
    
    # ==========================================
    # 🔗 第一阶段：处理祖先节点
    # ==========================================
    for ancestor in tweet.get('quote_chain', []):
        anc_id = str(ancestor['id'])
        is_reply = ancestor.get('is_reply', False)
        is_placeholder = ancestor.get('is_placeholder', False)
        
        if anc_id in dyn_map:
            prev_info = dyn_map[anc_id]
            if isinstance(prev_info, dict):
                prev_dyn_id = prev_info.get("dyn_id")
            else:
                prev_dyn_id = prev_info
                
            prev_tw_id = anc_id
            logger.info(f"   -> ♻️ 记忆寻址命中：祖先节点 {anc_id} 已搬运，跳过首发，将其作为套娃基底。")
            continue
            
        if is_placeholder:
            logger.info(f"   -> ⚠️ 祖先节点 {anc_id} 仅为防断链占位符且无记忆，跳过强行发布。")
            continue
            
        logger.info(f"   -> ⛓️ 发现全新未搬运的祖先节点！开始穿透发布: @{ancestor['author']}")
        
        # ⚡ 极速加载并发结果
        anc_translated = preprocessing_cache[anc_id]['translated_text']
        
        dt_str = datetime.fromtimestamp(ancestor['timestamp']).strftime("%Y-%m-%d %H:%M:%S")
        clean_raw = html.unescape(ancestor['text'])
        
        author_handle = ancestor['author']
        author_display = ancestor.get('author_display_name', f"@{author_handle}")
        display_name = settings.targets.account_title_map.get(author_handle, author_display)
        
        # 🚨 终极排版渲染：切除一切正文方括号和多余标题！
        if is_reply:
            settings.publishers.bilibili.title = "" 
            anc_content = f"💬{display_name}回复说：\n{anc_translated}\n\n(原文: {clean_raw})"
            if id_retention_level == 0:
                anc_content += f"\n\n{anc_id}"
        else:
            settings.publishers.bilibili.title = display_name[:15]
            anc_content = f"{dt_str}\n\n{anc_translated}\n\n【原文】\n{clean_raw}"
            if id_retention_level < 3:
                anc_content += f"\n\n{anc_id}\n-由GloBot驱动"
        
        anc_content = sanitize_for_bilibili(anc_content)
        
        anc_media = preprocessing_cache[anc_id]['final_media']
        anc_video_type = preprocessing_cache[anc_id]['video_type']
        anc_source_url = f"https://x.com/{ancestor['author']}/status/{anc_id}"
        
        has_anc_video = (anc_video_type == "translated" and settings.publishers.bilibili.publish_translated_video) or \
                        (anc_video_type == "original" and settings.publishers.bilibili.publish_original_video)
        vid_path = next((p for p in anc_media if str(p).lower().endswith('.mp4')), None) if has_anc_video else None
        has_any_media = len(anc_media) > 0
        
        if prev_dyn_id:
            real_prev_dyn_id = prev_dyn_id
            
            if isinstance(prev_dyn_id, str) and prev_dyn_id.startswith("BV"):
                resolved_id = await get_dynamic_id_by_bvid(prev_dyn_id)
                if resolved_id:
                    real_prev_dyn_id = resolved_id
                    logger.info(f"   -> 🎯 [动态猎犬] 成功将 {prev_dyn_id} 反查为动态 ID: {real_prev_dyn_id}")
                else:
                    logger.warning(f"   -> ⚠️ [动态猎犬] 反查 {prev_dyn_id} 失败，将被迫执行降级发布。")

            # 👇 注入点：全息溯源文本拼接 (完美避开 B站吞卡片)
            context_suffix = build_repost_context(prev_tw_id, dyn_map, settings)
            anc_content += context_suffix

            if has_any_media or str(real_prev_dyn_id).startswith("BV"):
                ref_link = f"https://www.bilibili.com/video/{prev_dyn_id}" if str(prev_dyn_id).startswith("BV") else f"https://t.bilibili.com/{prev_dyn_id}"
                if is_reply:
                    anc_content += f"\n\n(🔗 溯源: {ref_link})"
                else:
                    anc_content += f"\n\n🔗 溯源: {ref_link}"
                    
                if vid_path:
                    logger.info(f"   -> 🆕 [智能降级] 含媒体/反查拦截，转为独立视频投稿 (附溯源)...")
                    if id_retention_level >= 2:
                        anc_content = anc_content.replace(f"\n\n{anc_id}\n-由GloBot驱动", "").replace(f"\n\n{anc_id}", "")
                    success, new_anc_dyn_id = await upload_video_bilibili(vid_path, display_name[:80] if not is_reply else f"{display_name}的视频回复", anc_content, anc_source_url, settings)
                else:
                    logger.info(f"   -> 🆕 [智能降级] 含媒体/反查拦截，转为独立图文动态 (附溯源)...")
                    success, new_anc_dyn_id = await smart_publish(anc_content, anc_media, video_type=anc_video_type)
            else:
                logger.info(f"   -> 🔄 触发 B 站原生纯文本转发机制...")
                success, new_anc_dyn_id = await smart_repost(anc_content, real_prev_dyn_id)
        else:
            if vid_path:
                logger.info(f"   -> 🆕 [祖先节点] 移交视频投稿中枢...")
                if id_retention_level >= 2:
                    anc_content = anc_content.replace(f"\n\n{anc_id}\n-由GloBot驱动", "").replace(f"\n\n{anc_id}", "")
                success, new_anc_dyn_id = await upload_video_bilibili(vid_path, display_name[:80] if not is_reply else f"{display_name}的视频回复", anc_content, anc_source_url, settings)
            else:
                logger.info(f"   -> 🆕 正在将推文树的最底层根节点进行首发...")
                success, new_anc_dyn_id = await smart_publish(anc_content, anc_media, video_type=anc_video_type)
            
        cleanup_media(anc_media)
        
        if success and new_anc_dyn_id:
            # 📦 第一阶段存储：写入原生字典
            dyn_map[anc_id] = {
                "dyn_id": new_anc_dyn_id,
                "author_handle": author_handle,
                "author_display_name": author_display,
                "is_reply": is_reply,
                "dt_str": dt_str,
                "translated_text": anc_translated,
                "raw_text": clean_raw
            }
            save_dyn_map(dyn_map)
            prev_dyn_id = new_anc_dyn_id
            prev_tw_id = anc_id  # 让下游节点接住链条
            logger.warning("   -> ⏳ [风控规避] 祖先节点发射成功，强制开启 65 秒冷却通道...")
            await asyncio.sleep(65)
        else:
            logger.error(f"❌ 引用/回复 节点链条断裂，发布终止！")
            return False, ""

    # ==========================================
    # 👑 第二阶段：处理成员的最终点评 (叶子节点)
    # ==========================================
    logger.info(f"   -> 👑 链路穿透完成，开始处理最终成员点评！")
    
    tw_id = str(tweet['id'])
    translated_text = preprocessing_cache[tw_id]['translated_text']
    
    dt_str = datetime.fromtimestamp(tweet['timestamp']).strftime("%Y-%m-%d %H:%M:%S")
    clean_raw_text = html.unescape(tweet['text'])
    
    author_handle = tweet['author']
    display_name = settings.targets.account_title_map.get(author_handle, tweet.get('author_display_name', f"@{author_handle}"))
    
    is_leaf_reply = tweet.get('is_reply', False)
    
    # 🚨 终极排版渲染：切除所有正文多余方括号和重复标题
    if is_leaf_reply:
        settings.publishers.bilibili.title = ""
        final_content = f"💬{display_name}回复说：\n{translated_text}\n\n(原文: {clean_raw_text})"
        if id_retention_level == 0:
            final_content += f"\n\n{tw_id}"
    else:
        settings.publishers.bilibili.title = display_name[:15]
        final_content = f"{dt_str}\n\n{translated_text}\n\n【原文】\n{clean_raw_text}"
        if id_retention_level < 3:
            final_content += f"\n\n{tw_id}\n-由GloBot驱动"

    final_content = sanitize_for_bilibili(final_content)
    
    final_media = preprocessing_cache[tw_id]['final_media']
    video_type = preprocessing_cache[tw_id]['video_type']
    final_source_url = f"https://x.com/{tweet['author']}/status/{tw_id}"
    
    has_final_video = (video_type == "translated" and settings.publishers.bilibili.publish_translated_video) or \
                      (video_type == "original" and settings.publishers.bilibili.publish_original_video)
    vid_path = next((p for p in final_media if str(p).lower().endswith('.mp4')), None) if has_final_video else None
    has_any_media = len(final_media) > 0 

    if prev_dyn_id:
        real_prev_dyn_id = prev_dyn_id
        
        if isinstance(prev_dyn_id, str) and prev_dyn_id.startswith("BV"):
            resolved_id = await get_dynamic_id_by_bvid(prev_dyn_id)
            if resolved_id:
                real_prev_dyn_id = resolved_id
                logger.info(f"   -> 🎯 [动态猎犬] 成功将 {prev_dyn_id} 反查为动态 ID: {real_prev_dyn_id}")
            else:
                logger.warning(f"   -> ⚠️ [动态猎犬] 反查 {prev_dyn_id} 失败，将被迫执行降级发布。")

        # 👇 注入点：全息溯源文本拼接 (完美避开 B站吞卡片)
        context_suffix = build_repost_context(prev_tw_id, dyn_map, settings)
        final_content += context_suffix

        if has_any_media or str(real_prev_dyn_id).startswith("BV"):
            ref_link = f"https://www.bilibili.com/video/{prev_dyn_id}" if str(prev_dyn_id).startswith("BV") else f"https://t.bilibili.com/{prev_dyn_id}"
            
            if is_leaf_reply:
                final_content += f"\n\n(🔗 溯源: {ref_link})"
            else:
                final_content += f"\n\n🔗 溯源: {ref_link}"
                
            if vid_path:
                logger.info("   -> 🆕 [智能降级] 含媒体/反查拦截，转为独立视频投稿 (附溯源)...")
                if id_retention_level >= 2:
                    final_content = final_content.replace(f"\n\n{tw_id}\n-由GloBot驱动", "").replace(f"\n\n{tw_id}", "")
                success, new_dyn_id = await upload_video_bilibili(vid_path, display_name[:80] if not is_leaf_reply else f"{display_name}的视频回复", final_content, final_source_url, settings)
            else:
                logger.info("   -> 🆕 [智能降级] 源头为视频/包含媒体，转为独立图文动态 (附视频链接)...")
                success, new_dyn_id = await smart_publish(final_content, final_media, video_type=video_type)
        else:
            logger.info(f"   -> ♻️ 触发成员原生纯文本转发动作...")
            success, new_dyn_id = await smart_repost(final_content, real_prev_dyn_id)
    else:
        if vid_path:
            logger.info("   -> 移交视频投稿中枢...")
            if id_retention_level >= 2:
                final_content = final_content.replace(f"\n\n{tw_id}\n-由GloBot驱动", "").replace(f"\n\n{tw_id}", "")
            success, new_dyn_id = await upload_video_bilibili(vid_path, display_name[:80] if not is_leaf_reply else f"{display_name}的视频回复", final_content, final_source_url, settings)
        else:
            logger.info("   -> 移交图文首发中枢 (降级处理)...")
            success, new_dyn_id = await smart_publish(final_content, final_media, video_type=video_type)
        
    cleanup_media(final_media)
    return success, new_dyn_id


async def pipeline_loop():
    logger.info("🤖 GloBot 工业流水线已启动...")
    
    await start_telegram_bot()
    
    is_first_run = not FIRST_RUN_FLAG_FILE.exists()
    history_set = load_history()
    dyn_map = load_dyn_map()
    last_cleanup_time = 0
    
    if is_first_run: logger.warning("🚨 检测到首次部署！首发截断保护机制已就绪。")
    
    while True:
        try:
            # 👇 阀门卡口
            await GloBotState.is_running.wait()

            if time.time() - last_cleanup_time > 12 * 3600:
                retention = getattr(settings.system, 'media_retention_days', 2.0)
                cleanup_old_media(retention_days=retention)
                last_cleanup_time = time.time()

            logger.info("\n📡 启动爬虫嗅探...")
            await fetch_timeline()
            
            json_files = list(RAW_DIR.glob("*.json"))
            if not json_files:
                logger.info("💤 未发现 JSON 矿石，休眠 60 秒...")
                GloBotState.is_sleeping = True
                GloBotState.wake_up_event.clear()
                try:
                    await asyncio.wait_for(GloBotState.wake_up_event.wait(), timeout=60)
                    logger.info("⚡ 收到强制唤醒信号，提前结束休眠！")
                except asyncio.TimeoutError:
                    pass
                finally:
                    GloBotState.is_sleeping = False
                continue
                
            latest_json = max(json_files, key=os.path.getmtime)
            new_tweets = await parse_timeline_json(latest_json)
            
            for jf in json_files:
                if jf.name != latest_json.name:
                    try: jf.unlink()
                    except: pass
            
            if not new_tweets:
                sleep_time = random.randint(240, 420)
                logger.info(f"💤 无新动态，休眠 {sleep_time} 秒...")
                GloBotState.is_sleeping = True
                GloBotState.wake_up_event.clear()
                try:
                    await asyncio.wait_for(GloBotState.wake_up_event.wait(), timeout=sleep_time)
                    logger.info("⚡ 收到强制唤醒信号，提前结束休眠！")
                except asyncio.TimeoutError:
                    pass
                finally:
                    GloBotState.is_sleeping = False
                continue
                
            new_tweets.sort(key=lambda x: x['timestamp'])
            
            if is_first_run:
                for t in new_tweets[:-1]: history_set.add(str(t['id']))
                save_history(history_set)
                new_tweets = [new_tweets[-1]]
                FIRST_RUN_FLAG_FILE.touch()
                is_first_run = False

            # ==========================================
            # 🌟 三段式架构：Stage 1 & 2 (全局拓扑去重与受控并发预处理)
            # ==========================================
            unique_nodes = {}
            for tweet in new_tweets:
                for anc in tweet.get('quote_chain', []):
                    anc_id = str(anc['id'])
                    if not anc.get('is_placeholder') and anc_id not in dyn_map and anc_id not in unique_nodes:
                        unique_nodes[anc_id] = anc
                
                # 🚨 核心修复：叶子节点必须进缓存池！无论是否在 dyn_map 里。
                tw_id = str(tweet['id'])
                if tw_id not in unique_nodes:
                    unique_nodes[tw_id] = tweet

            preprocessing_cache = {}
            if unique_nodes:
                logger.info(f"\n" + "="*50)
                logger.info(f"⚡ [并发车间] 第一阶段扫描完成，提取出 {len(unique_nodes)} 个独立纯净任务。")
                logger.info(f"⚡ [并发车间] 正在启动 Stage 2：细粒度并发翻译与压制 (llm_sem=5, comp_sem=2)...")
                
                llm_sem = asyncio.Semaphore(5)
                comp_sem = asyncio.Semaphore(2)

                async def process_one(node):
                    node_id = str(node['id'])
                    async with llm_sem:
                        trans = await translate_text(node['text'])
                    async with comp_sem:
                        f_media, v_type = await process_media_files(node.get('media', []))
                    preprocessing_cache[node_id] = {
                        'translated_text': trans,
                        'final_media': f_media,
                        'video_type': v_type
                    }

                await asyncio.gather(*(process_one(n) for n in unique_nodes.values()))
                logger.info(f"✅ [并发车间] 所有节点内存处理完毕！总耗时极大缩短，准备进入 Stage 3 串行发射井...")

            # ==========================================
            # 🌟 三段式架构：Stage 3 (极其宁静且安全的串行发包)
            # ==========================================
            total = len(new_tweets)
            for i, tweet in enumerate(new_tweets):
                await GloBotState.is_running.wait()
                
                tweet_id = str(tweet['id'])
                
                try:
                    success, new_dyn_id = await process_pipeline(tweet, dyn_map, preprocessing_cache)
                    
                    if success:
                        history_set.add(tweet_id)
                        save_history(history_set)
                        if new_dyn_id:
                            # 📦 第二阶段存储：将叶子节点写入双轨字典
                            dt_str = datetime.fromtimestamp(tweet['timestamp']).strftime("%Y-%m-%d %H:%M:%S")
                            dyn_map[tweet_id] = {
                                "dyn_id": new_dyn_id,
                                "author_handle": tweet['author'],
                                "author_display_name": tweet.get('author_display_name', f"@{tweet['author']}"),
                                "is_reply": tweet.get('is_reply', False),
                                "dt_str": dt_str,
                                "translated_text": preprocessing_cache[tweet_id]['translated_text'],
                                "raw_text": html.unescape(tweet['text'])
                            }
                            save_dyn_map(dyn_map)
                        logger.info(f"✅ 任务 {i+1}/{total} [{tweet_id}] 成功发射！")
                        GloBotState.daily_stats['success'] += 1 
                        
                        if not str(new_dyn_id).startswith("BV"): 
                            await send_tg_msg(f"🎉 <b>图文搬运成功</b> [{i+1}/{total}]\n推特源: <code>{tweet_id}</code>\n成功生成 B站动态: <code>{new_dyn_id}</code>")

                    else:
                        logger.error(f"❌ 推文 {tweet_id} 发布失败！")
                        GloBotState.daily_stats['failed'] += 1   
                        await send_tg_msg(f"❌ <b>搬运受阻</b> [{i+1}/{total}]\n推特源: <code>{tweet_id}</code>\n未能成功发布，请检查终端日志排查。")
                        continue
                        
                except RuntimeError as e: 
                    if "AUTH_EXPIRED" in str(e):
                        logger.critical(f"🛑 [熔断机制] 侦测到凭证失效，强行切断流水线: {e}")
                        GloBotState.is_running.clear() 
                        await send_tg_error(f"🛑 <b>安全熔断机制触发！</b>\n\n检测到账号令牌失效或被拦截：\n<code>{e}</code>\n\n为防止无限重试导致死封，流水线已<b>强制物理挂起</b>。\n👉 请在终端运行 `python Bot_Publisher/bili_login.py` 重新扫码，更新凭证后发送 <code>/resume</code> 恢复运行。")
                        break 
                    else:
                        err_trace = traceback.format_exc()
                        logger.error(f"🔥 处理推文 {tweet_id} 时发生运行时异常: {e}")
                        await send_tg_error(f"处理推文崩溃:\n{err_trace[-300:]}")
                        GloBotState.daily_stats['failed'] += 1
                        continue

                except Exception as e:
                    err_trace = traceback.format_exc()
                    logger.error(f"🔥 处理推文 {tweet_id} 时发生内部崩溃: {e}")
                    await send_tg_error(f"处理推文 {tweet_id} 崩溃:\n{err_trace[-300:]}")
                    GloBotState.daily_stats['failed'] += 1
                    continue
                    
                if i < total - 1:
                    logger.warning("⏳ [风控规避] 单个成员任务完成，休眠 65 秒进入下一任务...")
                    await asyncio.sleep(65)
                    
            sleep_time = random.randint(240, 420)
            logger.info(f"✅ 周期巡视完成，深度休眠 {sleep_time} 秒...")
            
            GloBotState.is_sleeping = True
            GloBotState.wake_up_event.clear()
            try:
                await asyncio.wait_for(GloBotState.wake_up_event.wait(), timeout=sleep_time)
                logger.info("⚡ 收到强制唤醒信号，提前结束休眠！")
            except asyncio.TimeoutError:
                pass
            finally:
                GloBotState.is_sleeping = False
            
        except asyncio.CancelledError:
            logger.warning("🛑 爬虫任务已被强制取消。")
            break
        except Exception as e:
            err_trace = traceback.format_exc()
            logger.error(f"🔥 总线发生未捕获异常: {e}")
            await send_tg_error(f"总线挂机大崩溃:\n{err_trace[-400:]}")
            await asyncio.sleep(60)

async def main_master():
    logger.info("🤖 初始化 Telegram 中枢...")
    GloBotState.main_loop_coro = pipeline_loop
    await start_telegram_bot()
    GloBotState.crawler_task = asyncio.create_task(pipeline_loop())
    await send_tg_msg("🟢 <b>GloBot Matrix 已上线</b>\n总线连接正常，默认流水线已自动点火。您可随时通过 <code>/kill</code> 关停。")
    logger.info("🟢 GloBot 主控节点已就绪，正在永久挂起主线程监听指令...")
    while True:
        await asyncio.sleep(86400)

if __name__ == "__main__":
    try: asyncio.run(main_master())
    except KeyboardInterrupt: logger.info("\n🛑 收到主控台切断信号，GloBot 安全停机。")