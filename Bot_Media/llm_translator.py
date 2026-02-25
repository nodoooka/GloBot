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

# 单句翻译（保留给推文处理使用）
async def translate_text(jp_text: str, is_subtitle: bool = False) -> str:
    if not jp_text.strip(): return ""
    
    # 🧹 清洗推特底层的 HTML 转义字符 (如将 &lt; 还原为 < )，防止大模型抽风
    clean_jp_text = html.unescape(jp_text)
    
    rag_context = rag.build_context_prompt(clean_jp_text)
    
    # 🚀 强制测试：无视长短，所有推文全部交给 Master 模型 (DeepSeek/GPT 等) 处理！
    active_client, active_model = master_client, MASTER_MODEL

    system_prompt = (
        "你是一个精通日本地下偶像文化的专业翻译。\n"
        "任务：请将日文推文翻译成中文，要求自然、符合年轻粉丝的语气。\n"
        "纪律1：严禁汉化成员名字！必须保持日文原文(罗马音)。\n"
        "纪律2：直接输出中文翻译结果，【必须完全保留原文中的 Emoji 和颜文字】。严禁输出任何多余的解释、问候语或机器感的前言！"
    )
    
    try:
        # 1. 组装要发送的完整消息体
        messages_payload = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"请翻译以下内容：\n{clean_jp_text}\n\n{rag_context}"}
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

    system_prompt = (
        "你是一个资深影视本地化翻译兼地下偶像字幕组组长。请翻译以下视频台本。\n"
        "【输入格式】每行一个序号，包含“听觉语音”以及方括号内的“[画面花字: 日文]”。\n"
        "【翻译基准：导演思维与画面降噪】\n"
        "1. 【听觉语音】优先级极高，同时也要参考画面花字。但你必须具备“人类观众的过滤本能”，如果画面花字的日文不是有明确含义的内容，或内容与上下文明显无关则会将其忽略！\n"
        "2. 视频画面中经常会扫到各种应用界面、演职人员表或系统UI（如：主演、导演、电话号码、时间戳“今日 7:07”、系统提示“テキストメッセージ”等）。【绝对禁止】将这些无意义的UI碎片翻译出来！\n"
        "3. 你必须精准提取画面花字中最核心的“剧情正文”（如短信的真实内容），忽略所有UI干扰项，并与听觉语音结合，【提炼、融合成唯一一句符合人类说话习惯的中文字幕】。\n"
        "【最高输出纪律】\n"
        "1. 严格按输入序号逐行返回，格式必须为：`序号. 中文翻译`。\n"
        "2. 【绝对封杀机器味】：输出的最终字幕中，【绝对禁止】出现方括号 `[]`、竖线 `|` 等任何程序化的拼接符号！【绝对禁止】像报流水账一样列出画面元素！\n"
        "3. 【彻底汉化法则】：日常称呼（如お姉ちゃん、先輩等）和普通名词必须翻译成中文！只有真正的偶像名字允许保留“日文原文(罗马音)”。\n"
        "4. 输出中除偶像本人的名字外，【绝对禁止】出现其他日文，包括片假名和平假名！\n"
        "5. 我只需要你吐出最终那一句精炼、纯净、有网感的中文字幕单句！\n"
        "6. 在输出之前，我要你最后站在全局角度确认一遍全文翻译，根据上下文修正明显的错误，再一次确认除了真正的偶像名字外禁止出现非中文内容！"
    )

    try:
        response = await active_client.chat.completions.create(
            model=active_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"请翻译以下台本：\n\n{script_text}\n\n{rag_context}"}
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