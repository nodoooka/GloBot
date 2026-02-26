import os
import asyncio
import logging
from pathlib import Path
from openai import AsyncOpenAI
import sys
import re
import html

sys.path.append(str(Path(__file__).resolve().parent.parent))
from common.config_loader import settings, MASTER_LLM_API_KEY, WORKER_GLM_API_KEY
from Bot_Media.rag_manager import RAGManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

WORKER_BASE_URL = os.getenv("WORKER_BASE_URL", "https://open.bigmodel.cn/api/paas/v4/")
WORKER_MODEL = os.getenv("WORKER_MODEL", "glm-4-flash")

MASTER_BASE_URL = os.getenv("MASTER_BASE_URL", "https://api.deepseek.com") 
MASTER_MODEL = os.getenv("MASTER_MODEL", "deepseek-chat")

worker_client = AsyncOpenAI(api_key=WORKER_GLM_API_KEY, base_url=WORKER_BASE_URL) if WORKER_GLM_API_KEY else None
master_client = AsyncOpenAI(api_key=MASTER_LLM_API_KEY, base_url=MASTER_BASE_URL) if MASTER_LLM_API_KEY else None

rag = RAGManager()

# ==========================================
# 🚀 单句翻译（保留给推文处理使用）
# ==========================================
async def translate_text(jp_text: str, is_subtitle: bool = False) -> str:
    if not jp_text.strip(): return ""
    
    # 🧹 清洗推特底层的 HTML 转义字符 (如将 &lt; 还原为 < )，防止大模型抽风
    clean_jp_text = html.unescape(jp_text)
    
    # 🏷️ 核心修复：用正则提前将推特单井号标签 #tag 转换为 B站双井号 #tag#
    # 这样不仅适配了 B站格式，还能打断 Markdown 的标题语法，防止大模型跳过该行！
    clean_jp_text = re.sub(r'#(\w+)', r'#\1#', clean_jp_text)
    
    rag_context = rag.build_context_prompt(clean_jp_text)
    
    # 强制测试：无视长短，所有推文全部交给 Master 模型 (DeepSeek/GPT 等) 处理！
    active_client, active_model = master_client, MASTER_MODEL

    # 🧠 核心重构：从 config.yaml 中动态读取对应的提示词
    system_prompt = settings.prompts.video_translation_prompt if is_subtitle else settings.prompts.tweet_translation_prompt
    
    try:
        # 1. 组装要发送的完整消息体（加入物理边界符隔离）
        messages_payload = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"请翻译以下推文：\n<text>\n{clean_jp_text}\n</text>\n\n{rag_context}"}
        ]
        
        # 2. 🚦 增加通信探针：打印即将发给大模型的完整 JSON
        import json
        logger.info(f"   -> [大模型通信探针] 完整 Request Payload:\n{json.dumps(messages_payload, ensure_ascii=False, indent=2)}")
        
        # 3. 发送请求
        response = await active_client.chat.completions.create(
            model=active_model,
            messages=messages_payload,
            temperature=0.3, max_tokens=500
        )
        
        result = response.choices[0].message.content.strip()
        
        # 4. 🚦 增加响应探针：打印大模型真实返回的 Raw Data
        logger.info(f"   -> [大模型通信探针] Raw Response: '{result}'")
        
        if not result:
            logger.warning(f"⚠️ 大模型傲娇了，返回了空字符串！触发防爆兜底，直接使用清洗后的原文。")
            return clean_jp_text
            
        return result
    except Exception as e:
        logger.error(f"❌ 翻译失败: {e}")
        # 如果断网或 API 欠费，依然用原文兜底
        return html.unescape(jp_text)

# ==========================================
# 🚀 工业级批处理：整片视频台词一次性翻译
# ==========================================
async def translate_batch(segments: list, ocr_results: list) -> list:
    """将整段视频字幕打包，一次性交由 Master 模型进行上下文感知翻译"""
    if not segments:
        return []

    # 1. 组装带有 OCR 视觉上下文的带序号剧本
    input_lines = []
    full_text_for_rag = "" # 用于一次性提取所有知识库Buff
    
    for i, seg in enumerate(segments):
        start, end, text = seg['start'], seg['end'], seg['text'].strip()
        full_text_for_rag += text + " "
        
        # 匹配该时间段的花字
        hits = [o['text'] for o in ocr_results if not (o['end_time'] < start or o['start_time'] > end)]
        ocr_hint = f" [画面花字: {' | '.join(hits)}]" if hits else ""
        
        input_lines.append(f"{i+1}. {text}{ocr_hint}")

    script_text = "\n".join(input_lines)
    rag_context = rag.build_context_prompt(full_text_for_rag)
    
    # 整片翻译属于重度推理任务，强制路由给 Master 节点（或降级）
    active_client = master_client or worker_client
    active_model = MASTER_MODEL if master_client else WORKER_MODEL
    
    if not active_client:
        return [seg['text'] for seg in segments]

    logger.info(f"🧠 [智能路由] 正在将 {len(segments)} 句台本打包，移交【大师节点 {active_model}】...")

    # 🧠 核心重构：直接读取视频专属的提示词
    system_prompt = settings.prompts.video_translation_prompt

    try:
        response = await active_client.chat.completions.create(
            model=active_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"请翻译以下台本：\n<text>\n{script_text}\n</text>\n\n{rag_context}"}
            ],
            temperature=0.2, # 较低的温度确保结构稳定
            max_tokens=2000
        )
        
        output_text = response.choices[0].message.content.strip()
        
        # 2. 解析大模型返回的带序号文本
        translated_lines = []
        # 使用正则匹配序号，确保鲁棒性
        parsed_lines = re.split(r'\n\s*\d+\.\s*', '\n' + output_text)[1:] 
        
        # 如果模型极其听话没出岔子，长度应该和 segments 一致
        for i in range(len(segments)):
            if i < len(parsed_lines) and parsed_lines[i].strip():
                translated_lines.append(parsed_lines[i].strip())
            else:
                translated_lines.append(segments[i]['text']) # 兜底：如果少翻了就用日文原文
                
        return translated_lines
        
    except Exception as e:
        logger.error(f"❌ 批量翻译请求崩溃: {e}")
        return [seg['text'] for seg in segments] # 彻底断网的兜底方案