import json
import os
from pathlib import Path

KB_DIR = Path(os.getenv("LOCAL_DATA_DIR", "./GloBot_Data")) / "knowledge_base"
KB_DIR.mkdir(parents=True, exist_ok=True)

# ==========================================
# 1. 成员核心数据库 (废除中文名，全量替换罗马音)
# ==========================================
members_dict = {
    # 现役成员
    "あいす": {"romaji": "Aisu", "color": "白色", "nickname": "あいす"},
    "心花りり": {"romaji": "Konoka Riri", "color": "红色", "nickname": "りり"},
    "福丸うさ": {"romaji": "Fukumaru Usa", "color": "黄色", "nickname": "うさ"},
    "若葉のあ": {"romaji": "Wakaba Noa", "color": "绿色", "nickname": "のあ"},
    "空詩かれん": {"romaji": "Sonata Karen", "color": "蓝色", "nickname": "かれん"},
    "虹羽みに": {"romaji": "Kohane Mini", "color": "浅蓝色", "nickname": "みに"},
    "純嶺みき": {"romaji": "Sumire Miki", "color": "紫色", "nickname": "みき"},
    "小熊まむ": {"romaji": "Koguma Mamu", "color": "橙色", "nickname": "まむ"},
    "恋星はるか": {"romaji": "Konose Haruka", "color": "粉色", "nickname": "はるか"},
    
    # 过往成员
    "心咲めい": {"romaji": "Kokoro Mei", "color": "紫色", "nickname": "めい"},
    "双葉りつ": {"romaji": "Futaba Ritsu", "color": "绿色", "nickname": "りつ"},
    "桜餅ふゆ": {"romaji": "Sakuramochi Fuyu", "color": "粉色", "nickname": "ふゆ"},
    "琥珀しえる": {"romaji": "Kohaku Shieru", "color": "白色", "nickname": "しえる"},
    "甘音ゆあ": {"romaji": "Amane Yua", "color": "黄色", "nickname": "ゆあ"},
    "深月らむ": {"romaji": "Mizuki Ramu", "color": "蓝色", "nickname": "らむ"},
    "天羽しおり": {"romaji": "Amo Shiori", "color": "绿色", "nickname": "しおり"},
    "響羽リズ": {"romaji": "Otoha Rizu", "color": "绿色", "nickname": "リズ"},
    "向日えな": {"romaji": "Hinata Ena", "color": "粉色", "nickname": "えな"},
    "涼芽なの": {"romaji": "Suzume Nano", "color": "浅蓝色", "nickname": "なの"},
    "華瀬まい": {"romaji": "Hanase Mai", "color": "蓝色", "nickname": "まい"},
    "日日にこり": {"romaji": "Hibi Nicori", "color": "橙色", "nickname": "にこり"},
    "有栖るな": {"romaji": "Arisu Luna", "color": "紫色", "nickname": "るな"},
    "こるね": {"romaji": "Colne", "color": "橙色", "nickname": "こるね"},
    "那蘭のどか": {"romaji": "Nara Nodoka", "color": "粉色", "nickname": "のどか"},
    "恋春ねね": {"romaji": "Koharu Nene", "color": "粉色", "nickname": "ねね"}
}

# ==========================================
# 2. 官方宇宙词典
# ==========================================
lore_dict = {
    "iLiFE!": "iLiFE!",
    "アイライフ": "iLiFE!",
    "アイライファー": "iLiFER",
    "HEROINES": "HEROINES",
    "ヒロインズ": "HEROINES",
    "iTiger": "iTiger",
    "アイタイガー": "iTiger",
    "iRabbit": "iRabbit",
    "アイラビット": "iRabbit",
    "iON!": "iON!"
}

# ==========================================
# 3. 核心曲库
# ==========================================
songs_dict = {
    "星色トラベラー": "《星色旅行者》",
    "sweet timer": "《sweet timer》",
    "初恋リバイバル": "《初恋再体验》",
    "Sleeping face": "《Sleeping face》",
    "Quest×Quest": "《Quest×Quest》",
    "Dokkoi!ロマンティック": "《Dokkoi浪漫》",
    "黄昏サイクル": "《黄昏骑行》",
    "青春のパズルは埋まらない": "《青春的拼图永远不完整》",
    "キラメキダイアリー": "《闪耀日记》",
    "HUNGRY!!!": "《HUNGRY!!!》",
    "むげんだいすき": "《无止境最喜欢》",
    "君セン！": "《你来了！》",
    "可変三連MIXをおぼえる歌": "《可变三连学习曲》",
    "Ride On!": "《Ride On!》",
    "アイドルライフスターターパック": "《偶像生活新手包》",
    "Hands Up!": "《Hands Up!》",
    "Shout of Joy": "《Shout of Joy》",
    "ヒラリラリア": "《嘻嘻哈哈》",
    "会いにKiTE!": "《来见面吧！》",
    "KiSEK!": "《奇迹!》",
    "ころころガール": "《骨碌骨碌女孩》",
    "ドラマチックミライ": "《戏剧性未来》",
    "ナイナイ恋煩い♡": "《才没有相思病♡》",
    "アイドルライフブースターパック": "《偶像生活扩展包》",
    "サイクロンライフ！": "《旋风生活！》",
    "のびしろグリッター": "《成长的光芒》",
    "キスハグ侵略者！": "《亲吻拥抱侵略者》",
    "#ラブコード": "《#爱情代码》",
    "ガンバッテンダー": "《加油之歌》",
    "ライフステージ": "《人生舞台》",
    "デリバリサマー!!": "《外送夏天!!》",
    "アイドルライフエクストラパック": "《偶像生活额外包》",
    "メッセージ": "《Message》",
    "LOML": "《Love Of My Life》",
    "くりてぃかる♡ぷりちー": "《可爱度♡暴击》",
    "キュンとクラフト": "《心动攻略》",
    "メロメラ": "《心动爆燃》",
    "BRAVE GROOVE": "《BRAVE GROOVE》"
}

# ==========================================
# 4. 地下偶像通用日语黑话库 (纯净版)
# ==========================================
slang_dict = {
    "チェキ": "拍立得",
    "チェキ券": "拍立得券",
    "対バン": "拼盘Live",
    "ワンマン": "专场Live",
    "レス": "饭撒",
    "鍵開け": "开锁",
    "鍵閉め": "关锁",
    "TO": "顶流宅",
    "繋がり": "私联",
    "推し": "首推",
    "箱推し": "箱推",
    "生誕祭": "生诞祭",
    "リリイベ": "发售纪念活动",
    "フラゲ": "偷跑",
    "セトリ": "歌单",
    "現場": "Live现场",
    "遠征": "远征",
    "沸く": "嗨起来",
    "フリコピ": "跟跳",
    "認知": "认知",
    "剥がし": "推人",
    "ガチ恋": "真爱粉",
    "厄介": "厄介粉",
    "ペンライト": "应援棒",
    "キンブレ": "王剑"
}

# ==========================================
# 5. 东京圈场馆防伪库
# ==========================================
venues_dict = {
    "豊洲PIT": "丰洲PIT",
    "Zepp Shinjuku": "Zepp Shinjuku",
    "Zepp DiverCity": "Zepp DiverCity",
    "Zepp Haneda": "Zepp Haneda",
    "KT Zepp Yokohama": "KT Zepp Yokohama",
    "O-EAST": "Spotify O-EAST",
    "O-WEST": "Spotify O-WEST",
    "O-Crest": "Spotify O-Crest",
    "O-nest": "Spotify O-nest",
    "Club Asia": "Club Asia",
    "duo MUSIC EXCHANGE": "duo MUSIC EXCHANGE",
    "新宿ReNY": "新宿ReNY",
    "新宿BLAZE": "新宿BLAZE",
    "LIQUIDROOM": "LIQUIDROOM",
    "EX THEATER ROPPONGI": "EX THEATER ROPPONGI",
    "WWW X": "WWW X",
    "TDCホール": "TOKYO DOME CITY HALL",
    "LaLa arena": "LaLa arena TOKYO-BAY",
    "日本武道館": "日本武道馆",
    "代々木第一体育館": "代代木第一体育馆",
    "横浜アリーナ": "横滨体育场",
    "幕張メッセ": "幕张展览馆",
    "さいたまスーパーアリーナ": "埼玉超级竞技场"
}

# ==========================================
# 批量写出
# ==========================================
file_mapping = {
    "ilife_members.json": members_dict,
    "ilife_lore.json": lore_dict,
    "ilife_songs.json": songs_dict,
    "slang.json": slang_dict,
    "venues.json": venues_dict
}

for filename, data in file_mapping.items():
    filepath = KB_DIR / filename
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

print(f"✅ 罗马音纯净版集群初始化成功！共生成 {len(file_mapping)} 部大典！")