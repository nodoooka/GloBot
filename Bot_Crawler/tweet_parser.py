import json
import sqlite3
import os
import sys
import asyncio
import time
import re
from pathlib import Path
from datetime import datetime

sys.path.append(str(Path(__file__).resolve().parent.parent))
from common.config_loader import settings
from Bot_Crawler.media_downloader import download_media  

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
        if target_key in obj: return obj[target_key]
        for v in obj.values():
            res = find_key(v, target_key)
            if res is not None: return res
    elif isinstance(obj, list):
        for item in obj:
            res = find_key(item, target_key)
            if res is not None: return res
    return None

# 🛠️ 新增：标准化的单节点推文提取器
def extract_tweet_node(node):
    tweet_id = str(node.get('rest_id', ''))
    legacy = node.get('legacy', {})
    raw_screen_name = find_key(node.get('core', {}), 'screen_name')
    author_screen_name = str(raw_screen_name).lower() if raw_screen_name else ''
    
    full_text = legacy.get('full_text', '')
    try:
        if 'note_tweet_results' in node:
            nt = node['note_tweet_results'].get('result', {}).get('text', '')
            if nt: full_text = nt
        elif 'note_tweet' in node: 
            nt = node['note_tweet'].get('note_tweet_results', {}).get('result', {}).get('text', '')
            if nt: full_text = nt
    except: pass
        
    full_text = re.sub(r'https?://t\.co/\w+', '', full_text).strip()
    media_files = legacy.get('extended_entities', {}).get('media', [])
    
    raw_created_at = legacy.get('created_at', '')
    try:
        dt = datetime.strptime(raw_created_at, "%a %b %d %H:%M:%S %z %Y")
        timestamp_sec = int(dt.timestamp())
    except:
        timestamp_sec = int(time.time()) 
        
    return {
        'id': tweet_id,
        'author': author_screen_name,
        'text': full_text,
        'media_files_raw': media_files,
        'timestamp': timestamp_sec,
        'raw_node': node
    }

async def parse_timeline_json(json_file_path: Path) -> list:
    print(f"🔬 正在化验矿石: {json_file_path.name}")
    with open(json_file_path, "r", encoding="utf-8") as f: data = json.load(f)

    conn = init_db()
    cursor = conn.cursor()
    target_accounts = [acc.lower() for acc in settings.targets.x_accounts]
    parsed_new_tweets = []

    for tweet_node in find_tweets(data):
        target_info = extract_tweet_node(tweet_node)
        
        if target_info['author'] not in target_accounts: continue
        if 'retweeted_status_result' in tweet_node.get('legacy', {}): continue
            
        cursor.execute("SELECT 1 FROM tweets WHERE tweet_id = ?", (target_info['id'],))
        if cursor.fetchone(): continue

        # 🚨 核心逻辑：沿着引用链向下穿透！
        quote_chain = []
        curr_node = tweet_node
        while True:
            q_res = curr_node.get('quoted_status_result', {}).get('result', {})
            # 处理 Twitter 嵌套数据结构的恶心点
            if q_res.get('__typename') == 'TweetWithVisibilityResults':
                q_res = q_res.get('tweet', {})
                
            if not q_res or 'legacy' not in q_res:
                break
                
            q_info = extract_tweet_node(q_res)
            # 插入到头部，保证最老的根节点在最前面
            quote_chain.insert(0, q_info)
            curr_node = q_res

        # 🖼️ 为链条上的每一个节点下载媒体文件
        all_nodes = quote_chain + [target_info]
        for node in all_nodes:
            member_media_dir = FACTORY_DIR / "media" / node['author']
            member_media_dir.mkdir(parents=True, exist_ok=True)
            local_media = []
            img_count = 1
            for media in node['media_files_raw']:
                if media['type'] == 'photo':
                    orig_url = media['media_url_https'] + "?name=orig"
                    filename = f"{node['id']}_img{img_count}.jpg"
                    if await download_media(orig_url, member_media_dir, filename):
                        local_media.append(str(member_media_dir / filename))
                    img_count += 1
                elif media['type'] in ['video', 'animated_gif']:
                    variants = media.get('video_info', {}).get('variants', [])
                    mp4_variants = [v for v in variants if v.get('content_type') == 'video/mp4' and 'bitrate' in v]
                    if mp4_variants:
                        best_video = sorted(mp4_variants, key=lambda x: x['bitrate'], reverse=True)[0]
                        vid_url = best_video['url']
                        filename = f"{node['id']}_video.mp4"
                        if await download_media(vid_url, member_media_dir, filename):
                            local_media.append(str(member_media_dir / filename))
            node['media'] = local_media

        # 只记录目标成员的 ID，外部节点的 ID 靠 dyn_map 去重
        cursor.execute("INSERT INTO tweets (tweet_id, author) VALUES (?, ?)", (target_info['id'], target_info['author']))
        conn.commit()

        # 将链条挂载到目标推文上
        target_info['quote_chain'] = quote_chain
        parsed_new_tweets.append(target_info)

    conn.close()
    if parsed_new_tweets: print(f"\n✅ 提纯与下载全部完成！共提取 {len(parsed_new_tweets)} 条全新动态。")
    return parsed_new_tweets

if __name__ == "__main__":
    raw_dir = FACTORY_DIR / "timeline_raw"
    json_files = list(raw_dir.glob("*.json"))
    if json_files:
        latest_json = max(json_files, key=os.path.getmtime)
        res = asyncio.run(parse_timeline_json(latest_json))
        print(f"\n测试返回数据预览: {json.dumps(res, ensure_ascii=False, indent=2)}")