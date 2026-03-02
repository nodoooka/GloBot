import os
import json
import time
import logging
import asyncio
import random
import html
import shutil  # 👈 新增：用于在被媒体管线吞噬前，提前备份一份生肉视频
from Bot_Master.tg_bot import start_telegram_bot, send_tg_msg, send_tg_error, GloBotState
import traceback
from pathlib import Path
from datetime import datetime

from common.config_loader import settings, BILI_ACCOUNT_NAME
from Bot_Crawler.twitter_scraper import fetch_timeline
from Bot_Crawler.tweet_parser import parse_timeline_json
from Bot_Media.llm_translator import translate_text
from Bot_Media.media_pipeline import dispatch_media
from Bot_Publisher.bili_uploader import smart_publish, smart_repost, get_dynamic_id_by_bvid
from common.text_sanitizer import sanitize_for_bilibili

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("telegram.ext.Updater").setLevel(logging.CRITICAL)

# 👇 新增下面这两行，彻底屏蔽它们的碎碎念
logging.getLogger("apscheduler.scheduler").setLevel(logging.WARNING)
logging.getLogger("telegram.ext.Application").setLevel(logging.WARNING)

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

def cleanup_old_media(retention_days=2.0):
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
                    pass
    for member_dir in media_dir.iterdir():
        if member_dir.is_dir() and not any(member_dir.iterdir()):
            try: member_dir.rmdir()
            except: pass
    if deleted_files > 0:
        logger.info(f"🧹 [空间管理] 触发自动清理！已永久销毁 {deleted_files} 个陈旧媒体文件。")

# 🚨 重构点：返回双版本的字典结构 video_info
async def process_media_files(media_list):
    final_paths = []
    video_info = {"original": None, "translated": None}
    
    for mf in media_list:
        if str(mf).lower().endswith(('.mp4', '.mov')):
            logger.info(f"   -> 正在启动媒体管线压制视频...")
            source_file = Path(mf)
            
            PUBLISH_DIR = DATA_DIR / "ready_to_publish"
            PUBLISH_DIR.mkdir(parents=True, exist_ok=True)
            
            # 🛡️ 在源文件被媒体引擎“阅后即焚”前，提前复制一份作为生肉选项
            orig_file = PUBLISH_DIR / f"orig_{source_file.name}"
            shutil.copy2(source_file, orig_file)
            video_info["original"] = str(orig_file)
            final_paths.append(str(orig_file))
            
            output_file = PUBLISH_DIR / f"final_{source_file.name}"
            
            await dispatch_media(str(source_file))
            if output_file.exists():
                if getattr(settings.media_engine, 'enable_ai_translation', False):
                    video_info["translated"] = str(output_file)
                final_paths.append(str(output_file))
        else:
            final_paths.append(mf)
            
    return final_paths, video_info

def cleanup_media(media_paths):
    for f in media_paths:
        if "ready_to_publish" in str(f):
            try: Path(f).unlink()
            except: pass

def build_repost_context(prev_tw_id, dyn_map, settings_obj, id_retention_level, is_video_mode=False):
    if not prev_tw_id or prev_tw_id not in dyn_map: return ""
    prev_info = dyn_map[prev_tw_id]
    if not isinstance(prev_info, dict): return "" 
        
    p_mode = prev_info.get("publish_mode", "repost")
    if p_mode == "original": return "" 
        
    p_handle = prev_info.get("author_handle", "")
    p_disp = prev_info.get("author_display_name", p_handle)
    p_node_type = prev_info.get("node_type", "ORIGINAL")
    p_dt = prev_info.get("dt_str", "")
    p_trans = prev_info.get("translated_text", "")
    p_raw = prev_info.get("raw_text", "")
    
    p_name = settings_obj.targets.account_title_map.get(p_handle, p_disp)
    my_account = BILI_ACCOUNT_NAME
    
    if is_video_mode:
        c_trans_p = p_trans.replace('\n', ' ')
        if len(c_trans_p) > 25: c_trans_p = c_trans_p[:25] + "..."
        if p_node_type == 'REPLY':
            return f"\n//@{my_account}: 💬{p_name}回复: {c_trans_p}"
        else:
            return f"\n//@{my_account}: {p_name}: {c_trans_p}"
    else:
        if p_node_type == 'REPLY':
            c_trans_p = p_trans.replace('\n', ' ')
            c_raw_p = p_raw.replace('\n', ' ')
            return f"\n//@{my_account}: 💬{p_name}回复说： {c_trans_p} 【原文】 {c_raw_p}"
        else:
            retention_str = ""
            if id_retention_level < 3:
                retention_str = f"\n\n{prev_tw_id}\n-由GloBot驱动"
            return f"\n//@{my_account}: {p_name}\n\n{p_dt}\n\n{p_trans}\n\n【原文】\n{p_raw}{retention_str}"

def build_safe_dynamic_text(c_name, c_time, c_trans, c_raw, c_id, c_node_type, ret_level, context_suffix, ref_link, limit):
    if c_node_type == 'RETWEET':
        text = f"{c_name} 转发\n{c_time}"
        if context_suffix: text += context_suffix
        if ref_link: text += f"\n\n🔗 溯源: {ref_link}"
        if ret_level < 3: text += f"\n\n{c_id}\n-由GloBot驱动"
        return sanitize_for_bilibili(text[:limit])

    def assemble(include_tail, include_raw, truncate_trans_len=None):
        res = f"💬{c_name}回复说：\n" if c_node_type == 'REPLY' else f"{c_time}\n\n"
            
        if truncate_trans_len is not None:
            res += c_trans[:truncate_trans_len] + "..."
        else:
            res += c_trans
            
        if include_raw:
            if c_raw:
                res += f"\n\n(原文: {c_raw})" if c_node_type == 'REPLY' else f"\n\n【原文】\n{c_raw}"
        elif c_raw:
            res += "\n\n(原文过长已被截断)" if c_node_type == 'REPLY' else "\n\n【原文】\n...(日文原文过长，已被自动截断)"
                
        if context_suffix:
            res += context_suffix
            
        if ref_link:
            res += f"\n\n(🔗 溯源: {ref_link})" if c_node_type == 'REPLY' else f"\n\n🔗 溯源: {ref_link}"
                
        if include_tail and ret_level < 3:
            res += f"\n\n{c_id}"
            if c_node_type != 'REPLY':
                res += "\n-由GloBot驱动"
                
        return sanitize_for_bilibili(res)

    t0 = assemble(True, True)
    if len(t0) <= limit: return t0
    
    t1 = assemble(False, True)
    if len(t1) <= limit: return t1
    
    t2 = assemble(False, False)
    if len(t2) <= limit: return t2
    
    fixed_len = len(assemble(False, False, truncate_trans_len=0))
    avail = limit - fixed_len - 5
    if avail > 0:
        return assemble(False, False, truncate_trans_len=avail)
    else:
        return t2[:limit-3] + "..."

async def process_pipeline(tweet: dict, dyn_map: dict, preprocessing_cache: dict) -> tuple[bool, str, str]:
    logger.info(f"\n" + "="*50)
    logger.info(f"🚀 开始处理推文树... 目标终点成员: @{tweet['author']}")
    
    id_retention_level = getattr(settings.publishers.bilibili, 'tweet_id_retention', 0)
    prev_dyn_id = None
    prev_tw_id = None 
    
    for ancestor in tweet.get('quote_chain', []):
        anc_id = str(ancestor['id'])
        is_placeholder = ancestor.get('is_placeholder', False)
        
        if anc_id in dyn_map:
            prev_info = dyn_map[anc_id]
            if isinstance(prev_info, dict): prev_dyn_id = prev_info.get("dyn_id")
            else: prev_dyn_id = prev_info
            prev_tw_id = anc_id
            logger.info(f"   -> ♻️ 记忆寻址命中：祖先节点 {anc_id} 已搬运，跳过首发，将其作为套娃基底。")
            continue
            
        if is_placeholder:
            logger.info(f"   -> ⚠️ 祖先节点 {anc_id} 仅为防断链占位符且无记忆，跳过强行发布。")
            continue
            
        logger.info(f"   -> ⛓️ 发现全新未搬运的祖先节点！开始穿透发布: @{ancestor['author']}")
        
        anc_node_type = ancestor.get('node_type', 'ORIGINAL')
        anc_translated = preprocessing_cache[anc_id]['translated_text']
        dt_str = datetime.fromtimestamp(ancestor['timestamp']).strftime("%Y-%m-%d %H:%M:%S")
        clean_raw = html.unescape(ancestor['text'])
        author_handle = ancestor['author']
        author_display = ancestor.get('author_display_name', f"@{author_handle}")
        display_name = settings.targets.account_title_map.get(author_handle, author_display)
        
        anc_media = preprocessing_cache[anc_id]['final_media']
        anc_video_info = preprocessing_cache[anc_id].get('video_info', {"original": None, "translated": None})
        anc_source_url = f"https://x.com/{ancestor['author']}/status/{anc_id}"
        
        allow_trans = settings.publishers.bilibili.publish_translated_video
        allow_orig = settings.publishers.bilibili.publish_original_video
        
        avail_trans = anc_video_info.get("translated") if allow_trans else None
        avail_orig = anc_video_info.get("original") if allow_orig else None
        
        vid_candidates = {"translated": avail_trans, "original": avail_orig}
        has_anc_video = bool(avail_trans or avail_orig)
        anc_video_type = "translated" if avail_trans else "original" if avail_orig else "none"
        has_any_media = len(anc_media) > 0
        
        is_video_route = False
        fallback_to_publish = False
        ref_link = ""
        curr_publish_mode = "original"

        if prev_dyn_id:
            real_prev_dyn_id = prev_dyn_id
            if isinstance(prev_dyn_id, str) and prev_dyn_id.startswith("BV"):
                resolved_id = await get_dynamic_id_by_bvid(prev_dyn_id)
                if resolved_id: real_prev_dyn_id = resolved_id
                else: logger.warning(f"   -> ⚠️ [动态猎犬] 反查失败。")

            fallback_to_publish = has_any_media or str(real_prev_dyn_id).startswith("BV")
            if fallback_to_publish:
                is_video_route = has_anc_video
                ref_link = f"https://www.bilibili.com/video/{prev_dyn_id}" if str(prev_dyn_id).startswith("BV") else f"https://t.bilibili.com/{prev_dyn_id}"
                curr_publish_mode = "original"
            else:
                is_video_route = False
                curr_publish_mode = "repost"
        else:
            is_video_route = has_anc_video
            curr_publish_mode = "original"

        limit = 220 if is_video_route else 950
        context_suffix = build_repost_context(prev_tw_id, dyn_map, settings, id_retention_level, is_video_mode=is_video_route)
        settings.publishers.bilibili.title = "" if anc_node_type in ["REPLY", "RETWEET"] else display_name

        anc_content = build_safe_dynamic_text(
            c_name=display_name, c_time=dt_str, c_trans=anc_translated, c_raw=clean_raw, c_id=anc_id,
            c_node_type=anc_node_type, ret_level=id_retention_level, 
            context_suffix=context_suffix, ref_link=ref_link, limit=limit
        )

        if prev_dyn_id:
            if fallback_to_publish:
                if has_anc_video:
                    logger.info(f"   -> 🆕 [智能降级] 含媒体/反查拦截，转为独立视频投稿 (附溯源)...")
                    success, new_anc_dyn_id = await upload_video_bilibili(vid_candidates, display_name[:80] if anc_node_type != 'REPLY' else f"{display_name}的视频回复", anc_content, anc_source_url, settings)
                else:
                    logger.info(f"   -> 🆕 [智能降级] 含媒体/反查拦截，转为独立图文动态 (附溯源)...")
                    success, new_anc_dyn_id = await smart_publish(anc_content, anc_media, video_type=anc_video_type)
            else:
                logger.info(f"   -> 🔄 触发 B 站原生纯文本转发机制...")
                success, new_anc_dyn_id = await smart_repost(anc_content, real_prev_dyn_id)
        else:
            if has_anc_video:
                logger.info(f"   -> 🆕 [祖先节点] 移交视频投稿中枢...")
                success, new_anc_dyn_id = await upload_video_bilibili(vid_candidates, display_name[:80] if anc_node_type != 'REPLY' else f"{display_name}的视频回复", anc_content, anc_source_url, settings)
            else:
                logger.info(f"   -> 🆕 正在将推文树的最底层根节点进行首发...")
                success, new_anc_dyn_id = await smart_publish(anc_content, anc_media, video_type=anc_video_type)
            
        cleanup_media(anc_media)
        
        if success and new_anc_dyn_id:
            dyn_map[anc_id] = {
                "dyn_id": new_anc_dyn_id, "author_handle": author_handle, "author_display_name": author_display,
                "node_type": anc_node_type, "dt_str": dt_str, "translated_text": anc_translated, "raw_text": clean_raw, "publish_mode": curr_publish_mode
            }
            save_dyn_map(dyn_map)
            prev_dyn_id = new_anc_dyn_id
            prev_tw_id = anc_id
            logger.warning("   -> ⏳ [风控规避] 祖先节点发射成功，强制开启 65 秒冷却通道...")
            await asyncio.sleep(65)
        else:
            logger.error(f"❌ 引用/回复 节点链条断裂，发布终止！")
            return False, "", "repost"

    # ==========================================
    # 👑 第二阶段：处理成员的最终点评 (叶子节点)
    # ==========================================
    logger.info(f"   -> 👑 链路穿透完成，开始处理最终成员点评！")
    
    tw_id = str(tweet['id'])
    tw_node_type = tweet.get('node_type', 'ORIGINAL')
    
    author_handle = tweet['author']
    display_name = settings.targets.account_title_map.get(author_handle, tweet.get('author_display_name', f"@{author_handle}"))
    dt_str = datetime.fromtimestamp(tweet['timestamp']).strftime("%Y-%m-%d %H:%M:%S")
    
    translated_text = "" if tw_node_type == 'RETWEET' else preprocessing_cache[tw_id]['translated_text']
    clean_raw_text = "" if tw_node_type == 'RETWEET' else html.unescape(tweet['text'])
    
    final_media = [] if tw_node_type == 'RETWEET' else preprocessing_cache[tw_id]['final_media']
    tw_video_info = {} if tw_node_type == 'RETWEET' else preprocessing_cache[tw_id].get('video_info', {"original": None, "translated": None})
    final_source_url = f"https://x.com/{tweet['author']}/status/{tw_id}"

    allow_trans = settings.publishers.bilibili.publish_translated_video
    allow_orig = settings.publishers.bilibili.publish_original_video

    avail_trans = tw_video_info.get("translated") if allow_trans else None
    avail_orig = tw_video_info.get("original") if allow_orig else None

    vid_candidates = {"translated": avail_trans, "original": avail_orig}
    has_final_video = bool(avail_trans or avail_orig)
    leaf_video_type = "translated" if avail_trans else "original" if avail_orig else "none"
    has_any_media = len(final_media) > 0 

    is_video_route = False
    fallback_to_publish = False
    ref_link = ""
    curr_publish_mode = "original"

    if prev_dyn_id:
        real_prev_dyn_id = prev_dyn_id
        if isinstance(prev_dyn_id, str) and prev_dyn_id.startswith("BV"):
            resolved_id = await get_dynamic_id_by_bvid(prev_dyn_id)
            if resolved_id: real_prev_dyn_id = resolved_id

        fallback_to_publish = has_any_media or str(real_prev_dyn_id).startswith("BV")
        if fallback_to_publish:
            is_video_route = has_final_video
            ref_link = f"https://www.bilibili.com/video/{prev_dyn_id}" if str(prev_dyn_id).startswith("BV") else f"https://t.bilibili.com/{prev_dyn_id}"
            curr_publish_mode = "original"
        else:
            is_video_route = False
            curr_publish_mode = "repost"
    else:
        is_video_route = has_final_video
        curr_publish_mode = "original"

    limit = 220 if is_video_route else 950
    context_suffix = build_repost_context(prev_tw_id, dyn_map, settings, id_retention_level, is_video_mode=is_video_route)
    settings.publishers.bilibili.title = "" if tw_node_type in ["REPLY", "RETWEET"] else display_name

    final_content = build_safe_dynamic_text(
        c_name=display_name, c_time=dt_str, c_trans=translated_text, c_raw=clean_raw_text, c_id=tw_id,
        c_node_type=tw_node_type, ret_level=id_retention_level, 
        context_suffix=context_suffix, ref_link=ref_link, limit=limit
    )

    if prev_dyn_id:
        if fallback_to_publish:
            if has_final_video:
                logger.info("   -> 🆕 [智能降级] 含媒体/反查拦截，转为独立视频投稿 (附溯源)...")
                success, new_dyn_id = await upload_video_bilibili(vid_candidates, display_name[:80] if tw_node_type != 'REPLY' else f"{display_name}的视频回复", final_content, final_source_url, settings)
            else:
                logger.info("   -> 🆕 [智能降级] 源头为视频/包含媒体，转为独立图文动态 (附视频链接)...")
                success, new_dyn_id = await smart_publish(final_content, final_media, video_type=leaf_video_type)
        else:
            logger.info(f"   -> ♻️ 触发成员原生纯文本转发动作...")
            success, new_dyn_id = await smart_repost(final_content, real_prev_dyn_id)
    else:
        if has_final_video:
            logger.info("   -> 移交视频投稿中枢...")
            success, new_dyn_id = await upload_video_bilibili(vid_candidates, display_name[:80] if tw_node_type != 'REPLY' else f"{display_name}的视频回复", final_content, final_source_url, settings)
        else:
            logger.info("   -> 移交图文首发中枢 (降级处理)...")
            success, new_dyn_id = await smart_publish(final_content, final_media, video_type=leaf_video_type)
        
    cleanup_media(final_media)
    return success, new_dyn_id, curr_publish_mode

async def pipeline_loop():
    logger.info("🤖 GloBot 工业流水线已启动...")
    # 👇 删掉或注释掉下面这一行，把它全权交给外层的 main_master 管理！
    # await start_telegram_bot()
    is_first_run = not FIRST_RUN_FLAG_FILE.exists()
    history_set = load_history()
    dyn_map = load_dyn_map()
    last_cleanup_time = 0
    if is_first_run: logger.warning("🚨 检测到首次部署！首发截断保护机制已就绪。")
    
    while True:
        try:
            await GloBotState.is_running.wait()
            
            sleep_cfg = settings.crawlers.global_settings.sleep_schedule
            if sleep_cfg.enable:
                try:
                    now_dt = datetime.now()
                    curr_time = now_dt.time()
                    t_start = datetime.strptime(sleep_cfg.start_time, "%H:%M").time()
                    t_end = datetime.strptime(sleep_cfg.end_time, "%H:%M").time()
                    
                    is_sleeping_time = False
                    if t_start <= t_end:
                        is_sleeping_time = t_start <= curr_time <= t_end
                    else:
                        is_sleeping_time = curr_time >= t_start or curr_time <= t_end
                        
                    if is_sleeping_time:
                        logger.info(f"🌙 触发仿生休眠期 ({sleep_cfg.start_time} - {sleep_cfg.end_time})，系统进入深度蛰伏...")
                        GloBotState.is_sleeping = True
                        GloBotState.wake_up_event.clear()
                        try: 
                            await asyncio.wait_for(GloBotState.wake_up_event.wait(), timeout=600)
                            logger.info("⚡ 收到强制唤醒信号，提前结束蛰伏！")
                        except asyncio.TimeoutError: 
                            pass
                        finally: 
                            GloBotState.is_sleeping = False
                        continue
                except Exception as e:
                    logger.error(f"⚠️ 作息时间解析异常，忽略休眠: {e}")

            if time.time() - last_cleanup_time > 12 * 3600:
                cleanup_old_media(getattr(settings.system, 'media_retention_days', 2.0))
                last_cleanup_time = time.time()

            logger.info("\n📡 启动爬虫嗅探...")
            
            try:
                await fetch_timeline()
            except RuntimeError as e:
                if "TWITTER_AUTH_EXPIRED" in str(e):
                    logger.critical(f"🛑 [熔断机制] 侦测到推特账号异常，强行切断流水线: {e}")
                    GloBotState.is_running.clear()
                    await send_tg_error(f"🛑 <b>推特账号疑似被风控！</b>\n\n异常追踪：\n<code>{e}</code>\n\n为防止被死封，爬虫流水线已<b>物理挂起</b>。\n👉 请确认账号存活，如果有必要请重新运行登录脚本更新 cookie，之后发送 /resume 恢复运行。")
                    continue
                else: raise e
                
            json_files = list(RAW_DIR.glob("*.json"))
            if not json_files:
                logger.info("💤 未发现 JSON 矿石，休眠 60 秒...")
                GloBotState.is_sleeping = True
                GloBotState.wake_up_event.clear()
                try: await asyncio.wait_for(GloBotState.wake_up_event.wait(), timeout=60)
                except asyncio.TimeoutError: pass
                finally: GloBotState.is_sleeping = False
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
                try: await asyncio.wait_for(GloBotState.wake_up_event.wait(), timeout=sleep_time)
                except asyncio.TimeoutError: pass
                finally: GloBotState.is_sleeping = False
                continue
                
            new_tweets.sort(key=lambda x: x['timestamp'])
            if is_first_run:
                for t in new_tweets[:-1]: history_set.add(str(t['id']))
                save_history(history_set)
                new_tweets = [new_tweets[-1]]
                FIRST_RUN_FLAG_FILE.touch()
                is_first_run = False

            unique_nodes = {}
            for tweet in new_tweets:
                for anc in tweet.get('quote_chain', []):
                    anc_id = str(anc['id'])
                    if not anc.get('is_placeholder') and anc_id not in dyn_map and anc_id not in unique_nodes:
                        unique_nodes[anc_id] = anc
                tw_id = str(tweet['id'])
                if tweet.get('node_type') != 'RETWEET':
                    if tw_id not in unique_nodes: unique_nodes[tw_id] = tweet

            preprocessing_cache = {}
            if unique_nodes:
                logger.info(f"\n" + "="*50)
                logger.info(f"⚡ [并发车间] 第一阶段扫描完成，提取出 {len(unique_nodes)} 个独立纯净任务。")
                llm_sem = asyncio.Semaphore(5)
                comp_sem = asyncio.Semaphore(2)

                async def process_one(node):
                    node_id = str(node['id'])
                    async with llm_sem: trans = await translate_text(node['text'])
                    async with comp_sem: f_media, v_info = await process_media_files(node.get('media', []))
                    preprocessing_cache[node_id] = {'translated_text': trans, 'final_media': f_media, 'video_info': v_info}

                try:
                    await asyncio.gather(*(process_one(n) for n in unique_nodes.values()))
                except RuntimeError as e:
                    if "LLM_TRANSLATION_FAILED" in str(e):
                        logger.critical(f"🛑 [熔断机制] 侦测到大模型翻译引擎宕机: {e}")
                        GloBotState.is_running.clear()
                        await send_tg_error(f"🛑 <b>大模型发生 T0 级宕机！</b>\n\n<code>{e}</code>\n\n为防生肉流出，GloBot 主阀门已锁定。\n👉 请确认修复后发送 /resume 恢复。")
                        continue  
                    else: raise e

            total = len(new_tweets)
            for i, tweet in enumerate(new_tweets):
                await GloBotState.is_running.wait()
                tweet_id = str(tweet['id'])
                try:
                    success, new_dyn_id, leaf_publish_mode = await process_pipeline(tweet, dyn_map, preprocessing_cache)
                    if success:
                        history_set.add(tweet_id)
                        save_history(history_set)
                        if new_dyn_id:
                            dt_str = datetime.fromtimestamp(tweet['timestamp']).strftime("%Y-%m-%d %H:%M:%S")
                            leaf_node_type = tweet.get('node_type', 'ORIGINAL')
                            
                            t_trans = "" if leaf_node_type == 'RETWEET' else preprocessing_cache[tweet_id]['translated_text']
                            t_raw = "" if leaf_node_type == 'RETWEET' else html.unescape(tweet['text'])
                            
                            dyn_map[tweet_id] = {
                                "dyn_id": new_dyn_id, "author_handle": tweet['author'], "author_display_name": tweet.get('author_display_name', f"@{tweet['author']}"),
                                "node_type": leaf_node_type, "dt_str": dt_str, "translated_text": t_trans, "raw_text": t_raw, "publish_mode": leaf_publish_mode
                            }
                            save_dyn_map(dyn_map)
                        logger.info(f"✅ 任务 {i+1}/{total} [{tweet_id}] 成功发射！")
                        GloBotState.daily_stats['success'] += 1 
                        if not str(new_dyn_id).startswith("BV"): 
                            await send_tg_msg(f"🎉 <b>图文搬运成功</b> [{i+1}/{total}]\n推特源: <code>{tweet_id}</code>\nB站动态: <code>{new_dyn_id}</code>")
                    else:
                        logger.error(f"❌ 推文 {tweet_id} 发布失败！")
                        GloBotState.daily_stats['failed'] += 1   
                        await send_tg_msg(f"❌ <b>搬运受阻</b> [{i+1}/{total}]\n推特源: <code>{tweet_id}</code>\n未能成功发布。")
                        continue
                except RuntimeError as e: 
                    if "AUTH_EXPIRED" in str(e):
                        logger.critical(f"🛑 [熔断机制] 侦测到凭证失效，强行切断流水线: {e}")
                        GloBotState.is_running.clear() 
                        await send_tg_error(f"🛑 <b>安全熔断机制触发！</b>\n\n<code>{e}</code>\n\n为防死封，流水线已<b>物理挂起</b>。\n👉 重新扫码后发送 /resume。")
                        break 
                    else:
                        logger.error(f"🔥 处理推文 {tweet_id} 时发生运行时异常: {e}")
                        GloBotState.daily_stats['failed'] += 1
                        continue
                except Exception as e:
                    logger.error(f"🔥 处理推文 {tweet_id} 时发生内部崩溃: {e}")
                    GloBotState.daily_stats['failed'] += 1
                    continue
                    
                if i < total - 1:
                    logger.warning("⏳ [风控规避] 单个成员任务完成，休眠 65 秒进入下一任务...")
                    await asyncio.sleep(65)
                    
            sleep_time = random.randint(240, 420)
            logger.info(f"✅ 周期巡视完成，深度休眠 {sleep_time} 秒...")
            GloBotState.is_sleeping = True
            GloBotState.wake_up_event.clear()
            try: await asyncio.wait_for(GloBotState.wake_up_event.wait(), timeout=sleep_time)
            except asyncio.TimeoutError: pass
            finally: GloBotState.is_sleeping = False
            
        except asyncio.CancelledError: break
        except Exception as e:
            logger.error(f"🔥 总线发生未捕获异常: {e}")
            await asyncio.sleep(60)

async def main_master():
    logger.info("🤖 初始化 Telegram 中枢...")
    GloBotState.main_loop_coro = pipeline_loop
    await start_telegram_bot()
    GloBotState.crawler_task = asyncio.create_task(pipeline_loop())
    await send_tg_msg("🟢 <b>GloBot Matrix 已上线</b>")
    while True: await asyncio.sleep(86400)

if __name__ == "__main__":
    try: asyncio.run(main_master())
    except KeyboardInterrupt: logger.info("\n🛑 安全停机。")