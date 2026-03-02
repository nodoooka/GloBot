import os
import time
import logging
import asyncio
import random
import html
from pathlib import Path
from datetime import datetime

# 1. 核心底座与中枢
from common.config_loader import settings
from common.state_manager import load_history, save_history, load_dyn_map, save_dyn_map
from Bot_Master.tg_bot import start_telegram_bot, send_tg_msg, send_tg_error, GloBotState

# 2. 爬虫嗅探引擎
from Bot_Crawler.twitter_scraper import fetch_timeline
from Bot_Crawler.tweet_parser import parse_timeline_json

# 3. 多模态处理引擎
from Bot_Media.llm_translator import translate_text
from Bot_Media.media_pipeline import process_media_files, cleanup_media, cleanup_old_media

# 4. 发布与排版引擎
from Bot_Publisher.bili_formatter import build_safe_dynamic_text, build_repost_context
from Bot_Publisher.bili_uploader import smart_publish, smart_repost, get_dynamic_id_by_bvid
from Bot_Publisher.bili_video_uploader import upload_video_bilibili 

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("telegram.ext.Updater").setLevel(logging.CRITICAL)
logging.getLogger("apscheduler.scheduler").setLevel(logging.WARNING)
logging.getLogger("telegram.ext.Application").setLevel(logging.WARNING)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("GloBot_Main")

DATA_DIR = Path(os.getenv("LOCAL_DATA_DIR", f"./GloBot_Data/{settings.targets.group_name}"))
RAW_DIR = DATA_DIR / "timeline_raw"
FIRST_RUN_FLAG_FILE = DATA_DIR / ".first_run_completed"

async def process_pipeline(tweet: dict, dyn_map: dict, preprocessing_cache: dict) -> tuple[bool, str, str]:
    logger.info(f"\n" + "="*50)
    logger.info(f"🚀 开始处理推文树... 目标终点成员: @{tweet['author']}")
    
    id_retention_level = getattr(settings.publishers.bilibili, 'tweet_id_retention', 0)
    prev_dyn_id, prev_tw_id = None, None 
    
    for ancestor in tweet.get('quote_chain', []):
        anc_id = str(ancestor['id'])
        if anc_id in dyn_map:
            prev_info = dyn_map[anc_id]
            prev_dyn_id = prev_info.get("dyn_id") if isinstance(prev_info, dict) else prev_info
            prev_tw_id = anc_id
            logger.info(f"   -> ♻️ 记忆寻址命中：祖先节点 {anc_id} 已搬运，跳过首发，将其作为套娃基底。")
            continue
            
        if ancestor.get('is_placeholder', False):
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
        
        vid_candidates = {"translated": anc_video_info.get("translated") if settings.publishers.bilibili.publish_translated_video else None, 
                          "original": anc_video_info.get("original") if settings.publishers.bilibili.publish_original_video else None}
        has_anc_video = bool(vid_candidates["translated"] or vid_candidates["original"])
        anc_video_type = "translated" if vid_candidates["translated"] else "original" if vid_candidates["original"] else "none"
        
        fallback_to_publish, curr_publish_mode = False, "original"

        if prev_dyn_id:
            real_prev_dyn_id = prev_dyn_id
            if isinstance(prev_dyn_id, str) and prev_dyn_id.startswith("BV"):
                resolved_id = await get_dynamic_id_by_bvid(prev_dyn_id)
                if resolved_id: real_prev_dyn_id = resolved_id
                else: logger.warning(f"   -> ⚠️ [动态猎犬] 反查失败。")

            fallback_to_publish = (len(anc_media) > 0) or str(real_prev_dyn_id).startswith("BV")
            if not fallback_to_publish: curr_publish_mode = "repost"
                
        is_video_route = has_anc_video if (not prev_dyn_id or fallback_to_publish) else False
        limit = 220 if is_video_route else 950
        ref_link = f"https://www.bilibili.com/video/{prev_dyn_id}" if prev_dyn_id and str(prev_dyn_id).startswith("BV") else f"https://t.bilibili.com/{prev_dyn_id}" if prev_dyn_id else ""
        
        context_suffix = build_repost_context(prev_tw_id, dyn_map, settings, id_retention_level, is_video_mode=is_video_route)
        settings.publishers.bilibili.title = "" if anc_node_type in ["REPLY", "RETWEET"] else display_name

        anc_content = build_safe_dynamic_text(
            display_name, dt_str, anc_translated, clean_raw, anc_id, anc_node_type, 
            id_retention_level, context_suffix, ref_link if fallback_to_publish else "", limit
        )

        if prev_dyn_id and not fallback_to_publish:
            logger.info(f"   -> 🔄 触发 B 站原生纯文本转发机制...")
            success, new_anc_dyn_id = await smart_repost(anc_content, real_prev_dyn_id)
        else:
            if has_anc_video:
                logger.info(f"   -> 🆕 移交视频投稿中枢...")
                success, new_anc_dyn_id = await upload_video_bilibili(vid_candidates, display_name[:80] if anc_node_type != 'REPLY' else f"{display_name}的视频回复", anc_content, anc_source_url, settings)
            else:
                logger.info(f"   -> 🆕 正在将节点进行首发...")
                success, new_anc_dyn_id = await smart_publish(anc_content, anc_media, video_type=anc_video_type)
            
        cleanup_media(anc_media)
        
        if success and new_anc_dyn_id:
            dyn_map[anc_id] = {
                "dyn_id": new_anc_dyn_id, "author_handle": author_handle, "author_display_name": author_display,
                "node_type": anc_node_type, "dt_str": dt_str, "translated_text": anc_translated, "raw_text": clean_raw, "publish_mode": curr_publish_mode
            }
            save_dyn_map(dyn_map)
            prev_dyn_id, prev_tw_id = new_anc_dyn_id, anc_id
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

    vid_candidates = {"translated": tw_video_info.get("translated") if settings.publishers.bilibili.publish_translated_video else None,
                      "original": tw_video_info.get("original") if settings.publishers.bilibili.publish_original_video else None}
    has_final_video = bool(vid_candidates["translated"] or vid_candidates["original"])
    leaf_video_type = "translated" if vid_candidates["translated"] else "original" if vid_candidates["original"] else "none"

    fallback_to_publish, curr_publish_mode = False, "original"

    if prev_dyn_id:
        real_prev_dyn_id = prev_dyn_id
        if isinstance(prev_dyn_id, str) and prev_dyn_id.startswith("BV"):
            resolved_id = await get_dynamic_id_by_bvid(prev_dyn_id)
            if resolved_id: real_prev_dyn_id = resolved_id

        fallback_to_publish = (len(final_media) > 0) or str(real_prev_dyn_id).startswith("BV")
        if not fallback_to_publish: curr_publish_mode = "repost"

    is_video_route = has_final_video if (not prev_dyn_id or fallback_to_publish) else False
    limit = 220 if is_video_route else 950
    ref_link = f"https://www.bilibili.com/video/{prev_dyn_id}" if prev_dyn_id and str(prev_dyn_id).startswith("BV") else f"https://t.bilibili.com/{prev_dyn_id}" if prev_dyn_id else ""

    context_suffix = build_repost_context(prev_tw_id, dyn_map, settings, id_retention_level, is_video_mode=is_video_route)
    settings.publishers.bilibili.title = "" if tw_node_type in ["REPLY", "RETWEET"] else display_name

    final_content = build_safe_dynamic_text(
        display_name, dt_str, translated_text, clean_raw_text, tw_id, tw_node_type, 
        id_retention_level, context_suffix, ref_link if fallback_to_publish else "", limit
    )

    if prev_dyn_id and not fallback_to_publish:
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
                    curr_time = datetime.now().time()
                    t_start = datetime.strptime(sleep_cfg.start_time, "%H:%M").time()
                    t_end = datetime.strptime(sleep_cfg.end_time, "%H:%M").time()
                    
                    is_sleeping_time = (t_start <= curr_time <= t_end) if t_start <= t_end else (curr_time >= t_start or curr_time <= t_end)
                        
                    if is_sleeping_time:
                        logger.info(f"🌙 触发仿生休眠期 ({sleep_cfg.start_time} - {sleep_cfg.end_time})，系统进入深度蛰伏...")
                        GloBotState.is_sleeping = True
                        GloBotState.wake_up_event.clear()
                        try: 
                            await asyncio.wait_for(GloBotState.wake_up_event.wait(), timeout=600)
                            logger.info("⚡ 收到强制唤醒信号，提前结束蛰伏！")
                        except asyncio.TimeoutError: pass
                        finally: GloBotState.is_sleeping = False
                        continue
                except Exception as e:
                    logger.error(f"⚠️ 作息时间解析异常: {e}")

            if time.time() - last_cleanup_time > 12 * 3600:
                cleanup_old_media(getattr(settings.system, 'media_retention_days', 2.0))
                last_cleanup_time = time.time()

            logger.info("\n📡 启动爬虫嗅探...")
            try:
                await fetch_timeline()
            except RuntimeError as e:
                if "TWITTER_AUTH_EXPIRED" in str(e):
                    logger.critical(f"🛑 [熔断机制] 侦测到推特账号异常: {e}")
                    GloBotState.is_running.clear()
                    await send_tg_error(f"🛑 <b>推特账号疑似被风控！</b>\n\n异常追踪：\n<code>{e}</code>\n\n请更新 cookie 后发送 /resume 恢复。")
                    continue
                else: raise e
                
            json_files = list(RAW_DIR.glob("*.json"))
            if not json_files:
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
                logger.info(f"⚡ [并发车间] 提取出 {len(unique_nodes)} 个独立纯净任务。")
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
                        logger.critical(f"🛑 [熔断机制] 大模型翻译引擎宕机: {e}")
                        GloBotState.is_running.clear()
                        await send_tg_error(f"🛑 <b>大模型发生宕机！</b>\n\n<code>{e}</code>\n\n发送 /resume 恢复。")
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
                            leaf_node_type = tweet.get('node_type', 'ORIGINAL')
                            dyn_map[tweet_id] = {
                                "dyn_id": new_dyn_id, "author_handle": tweet['author'], 
                                "author_display_name": tweet.get('author_display_name', f"@{tweet['author']}"),
                                "node_type": leaf_node_type, "dt_str": datetime.fromtimestamp(tweet['timestamp']).strftime("%Y-%m-%d %H:%M:%S"), 
                                "translated_text": "" if leaf_node_type == 'RETWEET' else preprocessing_cache[tweet_id]['translated_text'], 
                                "raw_text": "" if leaf_node_type == 'RETWEET' else html.unescape(tweet['text']), 
                                "publish_mode": leaf_publish_mode
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
                        await send_tg_error(f"🛑 <b>安全熔断机制触发！</b>\n\n<code>{e}</code>\n\n重新扫码后发送 /resume。")
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