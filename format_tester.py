import os
import sys
import json
import html
import gradio as gr
from pathlib import Path
from dotenv import load_dotenv

sys.path.append(str(Path(__file__).resolve().parent))
load_dotenv()

try:
    from common.config_loader import settings, BILI_ACCOUNT_NAME
    from common.state_manager import load_dyn_map
    from Bot_Publisher.bili_formatter import build_repost_context, build_safe_dynamic_text
except ImportError as e:
    print(f"❌ 导入模块失败: {e}")
    sys.exit(1)

def fetch_local_data(c_id, p_id):
    dyn_map = load_dyn_map()
    
    c_trans, c_raw, c_handle, c_disp = "", "", "", ""
    c_node_type = "ORIGINAL"
    c_time = "2026-03-01 12:00:00"
    
    if c_id and c_id in dyn_map:
        c_info = dyn_map[c_id]
        if isinstance(c_info, dict):
            c_trans = c_info.get("translated_text", "")
            c_raw = c_info.get("raw_text", "")
            c_handle = c_info.get("author_handle", "")
            c_disp = c_info.get("author_display_name", "")
            c_node_type = c_info.get("node_type", "ORIGINAL")
            c_time = c_info.get("dt_str", "2026-03-01 12:00:00")
            if "is_reply" in c_info and c_info["is_reply"]: c_node_type = "REPLY"
            if not c_trans and not c_raw: c_node_type = "RETWEET"

    p_trans, p_raw, p_handle, p_disp = "", "", "", ""
    p_node_type, p_mode = "ORIGINAL", "repost"
    p_time = "2026-03-01 10:00:00"
    
    if p_id and p_id in dyn_map:
        p_info = dyn_map[p_id]
        if isinstance(p_info, dict):
            p_trans = p_info.get("translated_text", "")
            p_raw = p_info.get("raw_text", "")
            p_handle = p_info.get("author_handle", "")
            p_disp = p_info.get("author_display_name", "")
            p_node_type = p_info.get("node_type", "ORIGINAL")
            p_mode = p_info.get("publish_mode", "repost")
            p_time = p_info.get("dt_str", "2026-03-01 10:00:00")
            if "is_reply" in p_info and p_info["is_reply"]: p_node_type = "REPLY"

    return (c_trans, c_raw, c_handle, c_disp, c_node_type, c_time, 
            p_trans, p_raw, p_handle, p_disp, p_node_type, p_mode, p_time)

def simulate_assembly(c_id, c_trans, c_raw, c_handle, c_disp, c_node_type, c_time,
                      p_id, p_trans, p_raw, p_handle, p_disp, p_node_type, p_mode, p_time,
                      ret_level_str, channel_mode):
    try: ret_level = int(ret_level_str)
    except: ret_level = 0
        
    c_name = settings.targets.account_title_map.get(c_handle, c_disp) if c_handle else c_disp
    is_video = channel_mode.startswith("视频")
    limit = 220 if is_video else 950
    ref_link_mock = "https://t.bilibili.com/12345678" if p_id else ""
    
    context_suffix = build_repost_context(p_id, dyn_map={p_id: {"publish_mode": p_mode, "author_handle": p_handle, "author_display_name": p_disp, "node_type": p_node_type, "dt_str": p_time, "translated_text": p_trans, "raw_text": p_raw}}, settings_obj=settings, id_retention_level=ret_level, is_video_mode=is_video)
    
    # 🚨 调用中心引擎的 debug 模式
    final_content, degrade_status = build_safe_dynamic_text(
        c_name, c_time, c_trans, c_raw, c_id, c_node_type, ret_level, context_suffix, ref_link_mock, limit, debug_status=True
    )
    
    out_title = "" if c_node_type in ["REPLY", "RETWEET"] else c_name
    action = f"{degrade_status} (实际字数: {len(final_content)} / 上限: {limit})"

    return out_title, final_content, action

with gr.Blocks(title="GloBot 排版沙盒") as demo:
    gr.Markdown(f"## 🛠️ GloBot 动态排版拼装沙盒 (当前挂载号: `@{BILI_ACCOUNT_NAME}`)")
    
    with gr.Row():
        curr_id_in = gr.Textbox(label="1. 当前推文 ID", placeholder="输入你想要测试的推文ID...")
        prev_id_in = gr.Textbox(label="2. 上一环推文 ID (选填)", placeholder="填入以测试防套娃拼接...")
        fetch_btn = gr.Button("🔍 从本地缓存提取物理数据", variant="primary")
        
    gr.Markdown("---")
    
    with gr.Row():
        with gr.Column(scale=1):
            gr.Markdown("### 📝 当前推文干预区")
            curr_trans = gr.Textbox(label="翻译文本", lines=3)
            curr_raw = gr.Textbox(label="日文原文", lines=3)
            with gr.Row():
                curr_handle = gr.Textbox(label="原生 Handle")
                curr_disp = gr.Textbox(label="原生显示昵称")
            with gr.Row():
                curr_node_type = gr.Dropdown(label="👑 原生 DNA (node_type)", choices=["ORIGINAL", "QUOTE", "REPLY", "RETWEET"], value="ORIGINAL")
            curr_time = gr.Textbox(label="时间戳")

        with gr.Column(scale=1):
            gr.Markdown("### 🔗 上一环干预区 (被拼接对象)")
            prev_trans = gr.Textbox(label="翻译文本", lines=3)
            prev_raw = gr.Textbox(label="日文原文", lines=3)
            with gr.Row():
                prev_handle = gr.Textbox(label="原生 Handle")
                prev_disp = gr.Textbox(label="原生显示昵称")
            with gr.Row():
                prev_node_type = gr.Dropdown(label="👑 原生 DNA", choices=["ORIGINAL", "QUOTE", "REPLY", "RETWEET"], value="ORIGINAL")
                prev_mode = gr.Dropdown(label="🚨 发包模式", choices=["original", "repost"], value="repost")
            prev_time = gr.Textbox(label="时间戳")
            
    gr.Markdown("---")
    
    with gr.Row():
        channel_mode = gr.Radio(label="发包模拟通道", choices=["普通图文/转发 (1000字)", "视频投稿 (220字)"], value="普通图文/转发 (1000字)")
        retention_level = gr.Dropdown(label="全局隐藏等级", choices=["0", "1", "2", "3"], value="0")
        sim_btn = gr.Button("🚀 执行阶梯裁切渲染", variant="primary", size="lg")
        
    gr.Markdown("---")
    
    with gr.Row():
        with gr.Column(scale=1):
            out_action = gr.Textbox(label="发包动作与安全等级预测")
            out_title = gr.Textbox(label="B站原生标题 (Title)")
        with gr.Column(scale=2):
            out_content = gr.Textbox(label="最终发向 B 站的正文 (Content)", lines=12)

    fetch_btn.click(fn=fetch_local_data, inputs=[curr_id_in, prev_id_in], outputs=[curr_trans, curr_raw, curr_handle, curr_disp, curr_node_type, curr_time, prev_trans, prev_raw, prev_handle, prev_disp, prev_node_type, prev_mode, prev_time])
    sim_btn.click(fn=simulate_assembly, inputs=[curr_id_in, curr_trans, curr_raw, curr_handle, curr_disp, curr_node_type, curr_time, prev_id_in, prev_trans, prev_raw, prev_handle, prev_disp, prev_node_type, prev_mode, prev_time, retention_level, channel_mode], outputs=[out_title, out_content, out_action])

if __name__ == "__main__":
    print(f"🌐 正在启动排版沙盒环境，已加载账号: @{BILI_ACCOUNT_NAME} ...")
    demo.launch(inbrowser=True, theme=gr.themes.Soft())