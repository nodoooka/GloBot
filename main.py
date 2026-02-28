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
# 🚨 引入刚才写好的动态猎犬
from Bot_Publisher.bili_uploader import smart_publish, smart_repost, get_dynamic_id_by_bvid
from common.text_sanitizer import sanitize_for_bilibili

# ==========================================
# 🔇 全局日志静音配置 (防刷屏)
# ==========================================
# 1. 抑制底层网络库的心跳与连接日志
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# 2. 🚨 核心修复：屏蔽 Telegram 轮询器的断网报错刷屏
# Updater 遇到断网会自动重连，强制将其日志级别提升至 CRITICAL，避免打印几百行 Error
logging.getLogger("telegram.ext.Updater").setLevel(logging.CRITICAL)

# 🌟 新增引入视频投稿中枢
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
                    
    # 顺手清理空文件夹
    for member_dir in media_dir.iterdir():
        if member_dir.is_dir() and not any(member_dir.iterdir()):
            try: member_dir.rmdir()
            except: pass
            
    if deleted_files > 0:
        logger.info(f"🧹 [空间管理] 触发自动清理！已永久销毁 {deleted_files} 个超过 {retention_days} 天的陈旧媒体文件。")

# 处理媒体管线的共用函数
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

# 清理压制产物
def cleanup_media(media_paths):
    for f in media_paths:
        if "ready_to_publish" in str(f):
            try: Path(f).unlink()
            except: pass

async def process_pipeline(tweet: dict, dyn_map: dict) -> tuple[bool, str]:
    logger.info(f"\n" + "="*50)
    logger.info(f"🚀 开始处理推文树... 目标终点成员: @{tweet['author']}")
    
    # 🚨 获取 ID 保留配置策略 (兜底为 0: 全保留)
    id_retention_level = getattr(settings.publishers.bilibili, 'tweet_id_retention', 0)
    prev_dyn_id = None
    
    # ==========================================
    # 🔗 第一阶段：从根到叶，层层穿透发布外部引用节点
    # ==========================================
    for ancestor in tweet.get('quote_chain', []):
        anc_id = ancestor['id']
        is_reply = ancestor.get('is_reply', False)
        is_placeholder = ancestor.get('is_placeholder', False)
        
        if anc_id in dyn_map:
            prev_dyn_id = dyn_map[anc_id]
            logger.info(f"   -> ♻️ 记忆寻址命中：节点 {anc_id} 已搬运过，跳过。")
            continue
            
        if is_placeholder:
            logger.info(f"   -> ⚠️ 节点 {anc_id} 仅为防断链占位符且无记忆，跳过强行发布。")
            continue
            
        logger.info(f"   -> ⛓️ 发现全新未搬运的祖先节点！开始穿透发布: @{ancestor['author']}")
        
        anc_translated = await translate_text(ancestor['text'])
        
        dt_str = datetime.fromtimestamp(ancestor['timestamp']).strftime("%Y-%m-%d %H:%M:%S")
        clean_raw = html.unescape(ancestor['text'])
        
        author_handle = ancestor['author']
        author_display = ancestor.get('author_display_name', f"@{author_handle}")
        
        # 👇 核心修复：拦截非成员，并强制阻断全局变量状态污染
        anc_title = settings.targets.account_title_map.get(author_handle, "")
        display_name = anc_title if anc_title else author_display

        # 🚨 排版渲染分支：极简聊天气泡 vs 传统排版
        if is_reply:
            settings.publishers.bilibili.title = "" # 回复不加卡片独立标题
            anc_content = f"💬【{display_name}】回复说：\n{anc_translated}\n\n(原文: {clean_raw})"
            if id_retention_level == 0:
                anc_content += f"\n\n{anc_id}"
        else:
            settings.publishers.bilibili.title = anc_title[:15] if anc_title else ""
            header = f"【{anc_title}】\n\n" if anc_title else f"{display_name}\n\n"
            anc_content = f"{header}{dt_str}\n\n{anc_translated}\n\n【原文】\n{clean_raw}"
            if id_retention_level < 3:
                anc_content += f"\n\n{anc_id}\n-由GloBot驱动"
        
        anc_content = sanitize_for_bilibili(anc_content)
        
        anc_media, anc_video_type = await process_media_files(ancestor['media'])
        anc_source_url = f"https://x.com/{ancestor['author']}/status/{anc_id}"
        
        has_anc_video = (anc_video_type == "translated" and settings.publishers.bilibili.publish_translated_video) or \
                        (anc_video_type == "original" and settings.publishers.bilibili.publish_original_video)
        vid_path = next((p for p in anc_media if str(p).lower().endswith('.mp4')), None) if has_anc_video else None
        has_any_media = len(anc_media) > 0 # 🚨 若包含任何图/视频，绝对禁止使用转发卡片！
        
        if prev_dyn_id:
            real_prev_dyn_id = prev_dyn_id
            
            # 动态猎犬反查 BV 号
            if str(prev_dyn_id).startswith("BV"):
                resolved_id = await get_dynamic_id_by_bvid(prev_dyn_id)
                if resolved_id:
                    real_prev_dyn_id = resolved_id
                    logger.info(f"   -> 🎯 [动态猎犬] 成功将 {prev_dyn_id} 反查为动态 ID: {real_prev_dyn_id}")
                else:
                    logger.warning(f"   -> ⚠️ [动态猎犬] 反查 {prev_dyn_id} 失败，将被迫执行降级发布。")

            # 🚨 智能降级与反查路由
            if has_any_media or str(real_prev_dyn_id).startswith("BV"):
                ref_link = f"https://www.bilibili.com/video/{prev_dyn_id}" if str(prev_dyn_id).startswith("BV") else f"https://t.bilibili.com/{prev_dyn_id}"
                
                # 优雅拼接上下文溯源链接
                if is_reply:
                    anc_content += f"\n\n(🔗 溯源链接: {ref_link})"
                else:
                    anc_content += f"\n\n🔗 溯源链接: {ref_link}"
                    
                if vid_path:
                    logger.info(f"   -> 🆕 [智能降级] 含媒体/反查拦截，转为独立视频投稿 (附溯源)...")
                    if id_retention_level >= 2:
                        anc_content = anc_content.replace(f"\n\n{anc_id}\n-由GloBot驱动", "").replace(f"\n\n{anc_id}", "")
                    success, new_anc_dyn_id = await upload_video_bilibili(vid_path, anc_title if anc_title else "最新搬运", anc_content, anc_source_url, settings)
                else:
                    logger.info(f"   -> 🆕 [智能降级] 含媒体/反查拦截，转为独立图文动态 (附溯源)...")
                    success, new_anc_dyn_id = await smart_publish(anc_content, anc_media, video_type=anc_video_type)
            else:
                logger.info(f"   -> 🔄 触发 B 站无限套娃机制...")
                success, new_anc_dyn_id = await smart_repost(anc_content, real_prev_dyn_id)
        else:
            # 🎥 祖先节点的视频发射路由
            if vid_path:
                logger.info(f"   -> 🆕 [祖先节点] 移交视频投稿中枢...")
                if id_retention_level >= 2:
                    anc_content = anc_content.replace(f"\n\n{anc_id}\n-由GloBot驱动", "").replace(f"\n\n{anc_id}", "")
                success, new_anc_dyn_id = await upload_video_bilibili(vid_path, anc_title if anc_title else "最新搬运", anc_content, anc_source_url, settings)
            else:
                logger.info(f"   -> 🆕 [祖先节点] 移交图文首发中枢 (降级处理)...")
                success, new_anc_dyn_id = await smart_publish(anc_content, anc_media, video_type=anc_video_type)
            
        cleanup_media(anc_media)
        
        if success and new_anc_dyn_id:
            dyn_map[anc_id] = new_anc_dyn_id
            save_dyn_map(dyn_map)
            prev_dyn_id = new_anc_dyn_id
            logger.warning("   -> ⏳ [风控规避] 祖先节点发射成功，强制开启 65 秒冷却通道...")
            await asyncio.sleep(65)
        else:
            logger.error(f"❌ 引用节点链条断裂，发布终止！")
            return False, ""

    # ==========================================
    # 👑 第二阶段：处理成员的最终点评 (叶子节点)
    # ==========================================
    logger.info(f"   -> 👑 链路穿透完成，开始处理最终成员点评！")
    translated_text = await translate_text(tweet['text'])
    
    raw_title = settings.targets.account_title_map.get(tweet['author'], f"@{tweet['author']}")
    dt_str = datetime.fromtimestamp(tweet['timestamp']).strftime("%Y-%m-%d %H:%M:%S")
    clean_raw_text = html.unescape(tweet['text'])
    
    author_handle = tweet['author']
    display_name = raw_title if author_handle in settings.targets.account_title_map else tweet.get('author_display_name', f"@{author_handle}")
    is_leaf_reply = tweet.get('is_reply', False)

    if is_leaf_reply:
        settings.publishers.bilibili.title = ""
        final_content = f"💬【{display_name}】回复说：\n{translated_text}\n\n(原文: {clean_raw_text})"
        if id_retention_level == 0:
            final_content += f"\n\n{tweet['id']}"
    else:
        settings.publishers.bilibili.title = raw_title[:15]
        header = f"【{raw_title}】\n\n" if author_handle in settings.targets.account_title_map else ""
        final_content = f"{header}{dt_str}\n\n{translated_text}\n\n【原文】\n{clean_raw_text}"
        if id_retention_level < 3:
            final_content += f"\n\n{tweet['id']}\n-由GloBot驱动"

    final_content = sanitize_for_bilibili(final_content)
    
    final_media, video_type = await process_media_files(tweet['media'])
    final_source_url = f"https://x.com/{tweet['author']}/status/{tweet['id']}"
    
    has_final_video = (video_type == "translated" and settings.publishers.bilibili.publish_translated_video) or \
                      (video_type == "original" and settings.publishers.bilibili.publish_original_video)
    vid_path = next((p for p in final_media if str(p).lower().endswith('.mp4')), None) if has_final_video else None
    has_any_media = len(final_media) > 0 # 🚨 任何媒体都不能进原生转发卡片

    if prev_dyn_id:
        real_prev_dyn_id = prev_dyn_id
        
        # 动态猎犬反查 BV 号
        if str(prev_dyn_id).startswith("BV"):
            resolved_id = await get_dynamic_id_by_bvid(prev_dyn_id)
            if resolved_id:
                real_prev_dyn_id = resolved_id
                logger.info(f"   -> 🎯 [动态猎犬] 成功将 {prev_dyn_id} 反查为动态 ID: {real_prev_dyn_id}")
            else:
                logger.warning(f"   -> ⚠️ [动态猎犬] 反查 {prev_dyn_id} 失败，将被迫执行降级发布。")

        if has_any_media or str(real_prev_dyn_id).startswith("BV"):
            ref_link = f"https://www.bilibili.com/video/{prev_dyn_id}" if str(prev_dyn_id).startswith("BV") else f"https://t.bilibili.com/{prev_dyn_id}"
            
            if is_leaf_reply:
                final_content += f"\n\n(🔗 溯源链接: {ref_link})"
            else:
                final_content += f"\n\n🔗 溯源链接: {ref_link}"
                
            if vid_path:
                logger.info("   -> 🆕 [智能降级] 无法跨端转发/包含视频，转为独立视频投稿 (附溯源)...")
                if id_retention_level >= 2:
                    final_content = final_content.replace(f"\n\n{tweet['id']}\n-由GloBot驱动", "").replace(f"\n\n{tweet['id']}", "")
                success, new_dyn_id = await upload_video_bilibili(vid_path, raw_title[:80] if not is_leaf_reply else f"{display_name}的视频回复", final_content, final_source_url, settings)
            else:
                logger.info("   -> 🆕 [智能降级] 源头为视频/包含媒体，转为独立图文动态 (附视频链接)...")
                success, new_dyn_id = await smart_publish(final_content, final_media, video_type=video_type)
        else:
            logger.info(f"   -> ♻️ 触发成员转发动作...")
            success, new_dyn_id = await smart_repost(final_content, real_prev_dyn_id)
    else:
        # 🎥 叶子节点的视频发射路由
        if vid_path:
            logger.info("   -> 移交视频投稿中枢...")
            if id_retention_level >= 2:
                final_content = final_content.replace(f"\n\n{tweet['id']}\n-由GloBot驱动", "").replace(f"\n\n{tweet['id']}", "")
            success, new_dyn_id = await upload_video_bilibili(vid_path, raw_title[:80] if not is_leaf_reply else f"{display_name}的视频回复", final_content, final_source_url, settings)
        else:
            logger.info("   -> 移交图文首发中枢 (降级处理)...")
            success, new_dyn_id = await smart_publish(final_content, final_media, video_type=video_type)
        
    cleanup_media(final_media)
    return success, new_dyn_id

async def pipeline_loop():
    logger.info("🤖 GloBot 工业流水线已启动...")
    
    # 这里的 start_telegram_bot 将接管全局控制权
    await start_telegram_bot()
    
    is_first_run = not FIRST_RUN_FLAG_FILE.exists()
    history_set = load_history()
    dyn_map = load_dyn_map()
    last_cleanup_time = 0
    
    if is_first_run: logger.warning("🚨 检测到首次部署！首发截断保护机制已就绪。")
    
    while True:
        try:
            # 👇 2. 阀门卡口：如果 TG 下达了暂停指令，这里会无限挂起，直到恢复
            await GloBotState.is_running.wait()

            if time.time() - last_cleanup_time > 12 * 3600:
                retention = getattr(settings.system, 'media_retention_days', 2.0)
                cleanup_old_media(retention_days=retention)
                last_cleanup_time = time.time()

            logger.info("\n📡 启动爬虫嗅探...")
            await fetch_timeline()
            
            json_files = list(RAW_DIR.glob("*.json"))
            if not json_files:
                # 👇 找回这行日志
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
                # 👇 找回这行日志
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

            total = len(new_tweets)
            for i, tweet in enumerate(new_tweets):
                # 👇 每次发推前都检查一下阀门状态
                await GloBotState.is_running.wait()
                
                tweet_id = str(tweet['id'])
                
                try:
                    success, new_dyn_id = await process_pipeline(tweet, dyn_map)
                    
                    if success:
                        history_set.add(tweet_id)
                        save_history(history_set)
                        if new_dyn_id:
                            dyn_map[tweet_id] = new_dyn_id
                            save_dyn_map(dyn_map)
                        logger.info(f"✅ 任务 {i+1}/{total} [{tweet_id}] 成功发射！")
                        GloBotState.daily_stats['success'] += 1  # 统计成功
                        
                        # 🌟 新增：整体流程播报 (针对图文或普通搬运)
                        if not str(new_dyn_id).startswith("BV"):  # 视频会在专门的模块发推送，这里过滤掉以防重复
                            await send_tg_msg(f"🎉 <b>图文搬运成功</b> [{i+1}/{total}]\n推特源: <code>{tweet_id}</code>\n成功生成 B站动态: <code>{new_dyn_id}</code>")

                    else:
                        logger.error(f"❌ 推文 {tweet_id} 发布失败！")
                        GloBotState.daily_stats['failed'] += 1   # 统计失败
                        
                        # 🌟 新增：失败总体提示
                        await send_tg_msg(f"❌ <b>搬运受阻</b> [{i+1}/{total}]\n推特源: <code>{tweet_id}</code>\n未能成功发布，请检查终端日志排查。")
                        continue
                        
                except RuntimeError as e: # 🚨 核心改动：加入全局安全熔断器！
                    if "AUTH_EXPIRED" in str(e):
                        logger.critical(f"🛑 [熔断机制] 侦测到凭证失效，强行切断流水线: {e}")
                        GloBotState.is_running.clear() # 物理锁死总线
                        await send_tg_error(f"🛑 <b>安全熔断机制触发！</b>\n\n检测到账号令牌失效或被拦截：\n<code>{e}</code>\n\n为防止无限重试导致死封，流水线已<b>强制物理挂起</b>。\n👉 请在终端运行 `python Bot_Publisher/bili_login.py` 重新扫码，更新凭证后发送 <code>/resume</code> 恢复运行。")
                        break # 强制跳出这批推文的循环，进入最外层的 wait() 挂起等待
                    else:
                        err_trace = traceback.format_exc()
                        logger.error(f"🔥 处理推文 {tweet_id} 时发生运行时异常: {e}")
                        await send_tg_error(f"处理推文崩溃:\n{err_trace[-300:]}")
                        GloBotState.daily_stats['failed'] += 1
                        continue

                except Exception as e:
                    err_trace = traceback.format_exc()
                    logger.error(f"🔥 处理推文 {tweet_id} 时发生内部崩溃: {e}")
                    # 👇 3. 抛出致命异常到主理人的手机上！
                    await send_tg_error(f"处理推文 {tweet_id} 崩溃:\n{err_trace[-300:]}")
                    GloBotState.daily_stats['failed'] += 1
                    continue
                    
                if i < total - 1:
                        logger.warning("⏳ [风控规避] 单个成员任务完成，休眠 65 秒进入下一任务...")
                        await asyncio.sleep(65)
                    
            sleep_time = random.randint(240, 420)
            # 👇 找回这行日志
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
            
        except Exception as e:
            err_trace = traceback.format_exc()
            logger.error(f"🔥 总线发生未捕获异常: {e}")
            # 👇 将总线级崩溃直接推送到 Telegram
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