import os
import json
import time
import logging
import asyncio
import random
import html
from pathlib import Path
from datetime import datetime

# ==========================================
# 🧩 导入所有组件
# ==========================================
from common.config_loader import settings
from Bot_Crawler.twitter_scraper import fetch_timeline
from Bot_Crawler.tweet_parser import parse_timeline_json
from Bot_Media.llm_translator import translate_text
from Bot_Media.media_pipeline import dispatch_media
from Bot_Publisher.bili_uploader import smart_publish

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("GloBot_Main")

DATA_DIR = Path(os.getenv("LOCAL_DATA_DIR", f"./GloBot_Data/{settings.targets.group_name}"))
RAW_DIR = DATA_DIR / "timeline_raw"
HISTORY_FILE = DATA_DIR / "history.json"

# 用于记录是否是项目有史以来第一次执行
FIRST_RUN_FLAG_FILE = DATA_DIR / ".first_run_completed"

def load_history():
    """读取历史记录"""
    if not HISTORY_FILE.exists():
        return set()
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except Exception as e:
        logger.error(f"❌ 读取历史记录失败: {e}")
        return set()

def save_history(history_set):
    """持久化记录已发布的推文ID"""
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(list(history_set), f, ensure_ascii=False, indent=2)

async def process_pipeline(tweet: dict) -> bool:
    """全链路处理单条推文（翻译 -> 视频压制 -> 发布）"""
    tweet_id = str(tweet['id'])
    author = str(tweet.get('author', '')).lower()
    raw_text = tweet.get('text', '')
    media_files = tweet.get('media', [])  # 这已经是本地绝对路径列表了
    timestamp = tweet.get('timestamp', int(time.time()))
    
    logger.info(f"\n" + "="*50)
    logger.info(f"🚀 开始处理推文 ID: {tweet_id} | 作者: @{author}")
    
    # --- 1. 极其优雅的标题组装与防爆截断 ---
    fallback_title = f"{settings.targets.group_name} 最新动态"
    # 直接从 config.yaml 读取动态映射字典
    raw_title = settings.targets.account_title_map.get(author, fallback_title)
    
    # ⚠️ 核心修复：B站 Opus 标题极短，强行保留前 15 个字符以防报 4126146
    safe_title = raw_title[:15] 
    settings.publishers.bilibili.title = safe_title
    logger.info(f"   -> [安全标题] 已设定为: '{safe_title}'")
    
    # --- 2. 翻译正文 ---
    logger.info(f"   -> [探针] 爬虫提取到的原始日文: '{raw_text}'")
    translated_text = await translate_text(raw_text)
    logger.info(f"   -> [探针] LLM 返回的中译结果: '{translated_text}'")
    
    # 3. 动态正文终极排版 (中日双语对照)
    dt_str = datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
    
    # 清洗日文原文中的 HTML 转义符 (比如把 &lt; 还原成 <)，确保 B 站展示完美
    clean_raw_text = html.unescape(raw_text)
    
    final_content = f"{dt_str}\n\n{translated_text}\n\n【原文】\n{clean_raw_text}\n\n{tweet_id}\n-由GloBot驱动"

    # --- 4. 视频压制处理 (如果有视频的话) ---
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
                final_media_paths.append(str(source_file)) # 兜底用原视频
        else:
            final_media_paths.append(mf) # 图片直接保留
            
    # --- 5. 终极发射 ---
    logger.info("   -> 移交发布中枢...")
    success = await smart_publish(final_content, final_media_paths, video_type=video_type)
    
    # --- 6. 清理压制产物 ---
    for f in final_media_paths:
        if "ready_to_publish" in str(f):
            try: Path(f).unlink()
            except: pass
            
    return success

async def main_loop():
    logger.info("🤖 GloBot 工业流水线已启动...")
    
    # 判定是否为“真·首次启动”
    is_first_run = not FIRST_RUN_FLAG_FILE.exists()
    history_set = load_history()
    
    if is_first_run:
        logger.warning("🚨 检测到首次部署！首发截断保护机制已就绪。")
    
    while True:
        try:
            logger.info("\n📡 启动爬虫嗅探...")
            await fetch_timeline()  # 执行 Playwright 动作，落盘 JSON
            
            json_files = list(RAW_DIR.glob("*.json"))
            if not json_files:
                logger.info("💤 未发现 JSON 矿石，休眠中...")
                await asyncio.sleep(60)
                continue
                
            latest_json = max(json_files, key=os.path.getmtime)
            
            # 拿到结构化的新推文列表
            new_tweets = await parse_timeline_json(latest_json)
            
            # 🗑️ 阅后即焚：清理旧 JSON，但保留最新的一条方便调试
            for jf in json_files:
                if jf.name != latest_json.name:
                    try: jf.unlink()
                    except: pass
            
            if not new_tweets:
                sleep_time = random.randint(240, 420)
                logger.info(f"💤 无新动态，休眠 {sleep_time} 秒...")
                await asyncio.sleep(sleep_time)
                continue
                
            # 按时间从旧到新排序，保证补发时间轴正确
            new_tweets.sort(key=lambda x: x['timestamp'])
            
            # ==========================================
            # 🛡️ 首次启动截断机制
            # ==========================================
            if is_first_run:
                logger.warning(f"🚨 [首发保护] 检测到首次启动，爬取到 {len(new_tweets)} 条历史推文，仅保留最新一条！")
                
                # 将除最后一条外的所有历史推文直接写入数据库
                for t in new_tweets[:-1]:
                    history_set.add(str(t['id']))
                save_history(history_set)
                
                new_tweets = [new_tweets[-1]]
                
                # 标记首次启动已完成
                FIRST_RUN_FLAG_FILE.touch()
                is_first_run = False
            else:
                logger.info(f"🎯 待处理队列：{len(new_tweets)} 条动态")

            # ==========================================
            # 🔄 处理与冷却队列
            # ==========================================
            total = len(new_tweets)
            for i, tweet in enumerate(new_tweets):
                tweet_id = str(tweet['id'])
                success = await process_pipeline(tweet)
                
                if success:
                    history_set.add(tweet_id)
                    save_history(history_set)
                    logger.info(f"✅ 任务 {i+1}/{total} [{tweet_id}] 成功发射！")
                else:
                    logger.error(f"❌ 推文 {tweet_id} 发布失败，网络异常或触碰风控！")
                    break # 跳出循环，等下个周期再试，防止白给
                    
                # 队列积压补发时，增加 1 分钟安全冷却
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