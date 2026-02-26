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
from Bot_Publisher.bili_uploader import smart_publish, smart_repost

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
        
        # 如果这个老祖宗已经发过 B 站了，直接继承它的 ID，继续往下走
        if anc_id in dyn_map:
            prev_dyn_id = dyn_map[anc_id]
            logger.info(f"   -> ♻️ 记忆寻址命中：节点 {anc_id} 已搬运过，跳过。")
            continue
            
        logger.info(f"   -> ⛓️ 发现全新未搬运的祖先节点！开始穿透发布: @{ancestor['author']}")
        
        # 1. 翻译祖先节点
        anc_translated = await translate_text(ancestor['text'])
        
        # 2. 完美的排版组装（无视 B 站标题，直接拼装到内容顶部）
        anc_title = settings.targets.account_title_map.get(ancestor['author'], f"@{ancestor['author']}")
        dt_str = datetime.fromtimestamp(ancestor['timestamp']).strftime("%Y-%m-%d %H:%M:%S")
        clean_raw = html.unescape(ancestor['text'])
        
        anc_content = f"【{anc_title}】\n\n{dt_str}\n\n{anc_translated}\n\n【原文】\n{clean_raw}\n\n{anc_id}\n-由GloBot驱动"
        
        # 3. 处理祖先媒体文件
        anc_media, anc_video_type = await process_media_files(ancestor['media'])
        
        # 4. 发布（判断是首发还是转发套娃）
        if prev_dyn_id:
            logger.info(f"   -> 🔄 触发 B 站无限套娃机制...")
            success, new_anc_dyn_id = await smart_repost(anc_content, prev_dyn_id)
        else:
            logger.info(f"   -> 🆕 正在将推文树的最底层根节点进行首发...")
            success, new_anc_dyn_id = await smart_publish(anc_content, anc_media, video_type=anc_video_type)
            
        cleanup_media(anc_media)
        
        # 5. 严格风控
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

    # 针对首发动态的安全标题 (只有不是转发时才会用到这个字段)
    settings.publishers.bilibili.title = raw_title[:15]
    
    final_media, video_type = await process_media_files(tweet['media'])
    
    if prev_dyn_id:
        logger.info(f"   -> ♻️ 触发成员转发动作...")
        success, new_dyn_id = await smart_repost(final_content, prev_dyn_id)
    else:
        logger.info("   -> 移交首发中枢...")
        success, new_dyn_id = await smart_publish(final_content, final_media, video_type=video_type)
        
    cleanup_media(final_media)
    return success, new_dyn_id


async def main_loop():
    logger.info("🤖 GloBot 工业流水线已启动...")
    is_first_run = not FIRST_RUN_FLAG_FILE.exists()
    history_set = load_history()
    dyn_map = load_dyn_map()
    
    if is_first_run: logger.warning("🚨 检测到首次部署！首发截断保护机制已就绪。")
    
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
                for t in new_tweets[:-1]: history_set.add(str(t['id']))
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
                    if new_dyn_id:
                        dyn_map[tweet_id] = new_dyn_id
                        save_dyn_map(dyn_map)
                        
                    logger.info(f"✅ 任务 {i+1}/{total} [{tweet_id}] 成功发射！")
                else:
                    logger.error(f"❌ 推文 {tweet_id} 发布失败！")
                    break
                    
                if i < total - 1:
                    logger.warning("⏳ [风控规避] 单个成员任务完成，休眠 65 秒进入下一任务...")
                    await asyncio.sleep(65)
                    
            sleep_time = random.randint(240, 420)
            logger.info(f"✅ 周期巡视完成，深度休眠 {sleep_time} 秒...")
            await asyncio.sleep(sleep_time)
            
        except Exception as e:
            logger.error(f"🔥 总线发生未捕获异常: {e}")
            await asyncio.sleep(60)

if __name__ == "__main__":
    try: asyncio.run(main_loop())
    except KeyboardInterrupt: logger.info("\n🛑 收到主控台切断信号，GloBot 安全停机。")