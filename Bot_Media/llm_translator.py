import os
import asyncio
import logging
from pathlib import Path
from openai import AsyncOpenAI
import sys
import re
import html
import json
from pydantic import BaseModel, Field, ValidationError

sys.path.append(str(Path(__file__).resolve().parent.parent))
from common.config_loader import settings, MASTER_LLM_API_KEY, WORKER_GLM_API_KEY
from Bot_Media.rag_manager import RAGManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# 👇 补回刚才遗漏的环境变量读取！
WORKER_BASE_URL = os.getenv("WORKER_BASE_URL", "https://open.bigmodel.cn/api/paas/v4/")
WORKER_MODEL = os.getenv("WORKER_MODEL", "glm-4-flash")

MASTER_BASE_URL = os.getenv("MASTER_BASE_URL", "https://api.deepseek.com") 
MASTER_MODEL = os.getenv("MASTER_MODEL", "deepseek-chat")

# 彻底关闭官方默认套娃重试，由我们全权接管控制流
worker_client = AsyncOpenAI(
    api_key=WORKER_GLM_API_KEY, 
    base_url=WORKER_BASE_URL, 
    timeout=180.0, 
    max_retries=0
) if WORKER_GLM_API_KEY else None

master_client = AsyncOpenAI(
    api_key=MASTER_LLM_API_KEY, 
    base_url=MASTER_BASE_URL, 
    timeout=180.0, 
    max_retries=0
) if MASTER_LLM_API_KEY else None

rag = RAGManager()

# 强类型校验结构
class SubtitleLine(BaseModel):
    id: int = Field(..., description="台词的序号")
    text: str = Field(..., description="翻译后的中文内容")

class SubtitleBatch(BaseModel):
    lines: list[SubtitleLine]

# ==========================================
# 🚀 单句翻译（引入 SSE 流式防断连装甲）
# ==========================================
async def translate_text(jp_text: str, is_subtitle: bool = False) -> str:
    if not jp_text.strip(): return ""
    
    clean_jp_text = html.unescape(jp_text)
    clean_jp_text = re.sub(r'#(\w+)', r'#\1#', clean_jp_text)
    rag_context = rag.build_context_prompt(clean_jp_text)
    
    active_client, active_model = master_client, MASTER_MODEL
    system_prompt = settings.prompts.video_translation_prompt if is_subtitle else settings.prompts.tweet_translation_prompt
    
    for attempt in range(3):
        try:
            response = await active_client.chat.completions.create(
                model=active_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"请翻译以下推文：\n<text>\n{clean_jp_text}\n</text>\n\n{rag_context}"}
                ],
                temperature=0.3 + (attempt * 0.1),
                max_tokens=500,
                stream=True  # 👈 核心救命稻草：开启流式传输，防网关 30 秒强杀
            )
            
            result_chunks = []
            async for chunk in response:
                if chunk.choices and len(chunk.choices) > 0:
                    delta = chunk.choices[0].delta
                    if hasattr(delta, 'content') and delta.content:
                        result_chunks.append(delta.content)
            
            result = "".join(result_chunks).strip()
            
            # 清理可能被思考模型暴露出来的 <think> 标签内容
            result = re.sub(r'<think>.*?</think>', '', result, flags=re.DOTALL).strip()
            
            if "<!DOCTYPE html>" in result[:50].lower():
                raise RuntimeError(f"API 网关返回了异常网页: {result[:100]}...")

            if not result:
                raise RuntimeError("LLM 返回了空字符串")
            
            # 👇 把这句话加上，以后它重试成功了你就看得到了
            logger.info(f"   -> [单句翻译] 成功获取文本 ({len(result)}字)")
                
            return result
            
        except Exception as e:
            logger.warning(f"⚠️ [单句翻译] 第 {attempt+1} 次失败: {e}")
            if attempt == 2:
                logger.error(f"❌ 翻译彻底崩溃")
                raise RuntimeError(f"LLM_TRANSLATION_FAILED: API 请求崩溃 - {e}")
            await asyncio.sleep(1.5 ** attempt)

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
    
    if not active_client:
        raise RuntimeError("LLM_TRANSLATION_FAILED: 未配置任何可用的 LLM 客户端！")

    json_instruction = (
        "\n\n【输出格式强制要求】\n"
        "必须返回合法的 JSON 格式对象，不要使用任何 Markdown 代码块包裹，格式如下：\n"
        "{\n  \"lines\": [\n    {\"id\": 1, \"text\": \"第一句翻译\"},\n    {\"id\": 2, \"text\": \"第二句翻译\"}\n  ]\n}"
    )

    logger.info(f"🧠 [智能路由] 正在将 {len(segments)} 句台本打包，移交【大师节点 {active_model}】进行流式长连接翻译...")
    system_prompt = settings.prompts.video_translation_prompt

    for attempt in range(3):
        try:
            response = await active_client.chat.completions.create(
                model=active_model,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"请翻译以下台本：\n<text>\n{script_text}\n</text>\n\n{rag_context}{json_instruction}"}
                ],
                temperature=0.2 + (attempt * 0.1), 
                max_tokens=2000,
                stream=True  # 👈 流式防封杀
            )
            
            output_chunks = []
            async for chunk in response:
                if chunk.choices and len(chunk.choices) > 0:
                    delta = chunk.choices[0].delta
                    if hasattr(delta, 'content') and delta.content:
                        output_chunks.append(delta.content)
            
            output_text = "".join(output_chunks).strip()
            output_text = re.sub(r'<think>.*?</think>', '', output_text, flags=re.DOTALL).strip()
            
            if "<!DOCTYPE html>" in output_text[:50].lower():
                raise RuntimeError(f"API 网关阻断返回 HTML 页面: {output_text[:100]}...")
            
            if output_text.startswith("```json"): output_text = output_text[7:-3].strip()
            elif output_text.startswith("```"): output_text = output_text[3:-3].strip()
                
            parsed_data = json.loads(output_text)
            batch = SubtitleBatch(**parsed_data)
            
            translated_lines = []
            line_map = {item.id: item.text for item in batch.lines}
            
            for i in range(len(segments)):
                line_id = i + 1
                if line_id in line_map and line_map[line_id].strip():
                    translated_lines.append(line_map[line_id].strip())
                else:
                    translated_lines.append(segments[i]['text'])
                    
            logger.info(f"✅ [JSON 批量] 成功完美映射 {len(translated_lines)} 句台词。")
            return translated_lines
            
        except (json.JSONDecodeError, ValidationError) as e:
            logger.warning(f"⚠️ [批量翻译] 第 {attempt+1} 次解析失败: 格式损坏。")
            if attempt == 2:
                logger.error("❌ 批量翻译彻底失败，降级为全原文输出。")
                return [seg['text'] for seg in segments]
            await asyncio.sleep(2 ** attempt)
            
        except Exception as e:
            logger.error(f"❌ 批量请求网络异常: {e}")
            if attempt == 2:
                raise RuntimeError(f"LLM_TRANSLATION_FAILED: 批量请求 3 次崩溃 - {e}")
            await asyncio.sleep(1.5 ** attempt)
            
    return [seg['text'] for seg in segments]