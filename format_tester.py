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

# 读取全局被隔离的个人隐私账号名
BILI_ACCOUNT_NAME = os.getenv("BILI_ACCOUNT_NAME", "GloBot搬运")
DATA_DIR = Path(os.getenv("LOCAL_DATA_DIR", f"./GloBot_Data/{settings.targets.group_name}"))
DYN_MAP_FILE = DATA_DIR / "dyn_map.json"

def load_dyn_map():
    if not DYN_MAP_FILE.exists():
        return {}
    try:
        with open(DYN_MAP_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

# ==========================================
# 🔍 数据提取引擎 (零网络请求，纯查本地字典)
# ==========================================
def fetch_local_data(c_id, p_id):
    dyn_map = load_dyn_map()
    
    # 当前节点初始化
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
            # 纯转推的缓存特征：没有文本且没有被标记为原创（如果需要可手动在 UI 模拟）
            if not c_trans and not c_raw:
                c_is_rt = True

    # 上一环节点初始化
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
# 🚀 排版组装沙盒引擎 (1:1 完美复刻 main.py 的逻辑)
# ==========================================
def simulate_assembly(c_id, c_trans, c_raw, c_handle, c_disp, c_is_reply, c_is_rt, c_time,
                      p_id, p_trans, p_raw, p_handle, p_disp, p_is_reply, p_mode, p_time,
                      ret_level_str):
    try:
        ret_level = int(ret_level_str)
    except:
        ret_level = 0
        
    c_name = settings.targets.account_title_map.get(c_handle, c_disp) if c_handle else c_disp
    
    out_title = ""
    final_content = ""
    action = "智能判断中..."
    
    # ---------------- 1. 组装当前节点 ----------------
    if c_is_rt:
        out_title = ""
        final_content = f"{c_name} 转发\n{c_time}"
        if ret_level < 3:
            final_content += f"\n{c_id}\n-由GloBot驱动"
        final_content = sanitize_for_bilibili(final_content)
        action = "🔄 原生纯转推 (旁路模式，调 smart_repost)"
    else:
        if c_is_reply:
            out_title = ""
            final_content = f"💬{c_name}回复说：\n{c_trans}\n\n(原文: {c_raw})"
            if ret_level == 0:
                final_content += f"\n\n{c_id}"
        else:
            out_title = c_name  # 独立推文才给 Title 赋值
            final_content = f"{c_time}\n\n{c_trans}\n\n【原文】\n{c_raw}"
            if ret_level < 3:
                final_content += f"\n\n{c_id}\n-由GloBot驱动"
                
        final_content = sanitize_for_bilibili(final_content)
        action = "🎬 图文首发 (有媒体触发) / 🔄 原生转发 (无媒体触发)"

    # ---------------- 2. 组装上一环 (防吞卡片对策) ----------------
    context_suffix = ""
    if p_id:
        if p_mode == "original":
            # 🚨 智能刹车：原创卡片不吞噬，直接依靠 B 站原生排版，拒绝画蛇添足！
            context_suffix = "\n\n(⚠️ 智能刹车：检测到上一环是 original 原创卡片，已把显示权交还给B站，不进行多余拼接)"
        else:
            p_name = settings.targets.account_title_map.get(p_handle, p_disp) if p_handle else p_disp
            
            if p_is_reply:
                c_trans_p = p_trans.replace('\n', ' ')
                c_raw_p = p_raw.replace('\n', ' ')
                context_suffix = f"\n//@{BILI_ACCOUNT_NAME}: 💬{p_name}回复说： {c_trans_p} 【原文】 {c_raw_p}"
            else:
                retention_str = ""
                if ret_level < 3:
                    retention_str = f"\n\n{p_id}\n-由GloBot驱动"
                context_suffix = f"\n//@{BILI_ACCOUNT_NAME}: {p_name}\n\n{p_time}\n\n{p_trans}\n\n【原文】\n{p_raw}{retention_str}"
                
    final_content += context_suffix

    return out_title, final_content, action

# ==========================================
# 🌐 Gradio 优雅网页 UI (沙盒控制台)
# ==========================================
# 🚨 修复点 1：去除了 gr.Blocks() 中的 theme 参数
with gr.Blocks(title="GloBot 排版沙盒") as demo:
    gr.Markdown(f"## 🛠️ GloBot 动态排版拼装沙盒 (当前挂载号: `@{BILI_ACCOUNT_NAME}`)")
    
    with gr.Row():
        curr_id_in = gr.Textbox(label="1. 当前推文 ID", placeholder="输入你想要测试的推文ID...")
        prev_id_in = gr.Textbox(label="2. 上一环推文 ID (选填)", placeholder="填入以测试防套娃拼接...")
        fetch_btn = gr.Button("🔍 从本地缓存提取物理数据", variant="primary")
        
    gr.Markdown("---")
    
    with gr.Row():
        # 【左侧】：当前节点干预区
        with gr.Column(scale=1):
            gr.Markdown("### 📝 当前推文干预区")
            curr_trans = gr.Textbox(label="翻译文本", lines=3)
            curr_raw = gr.Textbox(label="日文原文", lines=3)
            with gr.Row():
                curr_handle = gr.Textbox(label="原生 Handle (自动查户口本)")
                curr_disp = gr.Textbox(label="原生显示昵称 (兜底用)")
            with gr.Row():
                curr_is_reply = gr.Checkbox(label="是回复吗？(is_reply)")
                curr_is_rt = gr.Checkbox(label="是纯转推吗？(is_pure_rt)")
            curr_time = gr.Textbox(label="时间戳")

        # 【右侧】：上一环节点干预区
        with gr.Column(scale=1):
            gr.Markdown("### 🔗 上一环干预区 (被拼接对象)")
            prev_trans = gr.Textbox(label="翻译文本", lines=3)
            prev_raw = gr.Textbox(label="日文原文", lines=3)
            with gr.Row():
                prev_handle = gr.Textbox(label="原生 Handle")
                prev_disp = gr.Textbox(label="原生显示昵称")
            with gr.Row():
                prev_is_reply = gr.Checkbox(label="是回复吗？")
                prev_mode = gr.Dropdown(label="🚨 发包模式 (publish_mode)", choices=["original", "repost"], value="repost", info="original 会触发智能刹车")
            prev_time = gr.Textbox(label="时间戳")
            
    gr.Markdown("---")
    
    with gr.Row():
        retention_level = gr.Dropdown(label="全局隐藏等级 (tweet_id_retention)", choices=["0", "1", "2", "3"], value="0", info="0: 全留, 3: 全隐")
        sim_btn = gr.Button("🚀 执行沙盒拼装渲染", variant="primary", size="lg")
        
    gr.Markdown("---")
    gr.Markdown("### 📊 B站最终呈现预览")
    
    with gr.Row():
        with gr.Column(scale=1):
            out_action = gr.Textbox(label="发包动作预测 (引擎流向)")
            out_title = gr.Textbox(label="B站原生标题 (Title 字段)", info="独立图文专属。如果是空说明不支持原生标题")
        with gr.Column(scale=2):
            # 🚨 修复点 2：移除了 Gradio 6.0 废弃的 show_copy_button
            out_content = gr.Textbox(label="最终发向 B 站的正文 (Content 字段)", lines=12)

    # ==========================================
    # 绑定事件
    # ==========================================
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
                retention_level],
        outputs=[out_title, out_content, out_action]
    )

if __name__ == "__main__":
    print(f"🌐 正在启动排版沙盒环境，已加载账号: @{BILI_ACCOUNT_NAME} ...")
    # 🚨 修复点 3：将 theme 参数转移至 launch() 中
    demo.launch(inbrowser=True, theme=gr.themes.Soft())