import os
import sys
import json
import html
import gradio as gr
from pathlib import Path
from dotenv import load_dotenv

# ==========================================
# ⚙️ 环境与核心依赖初始化
# ==========================================
sys.path.append(str(Path(__file__).resolve().parent))
load_dotenv()

try:
    from common.config_loader import settings
    from common.text_sanitizer import sanitize_for_bilibili
except ImportError as e:
    print(f"❌ 导入模块失败: {e}")
    sys.exit(1)

BILI_ACCOUNT_NAME = os.getenv("BILI_ACCOUNT_NAME", "GloBot搬运")
DATA_DIR = Path(os.getenv("LOCAL_DATA_DIR", f"./GloBot_Data/{settings.targets.group_name}"))
DYN_MAP_FILE = DATA_DIR / "dyn_map.json"

def load_dyn_map():
    if not DYN_MAP_FILE.exists(): return {}
    try:
        with open(DYN_MAP_FILE, "r", encoding="utf-8") as f: return json.load(f)
    except: return {}

# ==========================================
# 🔍 数据提取引擎
# ==========================================
def fetch_local_data(c_id, p_id):
    dyn_map = load_dyn_map()
    
    c_trans, c_raw, c_handle, c_disp = "", "", "", ""
    c_is_reply, c_is_rt = False, False
    c_time = "2026-03-01 12:00:00"
    
    if c_id and c_id in dyn_map:
        c_info = dyn_map[c_id]
        if isinstance(c_info, dict):
            c_trans = c_info.get("translated_text", "")
            c_raw = c_info.get("raw_text", "")
            c_handle = c_info.get("author_handle", "")
            c_disp = c_info.get("author_display_name", "")
            c_is_reply = c_info.get("is_reply", False)
            c_time = c_info.get("dt_str", "2026-03-01 12:00:00")
            if not c_trans and not c_raw: c_is_rt = True

    p_trans, p_raw, p_handle, p_disp = "", "", "", ""
    p_is_reply, p_mode = False, "repost"
    p_time = "2026-03-01 10:00:00"
    
    if p_id and p_id in dyn_map:
        p_info = dyn_map[p_id]
        if isinstance(p_info, dict):
            p_trans = p_info.get("translated_text", "")
            p_raw = p_info.get("raw_text", "")
            p_handle = p_info.get("author_handle", "")
            p_disp = p_info.get("author_display_name", "")
            p_is_reply = p_info.get("is_reply", False)
            p_mode = p_info.get("publish_mode", "repost")
            p_time = p_info.get("dt_str", "2026-03-01 10:00:00")

    return (c_trans, c_raw, c_handle, c_disp, c_is_reply, c_is_rt, c_time, 
            p_trans, p_raw, p_handle, p_disp, p_is_reply, p_mode, p_time)

# ==========================================
# 🚀 1:1 复制 main.py 的排版组装引擎
# ==========================================
def build_repost_context(p_id, p_handle, p_disp, p_is_reply, p_mode, p_dt, p_trans, p_raw, ret_level, is_video_mode):
    if not p_id: return ""
    if p_mode == "original": return "" 
        
    p_name = settings.targets.account_title_map.get(p_handle, p_disp) if p_handle else p_disp
    
    if is_video_mode:
        c_trans_p = p_trans.replace('\n', ' ')
        if len(c_trans_p) > 25: c_trans_p = c_trans_p[:25] + "..."
        if p_is_reply:
            return f"\n//@{BILI_ACCOUNT_NAME}: 💬{p_name}回复: {c_trans_p}"
        else:
            return f"\n//@{BILI_ACCOUNT_NAME}: {p_name}: {c_trans_p}"
    else:
        if p_is_reply:
            c_trans_p = p_trans.replace('\n', ' ')
            c_raw_p = p_raw.replace('\n', ' ')
            return f"\n//@{BILI_ACCOUNT_NAME}: 💬{p_name}回复说： {c_trans_p} 【原文】 {c_raw_p}"
        else:
            retention_str = ""
            if ret_level < 3:
                retention_str = f"\n\n{p_id}\n-由GloBot驱动"
            return f"\n//@{BILI_ACCOUNT_NAME}: {p_name}\n\n{p_dt}\n\n{p_trans}\n\n【原文】\n{p_raw}{retention_str}"

def build_safe_dynamic_text(c_name, c_time, c_trans, c_raw, c_id, c_is_reply, c_is_rt, ret_level, context_suffix, ref_link, limit):
    if c_is_rt:
        text = f"{c_name} 转发\n{c_time}"
        if context_suffix: text += context_suffix
        if ref_link: text += f"\n\n🔗 溯源: {ref_link}"
        if ret_level < 3: text += f"\n\n{c_id}\n-由GloBot驱动"
        return sanitize_for_bilibili(text[:limit])

    def assemble(include_tail, include_raw, truncate_trans_len=None):
        res = f"💬{c_name}回复说：\n" if c_is_reply else f"{c_time}\n\n"
            
        if truncate_trans_len is not None: res += c_trans[:truncate_trans_len] + "..."
        else: res += c_trans
            
        if include_raw:
            if c_raw: res += f"\n\n(原文: {c_raw})" if c_is_reply else f"\n\n【原文】\n{c_raw}"
        elif c_raw:
            res += "\n\n(原文过长已被截断)" if c_is_reply else "\n\n【原文】\n...(日文原文过长，已被自动截断)"
                
        if context_suffix: res += context_suffix
            
        if ref_link: res += f"\n\n(🔗 溯源: {ref_link})" if c_is_reply else f"\n\n🔗 溯源: {ref_link}"
                
        if include_tail and ret_level < 3:
            res += f"\n\n{c_id}"
            if not c_is_reply: res += "\n-由GloBot驱动"
                
        return sanitize_for_bilibili(res)

    t0 = assemble(True, True)
    if len(t0) <= limit: return t0, "✅ 形态0: 完美保留全部内容"
    
    t1 = assemble(False, True)
    if len(t1) <= limit: return t1, "🟡 形态1: 切除小尾巴"
    
    t2 = assemble(False, False)
    if len(t2) <= limit: return t2, "🟠 形态2: 彻底丢弃日文原文"
    
    fixed_len = len(assemble(False, False, truncate_trans_len=0))
    avail = limit - fixed_len - 5
    if avail > 0:
        return assemble(False, False, truncate_trans_len=avail), "🔴 形态3: 发生中文极限裁切"
    else:
        return t2[:limit-3] + "...", "💀 形态4: 彻底崩坏级裁切"

def simulate_assembly(c_id, c_trans, c_raw, c_handle, c_disp, c_is_reply, c_is_rt, c_time,
                      p_id, p_trans, p_raw, p_handle, p_disp, p_is_reply, p_mode, p_time,
                      ret_level_str, channel_mode):
    try: ret_level = int(ret_level_str)
    except: ret_level = 0
        
    c_name = settings.targets.account_title_map.get(c_handle, c_disp) if c_handle else c_disp
    
    is_video = channel_mode.startswith("视频")
    limit = 220 if is_video else 950
    
    ref_link_mock = "https://t.bilibili.com/12345678" if p_id else ""
    
    context_suffix = build_repost_context(p_id, p_handle, p_disp, p_is_reply, p_mode, p_time, p_trans, p_raw, ret_level, is_video)
    
    final_content, degrade_status = build_safe_dynamic_text(
        c_name, c_time, c_trans, c_raw, c_id, c_is_reply, c_is_rt, ret_level, context_suffix, ref_link_mock, limit
    )
    
    out_title = "" if c_is_reply or c_is_rt else c_name
    action = f"{degrade_status} (实际字数: {len(final_content)} / 上限: {limit})"

    return out_title, final_content, action

# ==========================================
# 🌐 Gradio 优雅网页 UI 
# ==========================================
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
                curr_is_reply = gr.Checkbox(label="是回复吗？")
                curr_is_rt = gr.Checkbox(label="是纯转推吗？")
            curr_time = gr.Textbox(label="时间戳")

        with gr.Column(scale=1):
            gr.Markdown("### 🔗 上一环干预区 (被拼接对象)")
            prev_trans = gr.Textbox(label="翻译文本", lines=3)
            prev_raw = gr.Textbox(label="日文原文", lines=3)
            with gr.Row():
                prev_handle = gr.Textbox(label="原生 Handle")
                prev_disp = gr.Textbox(label="原生显示昵称")
            with gr.Row():
                prev_is_reply = gr.Checkbox(label="是回复吗？")
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

    fetch_btn.click(
        fn=fetch_local_data,
        inputs=[curr_id_in, prev_id_in],
        outputs=[curr_trans, curr_raw, curr_handle, curr_disp, curr_is_reply, curr_is_rt, curr_time,
                 prev_trans, prev_raw, prev_handle, prev_disp, prev_is_reply, prev_mode, prev_time]
    )
    
    sim_btn.click(
        fn=simulate_assembly,
        inputs=[curr_id_in, curr_trans, curr_raw, curr_handle, curr_disp, curr_is_reply, curr_is_rt, curr_time,
                prev_id_in, prev_trans, prev_raw, prev_handle, prev_disp, prev_is_reply, prev_mode, prev_time,
                retention_level, channel_mode],
        outputs=[out_title, out_content, out_action]
    )

if __name__ == "__main__":
    print(f"🌐 正在启动排版沙盒环境，已加载账号: @{BILI_ACCOUNT_NAME} ...")
    demo.launch(inbrowser=True, theme=gr.themes.Soft())