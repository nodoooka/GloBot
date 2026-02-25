import json
import sqlite3
import os
import sys
import asyncio
import time
from pathlib import Path
from datetime import datetime

# 将项目根目录加入系统路径
sys.path.append(str(Path(__file__).resolve().parent.parent))
from common.config_loader import settings

from Bot_Crawler.media_downloader import download_media  

# 🌟 GloBot 动态路径
FACTORY_DIR = Path(os.getenv("LOCAL_DATA_DIR", f"./GloBot_Data/{settings.targets.group_name}"))
DB_PATH = FACTORY_DIR / "processed_tweets.db"

def init_db():
    FACTORY_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS tweets (
            tweet_id TEXT PRIMARY KEY,
            author TEXT,
            extracted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    return conn

def find_tweets(obj):
    if isinstance(obj, dict):
        # 💣 核心修复：必须带有全景正文，或者显式声明类型是 Tweet，彻底排除 User 对象
        is_tweet = 'Tweet' in str(obj.get('__typename', '')) or 'full_text' in obj.get('legacy', {})
        
        if is_tweet and 'legacy' in obj and 'rest_id' in obj and 'core' in obj:
            yield obj
            
        for k, v in obj.items():
            yield from find_tweets(v)
    elif isinstance(obj, list):
        for item in obj:
            yield from find_tweets(item)

def find_key(obj, target_key):
    if isinstance(obj, dict):
        if target_key in obj:
            return obj[target_key]
        for v in obj.values():
            res = find_key(v, target_key)
            if res is not None:
                return res
    elif isinstance(obj, list):
        for item in obj:
            res = find_key(item, target_key)
            if res is not None:
                return res
    return None

async def parse_timeline_json(json_file_path: Path) -> list:
    """解析 JSON 矿石，并返回结构化的新推文列表给总控中心"""
    print(f"🔬 正在化验矿石: {json_file_path.name}")
    
    with open(json_file_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    conn = init_db()
    cursor = conn.cursor()
    target_accounts = [acc.lower() for acc in settings.targets.x_accounts]
    
    parsed_new_tweets = []

    for tweet_node in find_tweets(data):
        tweet_id = tweet_node.get('rest_id')
        legacy = tweet_node.get('legacy', {})
        raw_screen_name = find_key(tweet_node.get('core', {}), 'screen_name')
        author_screen_name = str(raw_screen_name).lower() if raw_screen_name else ''
        
        if author_screen_name not in target_accounts:
            continue
        if 'retweeted_status_result' in legacy and 'is_quote_status' not in legacy:
            continue
            
        cursor.execute("SELECT 1 FROM tweets WHERE tweet_id = ?", (tweet_id,))
        if cursor.fetchone():
            continue

        # ==========================================
        # 🔪 暴力提取文本逻辑 (彻底根治长推文与引用推文丢失问题)
        # ==========================================
        full_text = legacy.get('full_text', '')
        
        # 1. 兼容推特蓝V长推文 (Note Tweet)
        try:
            if 'note_tweet_results' in tweet_node:
                note_text = tweet_node['note_tweet_results'].get('result', {}).get('text', '')
                if note_text:
                    full_text = note_text
            elif 'note_tweet' in tweet_node: # 某些旧版 GraphQL 格式兼容
                note_text = tweet_node['note_tweet'].get('note_tweet_results', {}).get('result', {}).get('text', '')
                if note_text:
                    full_text = note_text
        except Exception:
            pass
            
        # 2. 兼容引用推文 (Quote Tweet)
        try:
            if 'quoted_status_result' in tweet_node:
                quote_legacy = tweet_node['quoted_status_result']['result']['legacy']
                quote_text = quote_legacy.get('full_text', '')
                if quote_text:
                    full_text = full_text + f"\n\n【引用内容】:\n{quote_text}"
        except Exception:
            pass
            
        full_text = full_text.strip()
        # ==========================================

        media_files = legacy.get('extended_entities', {}).get('media', [])
        
        # 解析时间戳
        raw_created_at = legacy.get('created_at', '')
        try:
            dt = datetime.strptime(raw_created_at, "%a %b %d %H:%M:%S %z %Y")
            timestamp_sec = int(dt.timestamp())
        except:
            timestamp_sec = int(time.time()) 
        
        print(f"\n🌟 [新动态发现] 作者: @{author_screen_name} (ID: {tweet_id})")
        
        member_media_dir = FACTORY_DIR / "media" / author_screen_name
        local_media_paths = []
        
        img_count = 1
        for media in media_files:
            if media['type'] == 'photo':
                orig_url = media['media_url_https'] + "?name=orig"
                filename = f"{tweet_id}_img{img_count}.jpg"
                if await download_media(orig_url, member_media_dir, filename):
                    local_media_paths.append(str(member_media_dir / filename))
                img_count += 1
                
            elif media['type'] in ['video', 'animated_gif']:
                variants = media.get('video_info', {}).get('variants', [])
                mp4_variants = [v for v in variants if v.get('content_type') == 'video/mp4' and 'bitrate' in v]
                if mp4_variants:
                    best_video = sorted(mp4_variants, key=lambda x: x['bitrate'], reverse=True)[0]
                    vid_url = best_video['url']
                    filename = f"{tweet_id}_video.mp4"
                    if await download_media(vid_url, member_media_dir, filename):
                        local_media_paths.append(str(member_media_dir / filename))

        cursor.execute("INSERT INTO tweets (tweet_id, author) VALUES (?, ?)", (tweet_id, author_screen_name))
        conn.commit()

        parsed_new_tweets.append({
            'id': tweet_id,
            'author': author_screen_name,
            'text': full_text,
            'media': local_media_paths,
            'timestamp': timestamp_sec
        })

    conn.close()
    
    if not parsed_new_tweets:
        print("💤 没有发现新的监控对象动态，或全是旧数据。")
    else:
        print(f"\n✅ 提纯与下载全部完成！共提取 {len(parsed_new_tweets)} 条全新动态。")
        
    return parsed_new_tweets

if __name__ == "__main__":
    raw_dir = FACTORY_DIR / "timeline_raw"
    json_files = list(raw_dir.glob("*.json"))
    if not json_files:
        print("❌ 文件夹里空空如也，没有找到任何 JSON 矿石！")
    else:
        latest_json = max(json_files, key=os.path.getmtime)
        print(f"🤖 [自动寻敌] 发现最新抓取的数据包：{latest_json.name}\n")
        res = asyncio.run(parse_timeline_json(latest_json))
        print(f"\n测试返回数据预览: {json.dumps(res, ensure_ascii=False, indent=2)}")