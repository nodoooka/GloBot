import os
import sys
import html
import asyncio
import gradio as gr
from pathlib import Path

# ==========================================
# 环境初始化
# ==========================================
sys.path.append(str(Path(__file__).resolve().parent))

try:
    # 🌟 修复点：直接从你已经写好的 llm_translator 里借用实例和变量！
    from Bot_Media.llm_translator import master_client, MASTER_MODEL
    from Bot_Media.rag_manager import RAGManager
except ImportError as e:
    print(f"❌ 导入模块失败: {e}")
    sys.exit(1)

rag = RAGManager()

DEFAULT_SYSTEM_PROMPT = (
    "你是一个精通日本地下偶像文化的专业翻译。\n"
    "任务：请将日文推文翻译成中文，要求自然、符合年轻粉丝的语气。\n"
    "纪律1：严禁汉化成员名字！必须保持日文原文(罗马音)。\n"
    "纪律2：直接输出中文翻译结果，【必须完全保留原文中的 Emoji 和颜文字】。严禁输出任何多余的解释、问候语或机器感的前言！"
)

# ==========================================
# 核心翻译逻辑
# ==========================================
async def translate_preview(jp_text, sys_prompt):
    if not jp_text.strip():
        return "请输入原文", ""
    if not master_client:
        return "❌ 未配置 Master LLM 客户端，请检查 .env 文件", ""

    # 清洗并获取 RAG 上下文
    clean_jp_text = html.unescape(jp_text)
    rag_context = rag.build_context_prompt(clean_jp_text)

    messages_payload = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": f"请翻译以下内容：\n{clean_jp_text}\n\n{rag_context}"}
    ]

    try:
        response = await master_client.chat.completions.create(
            model=MASTER_MODEL,
            messages=messages_payload,
            temperature=0.3,
            max_tokens=500
        )
        result = response.choices[0].message.content.strip()
        return result, rag_context if rag_context.strip() else "未命中任何 RAG 词汇"
    except Exception as e:
        return f"❌ 翻译失败: {e}", rag_context

# ==========================================
# Gradio 优雅网页 UI (适配 Gradio 6.0+)
# ==========================================
with gr.Blocks(title="GloBot 提示词调校台") as demo:
    gr.Markdown(f"## 🤖 GloBot 翻译与提示词调校控制台 (模型: `{MASTER_MODEL}`)")
    
    with gr.Row():
        # 左侧输入区
        with gr.Column(scale=1):
            sys_prompt_input = gr.Textbox(label="🧠 System Prompt (系统提示词)", value=DEFAULT_SYSTEM_PROMPT, lines=6)
            jp_text_input = gr.Textbox(label="📝 待翻译日文原文", lines=5, placeholder="粘贴推文到这里...")
            translate_btn = gr.Button("🚀 发送给大模型进行测试", variant="primary")
            
        # 右侧输出区
        with gr.Column(scale=1):
            # 移除了 6.0 废弃的 show_copy_button
            result_output = gr.Textbox(label="✅ LLM 最终翻译结果", lines=6)
            rag_output = gr.Textbox(label="🔍 RAG 动态注入的词条", lines=5)

    # 绑定点击事件 (Gradio 自动处理 Async 函数)
    translate_btn.click(
        fn=translate_preview,
        inputs=[jp_text_input, sys_prompt_input],
        outputs=[result_output, rag_output]
    )

if __name__ == "__main__":
    print("🌐 正在启动本地 Web 控制台...")
    # 把 theme 参数移到了 launch 里
    demo.launch(inbrowser=True, theme=gr.themes.Soft())

    # 绑定点击事件 (Gradio 自动处理 Async 函数)
    translate_btn.click(
        fn=translate_preview,
        inputs=[jp_text_input, sys_prompt_input],
        outputs=[result_output, rag_output]
    )

if __name__ == "__main__":
    print("🌐 正在启动本地 Web 控制台...")
    # 启动网页，默认在 http://127.0.0.1:7860/
    demo.launch(inbrowser=True)