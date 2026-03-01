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

def extract_tweet_node(node):
    tweet_id = str(node.get('rest_id', ''))
    legacy = node.get('legacy', {})
    raw_screen_name = find_key(node.get('core', {}), 'screen_name')
    author_screen_name = str(raw_screen_name).lower() if raw_screen_name else ''
    
    # 精准提取推特账号的真实显示名称
    raw_display_name = find_key(node.get('core', {}), 'name')
    author_display_name = str(raw_display_name) if raw_display_name else f"@{author_screen_name}"    
    
    # 精准提取底层的评论回复对象属性
    raw_reply_name = legacy.get('in_reply_to_screen_name')
    in_reply_to_screen_name = str(raw_reply_name).lower() if raw_reply_name else None
    in_reply_to_status_id_str = legacy.get('in_reply_to_status_id_str')
    
    full_text = legacy.get('full_text', '')
    try:
        if 'note_tweet_results' in node:
            nt = node['note_tweet_results'].get('result', {}).get('text', '')
            if nt: full_text = nt
        elif 'note_tweet' in node: 
            nt = node['note_tweet'].get('note_tweet_results', {}).get('result', {}).get('text', '')
            if nt: full_text = nt
    except: pass
        
    # 1. 干掉推特自带的 t.co 短链
    full_text = re.sub(r'https?://t\.co/\w+', '', full_text).strip()
    
    # 2. 🚨 仅在“评论回复”场景下切除底层强制塞入的 @账号标签
    if legacy.get('in_reply_to_status_id_str'):
        full_text = re.sub(r'^(@\w+\s*)+', '', full_text).strip()

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
        'author_display_name': author_display_name,
        'text': full_text,
        'media_files_raw': media_files,
        'timestamp': timestamp_sec,
        'in_reply_to_screen_name': in_reply_to_screen_name,
        'in_reply_to_status_id_str': in_reply_to_status_id_str,
        'raw_node': node
    }

async def parse_timeline_json(json_file_path: Path) -> list:
    print(f"🔬 正在化验矿石: {json_file_path.name}")
    with open(json_file_path, "r", encoding="utf-8") as f: data = json.load(f)

    conn = init_db()
    cursor = conn.cursor()
    target_accounts = [acc.lower() for acc in settings.targets.x_accounts]
    parsed_new_tweets = []

    # 🌟 微创注入：建立全局节点缓存池，支持跨层级无限追溯回复！
    all_raw_tweets = list(find_tweets(data))
    all_nodes_dict = {}
    for t_node in all_raw_tweets:
        n_info = extract_tweet_node(t_node)
        all_nodes_dict[n_info['id']] = n_info

    for tweet_node in all_raw_tweets:
        target_info = extract_tweet_node(tweet_node)
        
        if target_info['author'] not in target_accounts: continue
            
        quote_chain = []
        target_info['is_reply'] = False
        target_info['is_pure_retweet'] = False

        cursor.execute("SELECT 1 FROM tweets WHERE tweet_id = ?", (target_info['id'],))
        if cursor.fetchone(): continue

        curr_node = tweet_node

        # ==========================================
        # 🚨 痛点修复 1：完美识别纯转推，自动将原推提权为祖先！
        # ==========================================
        if 'retweeted_status_result' in tweet_node.get('legacy', {}):
            target_info['is_pure_retweet'] = True
            target_info['text'] = ""
            target_info['media_files_raw'] = []
            
            rt_res = tweet_node['legacy']['retweeted_status_result'].get('result', {})
            if rt_res.get('__typename') == 'TweetWithVisibilityResults':
                rt_res = rt_res.get('tweet', {})
                
            if rt_res and 'legacy' in rt_res:
                rt_info = extract_tweet_node(rt_res)
                rt_info['is_reply'] = False
                rt_info['is_placeholder'] = False
                quote_chain.insert(0, rt_info)
                curr_node = rt_res  # 让底部的 while 循环继续去挖原推是否也带引用
            else:
                continue # 如果转推的原推已被删除，直接丢弃
        else:
            # ==========================================
            # 🚨 痛点修复 2：评论区回复的过滤与套娃溯源
            # ==========================================
            reply_to_user = target_info.get('in_reply_to_screen_name')
            if reply_to_user:
                if reply_to_user not in target_accounts:
                    continue # 彻底忽略对外部路人的普通回复
                else:
                    target_info['is_reply'] = True
                    curr_reply_id = target_info.get('in_reply_to_status_id_str')
                    
                    while curr_reply_id:
                        if curr_reply_id in all_nodes_dict:
                            anc_info = dict(all_nodes_dict[curr_reply_id]) 
                            anc_info['is_reply'] = True
                            anc_info['is_placeholder'] = False
                            quote_chain.insert(0, anc_info)
                            curr_reply_id = anc_info.get('in_reply_to_status_id_str')
                        else:
                            quote_chain.insert(0, {
                                'id': curr_reply_id,
                                'author': reply_to_user,
                                'author_display_name': f"@{reply_to_user}",
                                'text': "(回复溯源占位符)",
                                'timestamp': target_info['timestamp'] - 1,
                                'media_files_raw': [],
                                'is_reply': True,
                                'is_placeholder': True
                            })
                            break

        # 向上深挖可能存在的多层引用嵌套
        while True:
            q_res = curr_node.get('quoted_status_result', {}).get('result', {})
            if q_res.get('__typename') == 'TweetWithVisibilityResults':
                q_res = q_res.get('tweet', {})
                
            if not q_res or 'legacy' not in q_res:
                break
                
            q_info = extract_tweet_node(q_res)
            q_info['is_reply'] = False
            q_info['is_placeholder'] = False
            
            if not quote_chain and not target_info['is_pure_retweet']:
                target_info['quoted_tweet_id'] = q_info['id']
                target_info['quoted_text'] = q_info['text']
                
            quote_chain.insert(0, q_info)
            curr_node = q_res

        # 🖼️ 为链条上的每一个真实节点下载媒体文件
        all_nodes = quote_chain + [target_info]
        for node in all_nodes:
            if node.get('is_placeholder') or node.get('is_pure_retweet'):
                node['media'] = []
                continue

            member_media_dir = FACTORY_DIR / "media" / node['author']
            member_media_dir.mkdir(parents=True, exist_ok=True)
            local_media = []
            img_count = 1
            alt_texts = [] 
            
            for media in node['media_files_raw']:
                if media['type'] == 'photo':
                    orig_url = media['media_url_https'] + "?name=orig"
                    filename = f"{node['id']}_img{img_count}.jpg"
                    if await download_media(orig_url, member_media_dir, filename):
                        local_media.append(str(member_media_dir / filename))
                    
                    alt = media.get('ext_alt_text')
                    if alt:
                        alt_texts.append(f"【图{img_count}附言】\n{alt.strip()}")
                        
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
            
            if alt_texts:
                node['text'] += "\n\n" + "\n\n".join(alt_texts)

        cursor.execute("INSERT INTO tweets (tweet_id, author) VALUES (?, ?)", (target_info['id'], target_info['author']))
        conn.commit()

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