import os
import json
import time
import logging
import asyncio
import random
import html
from pathlib import Path
from datetime import datetime

from common.config_loader import settings
from Bot_Crawler.twitter_scraper import fetch_timeline
from Bot_Crawler.tweet_parser import parse_timeline_json
from Bot_Media.llm_translator import translate_text
from Bot_Media.media_pipeline import dispatch_media
from Bot_Publisher.bili_uploader import smart_publish, smart_repost # 引入原生转发模块

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("GloBot_Main")

DATA_DIR = Path(os.getenv("LOCAL_DATA_DIR", f"./GloBot_Data/{settings.targets.group_name}"))
RAW_DIR = DATA_DIR / "timeline_raw"
HISTORY_FILE = DATA_DIR / "history.json"
DYN_MAP_FILE = DATA_DIR / "dyn_map.json" # 新增：动态映射记忆表

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

async def process_pipeline(tweet: dict, dyn_map: dict) -> tuple[bool, str]:
    tweet_id = str(tweet['id'])
    author = str(tweet.get('author', '')).lower()
    raw_text = tweet.get('text', '')
    quoted_tweet_id = tweet.get('quoted_tweet_id')
    media_files = tweet.get('media', [])  
    timestamp = tweet.get('timestamp', int(time.time()))
    
    logger.info(f"\n" + "="*50)
    logger.info(f"🚀 开始处理推文 ID: {tweet_id} | 作者: @{author}")
    
    # 🧠 核心判断：看看引用的这条推文，我们在 B 站发过没？
    orig_dyn_id_str = dyn_map.get(quoted_tweet_id) if quoted_tweet_id else None

    # 如果没有发过，或者干脆是外部成员推文，则降级为图文拼接兜底
    if not orig_dyn_id_str and tweet.get('quoted_text'):
        raw_text += f"\n\n【引用内容】:\n{tweet['quoted_text']}"
    
    fallback_title = f"{settings.targets.group_name} 最新动态"
    raw_title = settings.targets.account_title_map.get(author, fallback_title)
    safe_title = raw_title[:15] 
    settings.publishers.bilibili.title = safe_title
    logger.info(f"   -> [安全标题] 已设定为: '{safe_title}'")
    
    logger.info(f"   -> [探针] 爬虫提取到的原始日文: '{raw_text}'")
    translated_text = await translate_text(raw_text)
    logger.info(f"   -> [探针] LLM 返回的中译结果: '{translated_text}'")
    
    dt_str = datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
    clean_raw_text = html.unescape(raw_text)
    final_content = f"{dt_str}\n\n{translated_text}\n\n【原文】\n{clean_raw_text}\n\n{tweet_id}\n-由GloBot驱动"

    video_type = "none"
    final_media_paths = []
    
    for mf in media_files:
        if str(mf).lower().endswith(('.mp4', '.mov')):
            logger.info(f"   -> 检测到视频，启动媒体管线...")
            source_file = Path(mf)
            PUBLISH_DIR = DATA_DIR / "ready_to_publish"
            PUBLISH_DIR.mkdir(parents=True, exist_ok=True)
            output_file = PUBLISH_DIR / f"final_{source_file.name}"
            
            await dispatch_media(str(source_file))
            if output_file.exists():
                final_media_paths.append(str(output_file))
                video_type = "translated" if settings.media_engine.enable_ai_translation else "original"
            else:
                final_media_paths.append(str(source_file)) 
        else:
            final_media_paths.append(mf) 
            
    # 🚀 最终智能发射路由
    if orig_dyn_id_str:
        logger.info(f"   -> ♻️ 检测到成员带评论转发了已有动态！触发 B站原生转发功能！")
        success, new_dyn_id = await smart_repost(final_content, orig_dyn_id_str)
    else:
        logger.info("   -> 移交图文/视频发布中枢...")
        success, new_dyn_id = await smart_publish(final_content, final_media_paths, video_type=video_type)
    
    for f in final_media_paths:
        if "ready_to_publish" in str(f):
            try: Path(f).unlink()
            except: pass
            
    return success, new_dyn_id

async def main_loop():
    logger.info("🤖 GloBot 工业流水线已启动...")
    
    is_first_run = not FIRST_RUN_FLAG_FILE.exists()
    history_set = load_history()
    dyn_map = load_dyn_map() # 🧠 加载 B 站动态映射记忆
    
    if is_first_run:
        logger.warning("🚨 检测到首次部署！首发截断保护机制已就绪。")
    
    while True:
        try:
            logger.info("\n📡 启动爬虫嗅探...")
            await fetch_timeline()
            
            json_files = list(RAW_DIR.glob("*.json"))
            if not json_files:
                logger.info("💤 未发现 JSON 矿石，休眠中...")
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
                logger.info(f"💤 无新动态，休眠 {sleep_time} 秒...")
                await asyncio.sleep(sleep_time)
                continue
                
            new_tweets.sort(key=lambda x: x['timestamp'])
            
            if is_first_run:
                logger.warning(f"🚨 [首发保护] 检测到首次启动，爬取到 {len(new_tweets)} 条历史推文，仅保留最新一条！")
                for t in new_tweets[:-1]:
                    history_set.add(str(t['id']))
                save_history(history_set)
                
                new_tweets = [new_tweets[-1]]
                FIRST_RUN_FLAG_FILE.touch()
                is_first_run = False
            else:
                logger.info(f"🎯 待处理队列：{len(new_tweets)} 条动态")

            total = len(new_tweets)
            for i, tweet in enumerate(new_tweets):
                tweet_id = str(tweet['id'])
                success, new_dyn_id = await process_pipeline(tweet, dyn_map)
                
                if success:
                    history_set.add(tweet_id)
                    save_history(history_set)
                    
                    # 🌟 成功发布后，持久化记录映射关系，为未来引用转发铺路
                    if new_dyn_id:
                        dyn_map[tweet_id] = new_dyn_id
                        save_dyn_map(dyn_map)
                        
                    logger.info(f"✅ 任务 {i+1}/{total} [{tweet_id}] 成功发射！B站动态ID: {new_dyn_id}")
                else:
                    logger.error(f"❌ 推文 {tweet_id} 发布失败，网络异常或触碰风控！")
                    break
                    
                if i < total - 1:
                    logger.warning("⏳ [风控规避] 连续发送冷却中，休眠 65 秒...")
                    await asyncio.sleep(65)
                    
            sleep_time = random.randint(240, 420)
            logger.info(f"✅ 周期巡视完成，深度休眠 {sleep_time} 秒...")
            await asyncio.sleep(sleep_time)
            
        except Exception as e:
            logger.error(f"🔥 总线发生未捕获异常: {e}")
            await asyncio.sleep(60)

if __name__ == "__main__":
    try:
        asyncio.run(main_loop())
    except KeyboardInterrupt:
        logger.info("\n🛑 收到主控台切断信号，GloBot 安全停机。")