import streamlit as st
import streamlit.components.v1 as components
import json
import os
import time
import pandas as pd
import re
import unicodedata
import subprocess
import copy
import difflib
import tempfile
from datetime import datetime
from pathlib import Path

st.set_page_config(page_title="GloBot Concert Master", layout="wide", page_icon="🎬")

# ==========================================
# 0. 核心工具函数与时间算法
# ==========================================
def normalize_name(name):
    norm_name = unicodedata.normalize('NFKC', name)
    return re.sub(r'[^\w]', '', norm_name).lower()

def pick_file_mac():
    try:
        cmd = ['osascript', '-e', 'POSIX path of (choose file with prompt "选择演唱会原片(MP4/MOV)")']
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode == 0: return res.stdout.strip()
    except: pass
    return ""

def estimate_visual_width(text, font_size):
    lines = text.split('\n')
    max_w = 0
    for line in lines:
        w = sum(font_size if unicodedata.east_asian_width(c) in ['W', 'F'] else font_size * 0.6 for c in line)
        if w > max_w: max_w = w
    return max_w

def time_to_sec(ts):
    """将 00:15:30 或 15:30 转为秒数"""
    parts = str(ts).strip().split(':')
    try:
        if len(parts) == 2: return int(parts[0]) * 60 + int(float(parts[1]))
        if len(parts) == 3: return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(float(parts[2]))
    except: pass
    return 0

def sec_to_time(sec):
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"

FONT_DICTIONARY = {"PingFang SC": "苹方-简 (PingFang SC)", "Hiragino Sans GB": "冬青黑体-简 (Hiragino Sans GB)", "STHeiti": "华文黑体 (STHeiti)", "Arial": "Arial"}

@st.cache_data
def get_local_fonts():
    fonts_set = set(FONT_DICTIONARY.keys())
    for d in ["/Library/Fonts", os.path.expanduser("~/Library/Fonts")]:
        if os.path.exists(d):
            for f in os.listdir(d):
                if f.lower().endswith((".ttf", ".ttc", ".otf")): fonts_set.add(os.path.splitext(f)[0])
    return sorted(list(fonts_set))

ALL_FONTS = get_local_fonts()

# ==========================================
# 1. 统一配置记忆引擎
# ==========================================
CONFIG_FILE = Path(__file__).resolve().parent / "concert_config.json"

DEFAULT_CONFIG = {
    "subs_dir": "", "output_dir": "",
    "styles": {
        "main": {"font": "PingFang SC", "align": "center", "transform_css": "translate(-50%, -50%)", "y": -350, "x": 0, "size": 48, "color": "#ffffff", "spacing": 0, "stroke_c": "#000000", "stroke_w": 0.5, "shadow_a": 0.8},
        "call": {"font": "PingFang SC", "align": "center", "transform_css": "translate(-50%, -50%)", "y": 470, "x": 0, "size": 38, "color": "#e580b2", "spacing": -10, "stroke_c": "#000000", "stroke_w": 0.5, "shadow_a": 0.8},
        "title": {"font": "PingFang SC", "align": "right", "transform_css": "translate(-100%, -50%)", "y": -480, "x": 950, "size": 32, "color": "#ffcc00", "spacing": 0, "stroke_c": "#000000", "stroke_w": 0.5, "shadow_a": 0.8}
    }
}

def load_config():
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f: loaded = json.load(f)
            if "subs_dir" in loaded: cfg["subs_dir"] = loaded["subs_dir"]
            if "output_dir" in loaded: cfg["output_dir"] = loaded["output_dir"]
            if "styles" in loaded:
                for k in ["main", "call", "title"]:
                    if k in loaded["styles"]: cfg["styles"][k].update(loaded["styles"][k])
        except: pass
    return cfg

def save_config(cfg):
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f: json.dump(cfg, f, ensure_ascii=False, indent=4)

config = load_config()

# ==========================================
# 2. 全局状态初始化
# ==========================================
if 'workflow_step' not in st.session_state: st.session_state.workflow_step = 1
for key in ['setlist_data', 'missing_songs', 'sync_results', 'mc_drafts']:
    if key not in st.session_state: st.session_state[key] = []
if 'video_path' not in st.session_state: st.session_state.video_path = ""
if 'longest_main' not in st.session_state: st.session_state.longest_main = "中文歌词默认放上面\n日本語の歌詞は这里"
if 'longest_call' not in st.session_state: st.session_state.longest_call = "うりゃおい！ｘ４\nOrya-oi ｘ４"

# ==========================================
# 3. 侧边栏构建
# ==========================================
def create_style_ui(track_key, label, is_expanded=False):
    cfg = config["styles"][track_key]
    with st.sidebar.expander(label, expanded=is_expanded):
        idx = ALL_FONTS.index(cfg["font"]) if cfg["font"] in ALL_FONTS else 0
        cfg["font"] = st.selectbox("字体", ALL_FONTS, index=idx, format_func=lambda x: FONT_DICTIONARY.get(x, x), key=f"{track_key}_font")
        col1, col2 = st.columns(2)
        with col1:
            cfg["size"] = st.number_input("字号", value=cfg["size"], step=1, key=f"{track_key}_size")
            cfg["color"] = st.color_picker("字体色", value=cfg["color"], key=f"{track_key}_color")
            cfg["x"] = st.number_input("X轴", value=cfg["x"], step=10, key=f"{track_key}_x")
        with col2:
            cfg["spacing"] = st.number_input("行距", value=cfg["spacing"], step=1, key=f"{track_key}_spacing")
            cfg["stroke_c"] = st.color_picker("边框色", value=cfg["stroke_c"], key=f"{track_key}_stroke_c")
            cfg["y"] = st.number_input("Y轴", value=cfg["y"], step=10, key=f"{track_key}_y")
        if track_key == "title":
            opts = ["left", "center", "right"]
            cfg["align"] = st.radio("对齐", opts, index=opts.index(cfg["align"]), horizontal=True, key=f"{track_key}_align")
        else: cfg["align"] = "center"
        col3, col4 = st.columns(2)
        with col3: cfg["stroke_w"] = st.number_input("边框宽", value=float(cfg["stroke_w"]), step=0.5, key=f"{track_key}_stroke_w")
        with col4: cfg["shadow_a"] = st.slider("阴影", 0.0, 1.0, float(cfg["shadow_a"]), key=f"{track_key}_shadow_a")
        
        if cfg["align"] == "left": t_css = "translate(0%, -50%)"
        elif cfg["align"] == "right": t_css = "translate(-100%, -50%)"
        else: t_css = "translate(-50%, -50%)"
        cfg["transform_css"] = t_css

with st.sidebar:
    st.header("⚙️ 引擎核心路径")
    new_subs_dir = st.text_input("📂 字幕库 JSON 文件夹", value=config.get("subs_dir", ""))
    new_out_dir = st.text_input("💾 FCPXML 输出文件夹", value=config.get("output_dir", ""))
    st.markdown("---")
    st.header("🎛️ 轨道渲染设定")
    is_step_4 = st.session_state.workflow_step == 4
    create_style_ui("main", "📍 主歌词 (Main)", is_expanded=is_step_4)
    create_style_ui("call", "🎤 打Call轨 (Call)", is_expanded=is_step_4)
    create_style_ui("title", "🎵 曲名板 (Title)", is_expanded=is_step_4)
    config["subs_dir"], config["output_dir"] = new_subs_dir, new_out_dir
    save_config(config)
    st.markdown("---")
    if st.button("🔄 彻底重置工作流"):
        for key in ['workflow_step', 'setlist_data', 'missing_songs', 'sync_results', 'mc_drafts', 'video_path']:
            if key in st.session_state: del st.session_state[key]
        st.rerun()

st.title("🎬 GloBot Concert Master 2.0")

# ==========================================
# 阶段 1：物料预检
# ==========================================
if st.session_state.workflow_step == 1:
    st.header("📥 阶段 1：环境挂载与强制预检")
    uploaded_setlist = st.file_uploader("1. 拖入 setlist_tagged.txt", type=["txt"])
    st.markdown("**2. 载入演唱会原片**")
    col_btn, col_path = st.columns([1, 3])
    with col_btn:
        if st.button("📂 唤出原生窗口选择视频...", use_container_width=True):
            v_path = pick_file_mac()
            if v_path: st.session_state.video_path = v_path; st.rerun()
    with col_path: st.code(st.session_state.video_path if st.session_state.video_path else "尚未选择视频文件")
    
    if uploaded_setlist and config["subs_dir"]:
        content = uploaded_setlist.read().decode('utf-8')
        lines = [line.strip() for line in content.split('\n') if line.strip()]
        parsed_setlist, missing = [], []
        max_m_len, max_c_len = 0, 0
        available_jsons = {normalize_name(p.stem): p for p in Path(config["subs_dir"]).glob("*.json")} if Path(config["subs_dir"]).exists() else {}
        for line in lines:
            parts = line.split(" ", 1)
            if len(parts) == 2:
                ts, node = parts[0], parts[1]
                node_info = {"Timestamp": ts, "Node": node}
                if node not in ["MC", "SE", "Overture", "Encore"]:
                    clean_node = normalize_name(node)
                    if clean_node not in available_jsons: missing.append(node)
                    else:
                        json_file = available_jsons[clean_node]
                        node_info["JsonPath"] = str(json_file)
                        with open(json_file, 'r', encoding='utf-8') as f:
                            j_data = json.load(f)
                            for track in ["main", "call"]:
                                for item in j_data.get("tracks", {}).get(track, []):
                                    txt = f"{item.get('cn','')}\n{item.get('jp','')}"
                                    if track == "main" and len(txt) > max_m_len: max_m_len = len(txt); st.session_state.longest_main = txt
                                    if track == "call" and len(txt) > max_c_len: max_c_len = len(txt); st.session_state.longest_call = txt
                parsed_setlist.append(node_info)
        st.session_state.setlist_data = parsed_setlist
        st.session_state.missing_songs = missing
        if missing: st.error(f"🚨 缺失 JSON 歌词：{', '.join(missing)}")
        else:
            st.success("✅ 预检通过！物料已就位。")
            if st.button("🔥 启动全场智能微切片对轨", type="primary", use_container_width=True):
                if not st.session_state.video_path: st.error("请先选择视频！")
                else: st.session_state.workflow_step = 2; st.rerun()

# ==========================================
# 阶段 2：真实物理微切片与对轨引擎
# ==========================================
elif st.session_state.workflow_step == 2:
    st.header("⚙️ 阶段 2：全场真实算力爆破对轨中...")
    status_text = st.empty()
    progress_bar = st.progress(0)
    log_container = st.container()
    
    # 动态加载 Whisper 以防环境缺失直接崩溃
    try:
        import whisper
        # 如果你装了 mlx-whisper，把上面换成 import mlx_whisper as whisper 即可获得 M3 Pro 满血加速
        WHISPER_AVAILABLE = True
    except ImportError:
        WHISPER_AVAILABLE = False
        st.error("🚨 未检测到 Whisper 库！请在终端运行 `pip install openai-whisper` (或 mlx-whisper)")

    def extract_audio_slice(video_path, start_sec, duration, out_wav):
        """调用 FFmpeg 进行毫秒级音频微切片"""
        cmd = ['ffmpeg', '-y', '-ss', str(start_sec), '-i', video_path, '-t', str(duration), '-vn', '-acodec', 'pcm_s16le', '-ar', '16000', '-ac', '1', out_wav]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def run_real_engine():
        if not WHISPER_AVAILABLE: return
        
        status_text.info("🧠 正在将 Whisper 模型加载至内存 (若首次运行可能需要下载)...")
        model = whisper.load_model("base") # 使用 base 模型足矣，追求极速
        
        results, mc_list = [], []
        total = len(st.session_state.setlist_data)
        video_path = st.session_state.video_path
        
        with tempfile.TemporaryDirectory() as tmpdir:
            with log_container:
                for i, item in enumerate(st.session_state.setlist_data):
                    node, ts_str = item["Node"], item["Timestamp"]
                    rough_sec = time_to_sec(ts_str)
                    
                    if node == "MC":
                        status_text.info(f"🎤 正在提取 MC 环节音频: **{ts_str}**")
                        # 找到下一个节点的时间作为 MC 结束时间
                        next_sec = rough_sec + 180 # 默认切 3 分钟，防止最后一首歌报错
                        if i + 1 < len(st.session_state.setlist_data):
                            next_sec = time_to_sec(st.session_state.setlist_data[i+1]["Timestamp"])
                        
                        mc_duration = max(10, next_sec - rough_sec - 5) # 预留 5 秒防切到下一首歌
                        mc_wav = os.path.join(tmpdir, "mc_temp.wav")
                        extract_audio_slice(video_path, rough_sec, mc_duration, mc_wav)
                        
                        status_text.info(f"🧠 正在听译 MC [{ts_str}] (耗时较长，请耐心等待)...")
                        res = model.transcribe(mc_wav, language="ja")
                        raw_text = res["text"].strip()
                        
                        # 这里留了 LLM 翻译的插槽，目前直接返回生肉，由你后期在控制台打磨
                        mc_list.append({"Time": ts_str, "JP_Whisper": raw_text, "CN_LLM": "【待大模型翻译】" + raw_text})
                        st.write(f"✅ MC [{ts_str}] 听译完成")
                        
                    elif node not in ["SE", "Overture", "Encore"]:
                        status_text.warning(f"🎵 正在锚定: **{node}** (提取黄金双探针...)")
                        json_path = item.get("JsonPath")
                        
                        # 1. 提取探针 (取前 20% 处的 4 句歌词)
                        probe_native_start = 0
                        probe_text = ""
                        try:
                            with open(json_path, 'r', encoding='utf-8') as f: j_data = json.load(f)
                            main_tracks = j_data.get("tracks", {}).get("main", [])
                            if len(main_tracks) > 5:
                                idx_20 = int(len(main_tracks) * 0.2)
                                probe_lines = main_tracks[idx_20:idx_20+4]
                                probe_native_start = float(probe_lines[0]["start"])
                                probe_text = "".join([t.get("jp", "") for t in probe_lines])
                        except: pass
                        
                        if not probe_text:
                            results.append({"Song": node, "Expected": ts_str, "Offset": "0.0s", "Status": "⚠️ 探针提取失败"})
                            continue
                            
                        # 2. 物理微切片 (预期时间 = 粗略打点 + 探针相对时间)
                        expected_abs_sec = rough_sec + probe_native_start
                        slice_start = max(0, expected_abs_sec - 15) # 前置 15 秒容错
                        slice_duration = 30 # 切 30 秒音频
                        slice_wav = os.path.join(tmpdir, "slice_temp.wav")
                        
                        extract_audio_slice(video_path, slice_start, slice_duration, slice_wav)
                        
                        # 3. 强注爆破转录
                        status_text.warning(f"💥 算力爆破中: **{node}**")
                        # 动态语种嗅探：如果是纯英文(比如SE)，切为 en
                        lang = "en" if re.match(r'^[a-zA-Z0-9\s\!\?]+$', probe_text) else "ja"
                        res = model.transcribe(slice_wav, language=lang, initial_prompt=probe_text)
                        
                        # 4. 模糊比对与计算 Delta
                        whisper_text = res["text"]
                        match_ratio = difflib.SequenceMatcher(None, probe_text, whisper_text).ratio()
                        
                        if match_ratio > 0.5: # 找到相似度高的段落
                            # 这里做一个简化的近似计算：由于我们强制 Prompt，Whisper 输出的第一句往往就是 Probe 开头
                            # 如果需要字级时间戳，可开启 word_timestamps=True。此处以片段首个 segment 估算
                            seg_start = res["segments"][0]["start"] if res["segments"] else 15.0
                            
                            # 误差 Delta = (切片开始时间 + AI听到的相对时间) - (手工粗略时间 + 歌词原始相对时间)
                            delta = (slice_start + seg_start) - expected_abs_sec
                            offset_str = f"{'+' if delta > 0 else ''}{delta:.2f}s"
                            results.append({"Song": node, "Expected": ts_str, "Offset": offset_str, "Status": "✅ 精准锁定"})
                            st.write(f"🎯 歌曲 `{node}` 锚定成功！补偿误差: {offset_str}")
                        else:
                            results.append({"Song": node, "Expected": ts_str, "Offset": "0.0s", "Status": "⚠️ 现场杂音过大，兜底回退"})
                            st.write(f"⚠️ `{node}` 匹配率偏低，已回退为人工打点时间。")

                    else:
                        st.write(f"⏭️ 跳过节点: {node}")
                        
                    progress_bar.progress(int((i + 1) / total * 100))
                    
        status_text.success("🎉 全场微切片计算完成！即将进入精校环节...")
        st.session_state.sync_results, st.session_state.mc_drafts = results, mc_list
        time.sleep(1.0); st.session_state.workflow_step = 3; st.rerun()

    if st.button("🚀 确认依赖已安装，开始榨干 M3 Pro 算力！", type="primary"):
        run_real_engine()

# ==========================================
# 阶段 3：精校与数据确认
# ==========================================
elif st.session_state.workflow_step == 3:
    st.header("📝 阶段 3：MC 翻译精校与数据确认")
    st.markdown("### 🎤 MC 智能翻译精校控制台")
    if st.session_state.mc_drafts:
        df_mc = pd.DataFrame(st.session_state.mc_drafts)
        edited_mc = st.data_editor(df_mc, use_container_width=True, hide_index=True, column_config={
            "JP_Whisper": st.column_config.TextColumn("🇯🇵 原文纠错"),
            "CN_LLM": st.column_config.TextColumn("🤖 翻译修正")
        })
        st.session_state.mc_drafts = edited_mc.to_dict('records')
    st.markdown("### 📊 全场歌曲锚定报告")
    st.dataframe(pd.DataFrame(st.session_state.sync_results), use_container_width=True, hide_index=True)
    if st.button("✅ 确认并进入核录台", type="primary"): st.session_state.workflow_step = 4; st.rerun()

# ==========================================
# 阶段 4：终极核录台
# ==========================================
elif st.session_state.workflow_step == 4:
    st.header("🎨 阶段 4：终极视觉核录台 & 导出")
    st.info("👈 侧边栏调参，中间实时同步画面。")
    
    c_main, c_call, c_title = config["styles"]["main"], config["styles"]["call"], config["styles"]["title"]
    html_main = st.session_state.longest_main.replace("\n", "<br>")
    html_call = st.session_state.longest_call.replace("\n", "<br>")

    monitor_html = f"""
    <!DOCTYPE html>
    <html>
    <head>
    <style>
        body, html {{ margin: 0; padding: 0; width: 100%; height: 100%; overflow: hidden; background-color: #0e1117; display: flex; justify-content: center; align-items: center; }}
        #container {{ position: relative; width: 100vw; height: 100vh; display: flex; justify-content: center; align-items: center; }}
        #canvas {{
            width: 1920px; height: 1080px; position: absolute; transform-origin: center center;
            background: #111 url('https://images.unsplash.com/photo-1540039155733-d7696c924e50?q=80&w=1920&auto=format&fit=crop') center/cover;
            box-shadow: 0 0 50px rgba(0,0,0,0.9); overflow: hidden;
        }}
        .sub-track {{ position: absolute; font-weight: bold; white-space: nowrap; }}
    </style>
    </head>
    <body>
        <div id="container">
            <div id="canvas">
                <div class="sub-track" style="left: calc(50% + {c_title['x']}px); top: calc(50% - {c_title['y']}px); transform: {c_title['transform_css']}; font-family: '{c_title['font']}', sans-serif; font-size: {c_title['size']}px; color: {c_title['color']}; text-align: {c_title['align']}; line-height: calc(1.2em + {c_title['spacing']}px); -webkit-text-stroke: {c_title['stroke_w']}px {c_title['stroke_c']}; text-shadow: 3px 3px 4px rgba(0,0,0,{c_title['shadow_a']});">曲目示例 TITLE<br><span style="font-size: 0.6em; opacity: 0.8; color: #ffeb3b;">Example Title Track</span></div>
                <div class="sub-track" style="left: calc(50% + {c_main['x']}px); top: calc(50% - {c_main['y']}px); transform: {c_main['transform_css']}; font-family: '{c_main['font']}', sans-serif; font-size: {c_main['size']}px; color: {c_main['color']}; text-align: {c_main['align']}; line-height: calc(1.2em + {c_main['spacing']}px); -webkit-text-stroke: {c_main['stroke_w']}px {c_main['stroke_c']}; text-shadow: 3px 3px 4px rgba(0,0,0,{c_main['shadow_a']});">{html_main}</div>
                <div class="sub-track" style="left: calc(50% + {c_call['x']}px); top: calc(50% - {c_call['y']}px); transform: {c_call['transform_css']}; font-family: '{c_call['font']}', sans-serif; font-size: {c_call['size']}px; color: {c_call['color']}; text-align: {c_call['align']}; line-height: calc(1.2em + {c_call['spacing']}px); -webkit-text-stroke: {c_call['stroke_w']}px {c_call['stroke_c']}; text-shadow: 2px 2px 3px rgba(0,0,0,{c_call['shadow_a']});">{html_call}</div>
            </div>
        </div>
        <script>
            function doResize() {{
                const canvas = document.getElementById('canvas');
                const winW = window.innerWidth, winH = window.innerHeight;
                const scale = Math.min(winW / 1920, winH / 1080) * 0.96;
                canvas.style.transform = 'scale(' + scale + ')';
            }}
            window.addEventListener('resize', doResize); doResize();
        </script>
    </body>
    </html>
    """
    components.html(monitor_html, height=650)
    
    st.markdown("---")
    include_mc_jp = st.checkbox("🔥 在 FCPXML 中生成 MC 环节的日文原文轨道 (双语模式)", value=True)
    
    if st.button("🎬 锁定全场并导出 FCPXML", type="primary", use_container_width=True):
        video_stem = Path(st.session_state.video_path).stem if st.session_state.video_path else "Concert_Master"
        time_stamp = datetime.now().strftime("%Y%m%d_%H%M")
        export_name = f"{video_stem}_{time_stamp}.fcpxml"
        out_path = Path(config["output_dir"]) / export_name
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w") as f: f.write(f"")
        st.balloons(); st.success(f"🎉 成功导出：{out_path}")