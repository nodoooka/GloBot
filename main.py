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
from Bot_Publisher.bili_uploader import smart_publish, smart_repost
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
    
    prev_dyn_id = None
    
    # ==========================================
    # 🔗 第一阶段：从根到叶，层层穿透发布外部引用节点
    # ==========================================
    for ancestor in tweet.get('quote_chain', []):
        anc_id = ancestor['id']
        
        if anc_id in dyn_map:
            prev_dyn_id = dyn_map[anc_id]
            logger.info(f"   -> ♻️ 记忆寻址命中：节点 {anc_id} 已搬运过，跳过。")
            continue
            
        logger.info(f"   -> ⛓️ 发现全新未搬运的祖先节点！开始穿透发布: @{ancestor['author']}")
        
        anc_translated = await translate_text(ancestor['text'])
        
        dt_str = datetime.fromtimestamp(ancestor['timestamp']).strftime("%Y-%m-%d %H:%M:%S")
        clean_raw = html.unescape(ancestor['text'])
        
        author_handle = ancestor['author']
        author_display = ancestor.get('author_display_name', f"@{author_handle}")
        
        # 👇 核心修复：拦截非成员，并强制阻断全局变量状态污染
        if author_handle in settings.targets.account_title_map:
            anc_title = settings.targets.account_title_map[author_handle]
            settings.publishers.bilibili.title = anc_title[:15] # 强制覆盖为当前成员
            anc_content = f"【{anc_title}】\n\n{dt_str}\n\n{anc_translated}\n\n【原文】\n{clean_raw}\n\n{anc_id}\n-由GloBot驱动"
        else:
            anc_title = ""
            settings.publishers.bilibili.title = "" # 强制留空，消除上一个叶子节点的残留影响
            anc_content = f"{author_display}\n\n{dt_str}\n\n{anc_translated}\n\n【原文】\n{clean_raw}\n\n{anc_id}\n-由GloBot驱动"
        
        anc_content = sanitize_for_bilibili(anc_content)
        
        anc_media, anc_video_type = await process_media_files(ancestor['media'])
        anc_source_url = f"https://x.com/{ancestor['author']}/status/{anc_id}"
        
        if prev_dyn_id:
            logger.info(f"   -> 🔄 触发 B 站无限套娃机制...")
            success, new_anc_dyn_id = await smart_repost(anc_content, prev_dyn_id)
        else:
            # 🎥 祖先节点的视频发射路由
            has_anc_video = (anc_video_type == "translated" and settings.publishers.bilibili.publish_translated_video) or \
                            (anc_video_type == "original" and settings.publishers.bilibili.publish_original_video)
            
            if has_anc_video:
                vid_path = next((p for p in anc_media if str(p).lower().endswith('.mp4')), None)
                if vid_path:
                    logger.info(f"   -> 🆕 [祖先节点] 移交视频投稿中枢...")
                    success, new_anc_dyn_id = await upload_video_bilibili(
                        video_path=vid_path,
                        dynamic_title=anc_title,
                        dynamic_content=anc_content,
                        source_url=anc_source_url,
                        settings=settings
                    )
                else:
                    logger.info(f"   -> 🆕 [祖先节点] 移交图文首发中枢 (降级处理)...")
                    success, new_anc_dyn_id = await smart_publish(anc_content, anc_media, video_type=anc_video_type)
            else:
                logger.info(f"   -> 🆕 正在将推文树的最底层根节点进行首发...")
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
    
    final_content = f"{dt_str}\n\n{translated_text}\n\n【原文】\n{clean_raw_text}\n\n{tweet['id']}\n-由GloBot驱动"

    final_content = sanitize_for_bilibili(final_content)

    settings.publishers.bilibili.title = raw_title[:15]
    
    final_media, video_type = await process_media_files(tweet['media'])
    final_source_url = f"https://x.com/{tweet['author']}/status/{tweet['id']}"
    
    if prev_dyn_id:
        logger.info(f"   -> ♻️ 触发成员转发动作...")
        success, new_dyn_id = await smart_repost(final_content, prev_dyn_id)
    else:
        # 🎥 叶子节点的视频发射路由
        has_final_video = (video_type == "translated" and settings.publishers.bilibili.publish_translated_video) or \
                          (video_type == "original" and settings.publishers.bilibili.publish_original_video)
                          
        if has_final_video:
            vid_path = next((p for p in final_media if str(p).lower().endswith('.mp4')), None)
            if vid_path:
                logger.info("   -> 移交视频投稿中枢...")
                # 👇 修复：使用第二阶段专属的 final_content 和 raw_title
                success, new_dyn_id = await upload_video_bilibili(
                    video_path=vid_path,
                    dynamic_title=raw_title[:80],  # B站视频标题最长80字
                    dynamic_content=final_content,
                    source_url=final_source_url,
                    settings=settings
                )
            else:
                logger.info("   -> 移交图文首发中枢 (降级处理)...")
                success, new_dyn_id = await smart_publish(final_content, final_media, video_type=video_type)
        else:
            logger.info("   -> 移交图文首发中枢...")
            success, new_dyn_id = await smart_publish(final_content, final_media, video_type=video_type)
        
    cleanup_media(final_media)
    return success, new_dyn_id

async def main_loop():
    logger.info("🤖 GloBot 工业流水线已启动...")
    
    # 👇 1. 启动 Telegram 后台协程
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
                await asyncio.sleep(60)
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
                await asyncio.sleep(sleep_time)
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
                    else:
                        logger.error(f"❌ 推文 {tweet_id} 发布失败！")
                        GloBotState.daily_stats['failed'] += 1   # 统计失败
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
            await asyncio.sleep(sleep_time)
            
        except Exception as e:
            err_trace = traceback.format_exc()
            logger.error(f"🔥 总线发生未捕获异常: {e}")
            # 👇 将总线级崩溃直接推送到 Telegram
            await send_tg_error(f"总线挂机大崩溃:\n{err_trace[-400:]}")
            await asyncio.sleep(60)

if __name__ == "__main__":
    try: asyncio.run(main_loop())
    except KeyboardInterrupt: logger.info("\n🛑 收到主控台切断信号，GloBot 安全停机。")