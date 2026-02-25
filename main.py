import os
import asyncio
import logging
import random
from pathlib import Path
from datetime import datetime

# ==========================================
# 导入所有组件
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

# 用于记录是否是项目有史以来第一次执行
FIRST_RUN_FLAG_FILE = DATA_DIR / ".first_run_completed"

async def process_pipeline(tweet: dict) -> bool:
    """全链路处理单条推文（翻译 -> 视频压制 -> 发布）"""
    tweet_id = tweet['id']
    raw_text = tweet['text']
    media_files = tweet['media']  # 这已经是本地绝对路径列表了
    timestamp = tweet['timestamp']
    
    # 1. 组装 B 站标题: 格式 あいす(Aisu) yyyy-mm-dd hh:mm:ss
    dt_str = datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
    target_name = settings.targets.group_name
    settings.publishers.bilibili.title = f"{target_name} {dt_str}"
    
    # 2. 翻译正文
    logger.info(f"🧠 开始翻译推文 {tweet_id} ...")
    translated_text = await translate_text(raw_text)
    
    # 满足要求一：正文末尾附带原始推文 ID
    final_content = f"{translated_text}\n\n{tweet_id}"
    
    # 3. 视频压制处理 (如果有视频的话)
    video_type = "none"
    final_media_paths = []
    
    for mf in media_files:
        if str(mf).lower().endswith(('.mp4', '.mov')):
            logger.info(f"🎬 检测到视频，启动媒体管线...")
            # 注意：你的 dispatch_media 返回的是 None，它是直接在 ready_to_publish 里生成 final_xxx.mp4
            # 这里我们需要推断出处理后的视频路径
            source_file = Path(mf)
            PUBLISH_DIR = DATA_DIR / "ready_to_publish"
            output_file = PUBLISH_DIR / f"final_{source_file.name}"
            
            await dispatch_media(str(source_file))
            
            if output_file.exists():
                final_media_paths.append(str(output_file))
                video_type = "translated" if settings.media_engine.enable_ai_translation else "original"
            else:
                final_media_paths.append(str(source_file)) # 兜底用原视频
        else:
            final_media_paths.append(mf) # 图片直接保留
            
    # 4. 终极发射
    logger.info("🚀 移交发布中枢...")
    success = await smart_publish(final_content, final_media_paths, video_type=video_type)
    
    # 5. 清理压制产物
    for f in final_media_paths:
        if "ready_to_publish" in str(f):
            try: Path(f).unlink()
            except: pass
            
    return success

async def main_loop():
    logger.info("🤖 GloBot 工业流水线已启动...")
    
    # 判定是否为“真·首次启动”
    is_first_run = not FIRST_RUN_FLAG_FILE.exists()
    
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
            
            # 阅后即焚清理 JSON
            try: latest_json.unlink()
            except: pass
            
            if not new_tweets:
                sleep_time = random.randint(240, 420)
                logger.info(f"💤 无新动态，休眠 {sleep_time} 秒...")
                await asyncio.sleep(sleep_time)
                continue
                
            # 按时间从旧到新排序，保证补发时间轴正确
            new_tweets.sort(key=lambda x: x['timestamp'])
            
            # ==========================================
            # 🛡️ 要求二：首次启动截断机制
            # ==========================================
            if is_first_run:
                logger.warning(f"🚨 [首发保护] 检测到首次启动，爬取到 {len(new_tweets)} 条历史推文，仅保留最新一条！")
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
                success = await process_pipeline(tweet)
                if not success:
                    logger.error(f"❌ 推文 {tweet['id']} 发布失败，网络异常或触碰风控！")
                    break # 跳出循环，等下个周期再试，防止白给
                    
                # 要求二：队列积压补发时，增加 1 分钟安全冷却
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
        logger.info("\n🛑 收到终止指令，安全停机。")