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
    
    clean_jp_text = html.unescape(jp_text)
    clean_jp_text = re.sub(r'#(\w+)', r'#\1#', clean_jp_text)
    rag_context = rag.build_context_prompt(clean_jp_text)
    
    active_client, active_model = master_client, MASTER_MODEL
    system_prompt = settings.prompts.video_translation_prompt if is_subtitle else settings.prompts.tweet_translation_prompt
    
    try:
        messages_payload = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"请翻译以下推文：\n<text>\n{clean_jp_text}\n</text>\n\n{rag_context}"}
        ]
        
        import json
        logger.info(f"   -> [大模型通信探针] 完整 Request Payload:\n{json.dumps(messages_payload, ensure_ascii=False, indent=2)}")
        
        response = await active_client.chat.completions.create(
            model=active_model,
            messages=messages_payload,
            temperature=0.3, max_tokens=500
        )
        
        result = response.choices[0].message.content.strip()
        logger.info(f"   -> [大模型通信探针] Raw Response: '{result}'")
        
        # 🚨 T0 级防线：严禁返回空字符串！直接熔断！
        if not result:
            raise RuntimeError("LLM_TRANSLATION_FAILED: 大模型傲娇了，返回了极其致命的空字符串！")
            
        return result
        
    except RuntimeError as e:
        raise e  # 向上层（main.py）抛出，触发物理停机
    except Exception as e:
        logger.error(f"❌ 翻译失败: {e}")
        # 🚨 T0 级防线：API 崩溃或欠费，严禁返回生肉，直接熔断！
        raise RuntimeError(f"LLM_TRANSLATION_FAILED: API 请求崩溃或网络断连 - {e}")

# ==========================================
# 🚀 工业级批处理：整片视频台词一次性翻译
# ==========================================
async def translate_batch(segments: list, ocr_results: list) -> list:
    if not segments:
        return []

    input_lines = []
    full_text_for_rag = "" 
    
    for i, seg in enumerate(segments):
        start, end, text = seg['start'], seg['end'], seg['text'].strip()
        full_text_for_rag += text + " "
        
        hits = [o['text'] for o in ocr_results if not (o['end_time'] < start or o['start_time'] > end)]
        ocr_hint = f" [画面花字: {' | '.join(hits)}]" if hits else ""
        
        input_lines.append(f"{i+1}. {text}{ocr_hint}")

    script_text = "\n".join(input_lines)
    rag_context = rag.build_context_prompt(full_text_for_rag)
    
    active_client = master_client or worker_client
    active_model = MASTER_MODEL if master_client else WORKER_MODEL
    
    # 🚨 T0 级防线
    if not active_client:
        raise RuntimeError("LLM_TRANSLATION_FAILED: 未配置任何可用的 LLM 客户端！")

    logger.info(f"🧠 [智能路由] 正在将 {len(segments)} 句台本打包，移交【大师节点 {active_model}】...")
    system_prompt = settings.prompts.video_translation_prompt

    try:
        response = await active_client.chat.completions.create(
            model=active_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"请翻译以下台本：\n<text>\n{script_text}\n</text>\n\n{rag_context}"}
            ],
            temperature=0.2, 
            max_tokens=2000
        )
        
        output_text = response.choices[0].message.content.strip()
        translated_lines = []
        parsed_lines = re.split(r'\n\s*\d+\.\s*', '\n' + output_text)[1:] 
        
        for i in range(len(segments)):
            if i < len(parsed_lines) and parsed_lines[i].strip():
                translated_lines.append(parsed_lines[i].strip())
            else:
                translated_lines.append(segments[i]['text']) 
                
        return translated_lines
        
    except RuntimeError as e:
        raise e
    except Exception as e:
        logger.error(f"❌ 批量翻译请求崩溃: {e}")
        # 🚨 T0 级防线
        raise RuntimeError(f"LLM_TRANSLATION_FAILED: 批量翻译请求崩溃 - {e}")