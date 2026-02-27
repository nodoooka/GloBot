import json
import os
from pathlib import Path
import sys

# 将项目根目录加入系统路径
sys.path.append(str(Path(__file__).resolve().parent.parent))
from common.config_loader import settings

class RAGManager:
    """动态知识库提取器：毫秒级扫描文本，精准投喂，极致节省 Token"""
    
    def __init__(self):
        # 定位到我们刚刚生成的 knowledge_base 目录
        self.kb_dir = Path(os.getenv("LOCAL_DATA_DIR", "./GloBot_Data")) / "knowledge_base"
        
        # 预加载所有 5 部大典到物理内存
        self.members = self._load_json("ilife_members.json")
        self.songs = self._load_json("ilife_songs.json")
        self.lore = self._load_json("ilife_lore.json")
        self.slang = self._load_json("slang.json")
        self.venues = self._load_json("venues.json")

    def _load_json(self, filename: str) -> dict:
        filepath = self.kb_dir / filename
        if filepath.exists():
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        return {}

    def build_context_prompt(self, target_text: str) -> str:
        """
        核心黑科技：对输入文本进行毫秒级 X 光扫描。
        如果命中知识库，则提取该词并组装成 LLM 强化提示词。
        """
        if not target_text:
            return ""

        matched_members = []
        matched_songs = []
        matched_lore = []
        matched_slang = []
        matched_venues = []

        # 1. 扫描成员 (严格遵循：日文原文保留，加注罗马音)
        for full_name, info in self.members.items():
            nickname = info.get("nickname", "")
            romaji = info.get("romaji", "")
            
            # 如果命中了全名
            if full_name in target_text:
                matched_members.append(f"- 原文「{full_name}」 -> 翻译为：{full_name}({romaji})")
            # 如果只命中了昵称
            elif nickname and nickname in target_text:
                matched_members.append(f"- 原文「{nickname}」 -> 翻译为：{nickname}({romaji})")

        # 2. 扫描曲库
        for jp_song, cn_song in self.songs.items():
            if jp_song in target_text:
                matched_songs.append(f"- 【{jp_song}】 -> {cn_song}")

        # 3. 扫描宇宙观黑话
        for jp_lore, cn_lore in self.lore.items():
            if jp_lore in target_text:
                matched_lore.append(f"- 【{jp_lore}】 -> {cn_lore}")

        # 4. 扫描地下偶像通用黑话
        for jp_slang, cn_slang in self.slang.items():
            if jp_slang in target_text:
                matched_slang.append(f"- 【{jp_slang}】 -> {cn_slang}")

        # 5. 扫描场馆
        for jp_venue, cn_venue in self.venues.items():
            if jp_venue in target_text:
                matched_venues.append(f"- 【{jp_venue}】 -> {cn_venue}")

        # ==========================================
        # 组装终极 Buff 提示词 (极其严厉的 Prompt 工程)
        # ==========================================
        if not any([matched_members, matched_songs, matched_lore, matched_slang, matched_venues]):
            return ""  # 没命中任何词汇，不消耗额外 Token

        prompt_blocks = ["\n\n【==== 专属知识库强制规范 ====】\n请在翻译时严格参照以下提取到的专有名词映射表："]

        # 成员名称属于最高优先级，必须加上极其严厉的纪律警告
        if matched_members:
            prompt_blocks.append("\n[1] 成员名字强制拼接公式：")
            prompt_blocks.append("如果原文名字带有接尾辞（如ちゃん、ちー等），【必须】严格按照此公式拼接：日文名 + (罗马音) + 中文接尾辞。")
            prompt_blocks.append("【绝对禁止】省略罗马音！例如：原文如果是「まむちー」，必须无条件输出为「まむ(Koguma Mamu)亲～」，禁止自作主张优化排版！")
            prompt_blocks.extend(matched_members)

        if matched_songs:
            prompt_blocks.append("\n[2] 官方曲目：")
            prompt_blocks.extend(matched_songs)

        if matched_lore:
            prompt_blocks.append("\n[3] 官方宇宙专有名词：")
            prompt_blocks.extend(matched_lore)

        if matched_slang:
            prompt_blocks.append("\n[4] 饭圈文化词汇：")
            prompt_blocks.extend(matched_slang)

        if matched_venues:
            prompt_blocks.append("\n[5] 线下场馆：")
            prompt_blocks.extend(matched_venues)

        prompt_blocks.append("【========================】\n")
        
        return "\n".join(prompt_blocks)

# ==========================================
# 🧪 测试防线
# ==========================================
if __name__ == "__main__":
    rag = RAGManager()
    
    # 模拟一段极其硬核、包含多重黑话和成员昵称的推文
    test_tweet = "今日はZepp Shinjukuでの対バンありがとう！かれんのレス最高だった！次回のワンマンも楽しみ！チェキ撮ろうね！セトリにアイドルライフスターターパックがあって沸いた！"
    
    print(f"📄 待翻译原文:\n{test_tweet}\n")
    print("🔍 正在经过 RAG 动态 X 光扫描...\n")
    
    context_prompt = rag.build_context_prompt(test_tweet)
    
    print(f"🤖 即将附带给大模型的精简强制 Context:{context_prompt}")