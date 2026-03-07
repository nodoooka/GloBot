import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
import json
import re
import os
import xml.etree.ElementTree as ET
import unicodedata

# ==========================================
# 核心路径解析引擎
# ==========================================
def get_dict_path():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_dir, "knowledge_base", "songs.json")

# ==========================================
# 辅助函数：颜色、时间与溢出计算
# ==========================================
def hex_to_fcp(hex_color, alpha=1.0):
    hex_color = hex_color.lstrip('#')
    r = int(hex_color[0:2], 16) / 255.0
    g = int(hex_color[2:4], 16) / 255.0
    b = int(hex_color[4:6], 16) / 255.0
    return f"{r:.3f} {g:.3f} {b:.3f} {alpha}"

def parse_frame_duration(fd_str):
    match = re.match(r"(\d+)/(\d+)s", fd_str)
    if match: return int(match.group(1)), int(match.group(2))
    return 1001, 30000

def sec_to_frames(seconds, num, den):
    fps = den / num
    return round(seconds * fps)

def format_time_by_frames(frame_count, num, den):
    if frame_count == 0: return "0s"
    return f"{frame_count * num}/{den}s"

def estimate_visual_width(text, font_size):
    """根据东亚全角/半角特性，估算字符串在特定字号下的像素物理宽度"""
    lines = text.split('\n')
    max_width = 0
    for line in lines:
        width = 0
        for char in line:
            # W (Wide) 和 F (Fullwidth) 算 1em，其他算 0.6em
            if unicodedata.east_asian_width(char) in ['W', 'F']:
                width += font_size
            else:
                width += font_size * 0.6
        if width > max_width:
            max_width = width
    return max_width

def get_longest_text(tracks_list, is_main=True):
    """找出轨道中视觉上最长的一句歌词进行极端压力测试"""
    max_len = 0
    longest_str = ""
    for item in tracks_list:
        if is_main:
            combined = "\n".join(filter(None, [item.get('cn'), item.get('jp')]))
        else:
            combined = "\n".join(filter(None, [item.get('jp'), item.get('cn')]))
            
        vis_len = estimate_visual_width(combined, 10) # 统一用字号10作为衡量基准
        if vis_len > max_len:
            max_len = vis_len
            longest_str = combined
    return longest_str

# ==========================================
# 知识库引擎：读取歌名翻译字典
# ==========================================
def get_cn_translation(jp_title):
    dict_path = get_dict_path()
    if os.path.exists(dict_path):
        try:
            with open(dict_path, 'r', encoding='utf-8') as f:
                translations = json.load(f)
            norm_jp = unicodedata.normalize('NFC', jp_title)
            norm_dict = {unicodedata.normalize('NFC', k): v for k, v in translations.items()}
            return norm_dict.get(norm_jp, "")
        except Exception:
            pass
    return ""

def get_display_title(jp_title):
    cn_title = get_cn_translation(jp_title)
    if cn_title and cn_title != jp_title:
        return f"{cn_title}\n{jp_title}"
    return jp_title


# ==========================================
# FCPXML 生成核心引擎
# ==========================================
def append_title_components(clip_node, text_content, style_cfg, ts_id):
    text_node = ET.SubElement(clip_node, "text")
    style_node = ET.SubElement(text_node, "text-style", {"ref": ts_id})
    style_node.text = text_content.replace('\n', '\r')

    def_node = ET.SubElement(clip_node, "text-style-def", {"id": ts_id})
    style_attrs = {
        "font": str(style_cfg.get("font", "PingFang SC")),
        "fontSize": str(style_cfg.get("fontSize", "48")),
        "fontColor": str(style_cfg.get("fontColor", "1 1 1 1")),
        "alignment": str(style_cfg.get("alignment", "center"))
    }
    
    optional_attrs = ["lineSpacing", "strokeColor", "strokeWidth", "shadowColor", "shadowOffset", "shadowBlurRadius"]
    for attr in optional_attrs:
        if attr in style_cfg and str(style_cfg[attr]) != "":
            style_attrs[attr] = str(style_cfg[attr])

    ET.SubElement(def_node, "text-style", style_attrs)

    offset_x = style_cfg.get("offset_x", "0")
    offset_y = style_cfg.get("offset_y", "0")
    if str(offset_x) != "0" or str(offset_y) != "0":
        ET.SubElement(clip_node, "adjust-transform", {"position": f"{offset_x} {offset_y}"})

def generate_fcpxml_string(json_data, config):
    metadata = json_data.get("metadata", {})
    song_title = unicodedata.normalize('NFC', metadata.get("title", "Unknown_Song"))
    tracks = json_data.get("tracks", {})
    
    num, den = parse_frame_duration(config["project_settings"]["frame_duration"])
    fcpxml = ET.Element("fcpxml", version="1.10")
    
    resources = ET.SubElement(fcpxml, "resources")
    fmt = config["project_settings"]
    ET.SubElement(resources, "format", {"id": "r1", "name": fmt["framerate_format"], "frameDuration": fmt["frame_duration"], "width": fmt["resolution"]["width"], "height": fmt["resolution"]["height"]})
    ET.SubElement(resources, "effect", {"id": "r2", "name": "基本字幕", "uid": ".../Titles.localized/Bumper:Opener.localized/Basic Title.localized/Basic Title.moti"})

    max_end_time = 0.0
    for track_name in ["main", "call"]:
        for item in tracks.get(track_name, []):
            if item["end"] > max_end_time: max_end_time = item["end"]
                
    master_frames = sec_to_frames(max_end_time + 10.0, num, den)
    master_duration_str = format_time_by_frames(master_frames, num, den)

    compound_media = ET.SubElement(resources, "media", {"id": "r3", "name": f"{song_title} (复合字幕)"})
    sequence = ET.SubElement(compound_media, "sequence", {"format": "r1", "tcStart": "0s"})
    spine = ET.SubElement(sequence, "spine")
    gap = ET.SubElement(spine, "gap", {"name": "Master Gap", "offset": "0s", "duration": master_duration_str})

    clips_buffer = []

    title_style = config["styles"]["song_title"]
    title_frames = sec_to_frames(max_end_time if max_end_time > 0 else 10.0, num, den)
    display_title_text = get_display_title(song_title)
    
    clips_buffer.append({"start_frame": 0, "end_frame": title_frames, "lane": title_style["lane"], "name": song_title, "text": display_title_text, "style_cfg": title_style})

    for track_name, track_key in [("main", "main_track"), ("call", "call_track")]:
        style_cfg = config["styles"][track_key]
        track_clips = []
        for item in tracks.get(track_name, []):
            jp_text = str(item.get("jp", "")).strip()
            cn_text = str(item.get("cn", "")).strip()
            if not jp_text and not cn_text: continue
            
            combined_text = "\n".join(filter(None, [cn_text, jp_text])) if track_name == "main" else "\n".join(filter(None, [jp_text, cn_text]))
                
            start_frame = sec_to_frames(float(item["start"]), num, den)
            end_frame = sec_to_frames(float(item["end"]), num, den)
            if end_frame <= start_frame: end_frame = start_frame + 1

            track_clips.append({"start_frame": start_frame, "end_frame": end_frame, "lane": style_cfg["lane"], "name": jp_text[:15] or cn_text[:15], "text": combined_text, "style_cfg": style_cfg})

        track_clips.sort(key=lambda x: x["start_frame"])
        for i in range(len(track_clips) - 1):
            if track_clips[i]["end_frame"] > track_clips[i+1]["start_frame"]:
                track_clips[i]["end_frame"] = track_clips[i+1]["start_frame"]

        clips_buffer.extend(track_clips)

    clips_buffer.sort(key=lambda x: x["start_frame"])
    ts_counter = 1
    
    for clip_data in clips_buffer:
        duration_frames = clip_data["end_frame"] - clip_data["start_frame"]
        if duration_frames <= 0: continue

        clip = ET.SubElement(gap, "title", {"name": clip_data["name"], "lane": clip_data["lane"], "offset": format_time_by_frames(clip_data["start_frame"], num, den), "duration": format_time_by_frames(duration_frames, num, den), "ref": "r2", "start": "3600s"})
        current_ts_id = f"ts{ts_counter}"
        ts_counter += 1
        append_title_components(clip, clip_data["text"], clip_data["style_cfg"], current_ts_id)

    library = ET.SubElement(fcpxml, "library")
    event = ET.SubElement(library, "event", {"name": "GloBot_Concert_Subs"})
    ET.SubElement(event, "ref-clip", {"name": f"{song_title} (精校字幕包)", "ref": "r3", "duration": master_duration_str})

    tree = ET.ElementTree(fcpxml)
    ET.indent(tree, space="    ", level=0)
    return ET.tostring(fcpxml, encoding="utf-8", xml_declaration=True).decode("utf-8")


# ==========================================
# 字体扫描与字典映射系统
# ==========================================
FONT_DICTIONARY = {
    "PingFang SC": "苹方-简 (PingFang SC)",
    "Hiragino Sans GB": "冬青黑体-简 (Hiragino Sans GB)",
    "STHeiti": "华文黑体 (STHeiti)",
    "STKaiti": "华文楷体 (STKaiti)",
    "STSong": "华文宋体 (STSong)",
    "Songti SC": "宋体-简 (Songti SC)",
    "Kaiti SC": "楷体-简 (Kaiti SC)",
    "Arial": "Arial"
}

@st.cache_data
def get_local_fonts():
    fonts_set = set(FONT_DICTIONARY.keys())
    font_dirs = ["/Library/Fonts", os.path.expanduser("~/Library/Fonts")]
    for d in font_dirs:
        if os.path.exists(d):
            for f in os.listdir(d):
                if f.lower().endswith((".ttf", ".ttc", ".otf")):
                    font_name = os.path.splitext(f)[0]
                    fonts_set.add(font_name)
    return sorted(list(fonts_set))

ALL_FONTS = get_local_fonts()

def format_font_name(font_code):
    return FONT_DICTIONARY.get(font_code, font_code)


# ==========================================
# Streamlit Web UI 构建
# ==========================================
st.set_page_config(page_title="GloBot Subs Editor", layout="wide", page_icon="🎬")

# --- 全局状态初始化 ---
if 'jp_title' not in st.session_state:
    st.session_state['jp_title'] = "キスハグ侵略者！"
if 'cn_title' not in st.session_state:
    st.session_state['cn_title'] = "《亲吻拥抱侵略者》"
if 'last_loaded_file' not in st.session_state:
    st.session_state['last_loaded_file'] = ""
if 'json_data' not in st.session_state:
    st.session_state['json_data'] = None
if 'df' not in st.session_state:
    st.session_state['df'] = None
if 'edited_df' not in st.session_state:
    st.session_state['edited_df'] = None
if 'longest_main' not in st.session_state:
    st.session_state['longest_main'] = "中文歌词默认放上面\n日本語の歌詞はここ"
if 'longest_call' not in st.session_state:
    st.session_state['longest_call'] = "うりゃおい！ｘ４\nOrya-oi ｘ４"


# ==========================================
# UI 配置持久化引擎 (自动保存用户习惯)
# ==========================================
def get_ui_config_path():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_dir, "ui_config.json")

def load_ui_config():
    path = get_ui_config_path()
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except: pass
    return {}

def save_ui_config(config_data):
    with open(get_ui_config_path(), 'w', encoding='utf-8') as f:
        json.dump(config_data, f, ensure_ascii=False, indent=4)

st.sidebar.title("🎨 FCPXML 视觉配置")

# 1. 脚本启动时，先加载本地记忆配置
saved_ui_cfg = load_ui_config()

def create_style_ui(track_key, label, default_y_px, default_x_px, default_size, default_color, default_stroke, default_stroke_w, is_main=False, is_title=False):
    # 尝试从已保存的配置中读取，如果没有则回退到代码底层的 default 值
    cfg = saved_ui_cfg.get(track_key, {})
    
    # 读取各个参数，带回退机制
    c_font = cfg.get("font", "PingFang SC")
    c_align = cfg.get("align", "left" if is_title else "center")
    c_y = cfg.get("y", default_y_px)
    c_x = cfg.get("x", default_x_px)
    c_size = cfg.get("size", default_size)
    c_color = cfg.get("color", default_color)
    c_spacing = cfg.get("spacing", 0 if is_title else (-12 if is_main else -10))
    c_stroke_c = cfg.get("stroke_c", default_stroke)
    c_stroke_w = cfg.get("stroke_w", default_stroke_w)
    c_shadow_a = cfg.get("shadow_a", 0.8)

    with st.sidebar.expander(label, expanded=is_main):
        default_idx = ALL_FONTS.index(c_font) if c_font in ALL_FONTS else (ALL_FONTS.index("PingFang SC") if "PingFang SC" in ALL_FONTS else 0)
        font_choice = st.selectbox("字体", ALL_FONTS, index=default_idx, format_func=format_font_name, key=f"{label}_font")
        
        align_choice = "center"
        if is_title:
            align_options = {"左对齐": "left", "居中": "center", "右对齐": "right"}
            align_label_idx = list(align_options.values()).index(c_align) if c_align in align_options.values() else 0
            align_label = st.radio("对齐方式", list(align_options.keys()), index=align_label_idx, horizontal=True, key=f"{label}_align")
            align_choice = align_options[align_label]
            
        col1, col2 = st.columns(2)
        with col1:
            y_px = st.number_input("Y轴位置 (px)", value=c_y, step=10, key=f"{label}_y")
            size = st.number_input("字号 (px)", value=c_size, step=1, key=f"{label}_size")
            color_hex = st.color_picker("字体颜色", value=c_color, key=f"{label}_color")
        with col2:
            x_px = st.number_input("X轴位置 (px)", value=c_x, step=10, key=f"{label}_x")
            spacing = st.number_input("行间距 (px)", value=c_spacing, step=1, key=f"{label}_spacing")
            stroke_hex = st.color_picker("边框颜色", value=c_stroke_c, key=f"{label}_stroke_c")
        
        stroke_w = st.number_input("边框粗细", value=float(abs(c_stroke_w)), step=0.5, key=f"{label}_stroke_w")
        shadow_a = st.slider("阴影透明度", 0.0, 1.0, float(c_shadow_a), key=f"{label}_shadow_a")
        
        if align_choice == "left":
            transform_css = "translate(0%, -50%)" 
        elif align_choice == "right":
            transform_css = "translate(-100%, -50%)" 
        else:
            transform_css = "translate(-50%, -50%)" 
        
        # ⚠️ 这里已经顺手把你上一条提到的 X 轴 1080P 基准算法 Bug 修复了！
        fcp_y = round((y_px / 1080) * 100, 3)
        fcp_x = round((x_px / 1080) * 100, 3) 
        
        fcp_style = {
            "font": font_choice, "fontSize": str(size), "alignment": align_choice,
            "lineSpacing": str(spacing), "fontColor": hex_to_fcp(color_hex),
            "strokeColor": hex_to_fcp(stroke_hex), "strokeWidth": str(-abs(stroke_w)), 
            "shadowColor": f"0 0 0 {shadow_a}", "shadowOffset": "3 -3", "shadowBlurRadius": "4",
            "offset_x": str(fcp_x), "offset_y": str(fcp_y) 
        }
        
        css_style = {
            "font": font_choice, "align": align_choice, "transform_css": transform_css,
            "y": y_px, "x": x_px, "size": size, "color": color_hex, "spacing": spacing, 
            "stroke_c": stroke_hex, "stroke_w": stroke_w, "shadow_a": shadow_a
        }
        return fcp_style, css_style

# 2. 传递 Track Key (main/call/title)，让函数去配置表里找记忆
fcp_main, css_main = create_style_ui("main", "📍 主歌词 (Main)", -380, 0, 48, "#ffffff", "#000000", 3.0, is_main=True)
fcp_call, css_call = create_style_ui("call", "🎤 打Call轨 (Call)", 380, 0, 38, "#e580b2", "#ffffff", 2.5)
fcp_title, css_title = create_style_ui("title", "🎵 曲名板 (Title)", 430, -770, 32, "#ffcc00", "#000000", 2.0, is_title=True)

# 3. 自动检测并把你在界面上调的最新的参数保存下来！
current_ui_cfg = {
    "main": css_main,
    "call": css_call,
    "title": css_title
}
if current_ui_cfg != saved_ui_cfg:
    save_ui_config(current_ui_cfg)

# ==========================================
current_config = {
    "project_settings": {"framerate_format": "FFVideoFormat1080p2997", "frame_duration": "1001/30000s", "resolution": {"width": "1920", "height": "1080"}},
    "styles": {"main_track": fcp_main, "call_track": fcp_call, "song_title": fcp_title}
}
fcp_title["lane"] = "3"
fcp_main["lane"] = "1"
fcp_call["lane"] = "2"


st.title("🎬 GloBot 演唱会字幕核录台")

# ==========================================
# 高能预警计算器
# ==========================================
warning_msgs = []
main_width = estimate_visual_width(st.session_state['longest_main'], css_main['size'])
if main_width > 1800:
    warning_msgs.append(f"🔴 **主轨道超长预警！** 最长的一句估算达到了 {main_width}px，如果包含大量英文可能会误报，请结合上方监视器实际效果判断。")

call_width = estimate_visual_width(st.session_state['longest_call'], css_call['size'])
if call_width > 1800:
    warning_msgs.append(f"🔴 **Call轨超长预警！** 最长的一句估算达到了 {call_width}px，如果包含大量英文可能会误报，请结合上方监视器实际效果判断。")

for msg in warning_msgs:
    st.error(msg)


# ==========================================
# 实时视觉监视器
# ==========================================
display_jp = st.session_state['jp_title']
display_cn = st.session_state['cn_title']

if display_cn and display_cn != display_jp:
    title_display_html = f"{display_cn}<br>{display_jp}"
else:
    title_display_html = f"♪ {display_jp}"

html_longest_main = st.session_state['longest_main'].replace("\n", "<br>")
html_longest_call = st.session_state['longest_call'].replace("\n", "<br>")

monitor_html = f"""
<!DOCTYPE html>
<html>
<body style="margin:0; padding:0; display:flex; justify-content:center; background:#0e1117;">
    <div style="width: 480px; height: 270px; border: 2px solid #444; border-radius: 8px; overflow: hidden; box-shadow: 0 10px 30px rgba(0,0,0,0.8);">
        <div style="width: 1920px; height: 1080px; transform: scale(0.25); transform-origin: 0 0; position: relative; background: #111 url('https://images.unsplash.com/photo-1540039155733-d7696c924e50?q=80&w=1920&auto=format&fit=crop') center/cover;">
            
            <div style="position: absolute; left: calc(50% + {css_title['x']}px); top: calc(50% - {css_title['y']}px); 
                        transform: {css_title['transform_css']}; 
                        font-family: '{css_title['font']}', sans-serif; font-size: {css_title['size']}px; color: {css_title['color']}; 
                        text-align: {css_title['align']}; font-weight: bold; white-space: nowrap;
                        line-height: calc(1.2em + {css_title['spacing']}px); 
                        -webkit-text-stroke: {css_title['stroke_w']}px {css_title['stroke_c']}; 
                        text-shadow: 3px 3px 4px rgba(0,0,0,{css_title['shadow_a']});">
                {title_display_html}<br>
                <span style="font-size: 0.6em; opacity: 0.8; color: #ffeb3b;">(已开启真实锚点对齐)</span>
            </div>

            <div style="position: absolute; left: calc(50% + {css_main['x']}px); top: calc(50% - {css_main['y']}px); 
                        transform: {css_main['transform_css']}; 
                        font-family: '{css_main['font']}', sans-serif; font-size: {css_main['size']}px; color: {css_main['color']}; 
                        text-align: {css_main['align']}; font-weight: bold; white-space: nowrap;
                        line-height: calc(1.2em + {css_main['spacing']}px); 
                        -webkit-text-stroke: {css_main['stroke_w']}px {css_main['stroke_c']}; 
                        text-shadow: 3px 3px 4px rgba(0,0,0,{css_main['shadow_a']});">
                {html_longest_main}
            </div>

            <div style="position: absolute; left: calc(50% + {css_call['x']}px); top: calc(50% - {css_call['y']}px); 
                        transform: {css_call['transform_css']}; 
                        font-family: '{css_call['font']}', sans-serif; font-size: {css_call['size']}px; color: {css_call['color']}; 
                        text-align: {css_call['align']}; font-weight: bold; white-space: nowrap;
                        line-height: calc(1.2em + {css_call['spacing']}px); 
                        -webkit-text-stroke: {css_call['stroke_w']}px {css_call['stroke_c']}; 
                        text-shadow: 2px 2px 3px rgba(0,0,0,{css_call['shadow_a']});">
                {html_longest_call}
            </div>

        </div>
    </div>
</body>
</html>
"""

components.html(monitor_html, height=300)


# --- 核心工作流选项卡 ---
tab1, tab2, tab3 = st.tabs(["📥 步骤 1: 导入 JSON", "📝 步骤 2: 精校对轴", "📤 步骤 3: 渲染导出"])

with tab1:
    dict_path = get_dict_path()
    if os.path.exists(dict_path):
        st.success(f"✅ 知识库已连接: `{dict_path}`")
    else:
        st.error(f"⚠️ 找不到知识库文件！程序预期路径为: `{dict_path}`")
        
    st.info("💡 拖入你的 JSON 歌词文件，系统将自动进行：字典翻译匹配、提取全曲最长歌词进行压力测试。")
    uploaded_file = st.file_uploader("上传已有的字幕 JSON 文件", type=['json'])
    
    if uploaded_file is not None:
        if st.session_state['last_loaded_file'] != uploaded_file.name:
            try:
                data = json.load(uploaded_file)
                st.session_state['json_data'] = data
                
                raw_title = data.get('metadata', {}).get('title', 'Unknown')
                song_title = unicodedata.normalize('NFC', raw_title)
                st.session_state['jp_title'] = song_title
                cn_title = get_cn_translation(song_title)
                st.session_state['cn_title'] = cn_title
                
                st.session_state['longest_main'] = get_longest_text(data.get("tracks", {}).get("main", []), is_main=True)
                st.session_state['longest_call'] = get_longest_text(data.get("tracks", {}).get("call", []), is_main=False)
                
                rows = []
                for track_type in ["main", "call"]:
                    for item in data.get("tracks", {}).get(track_type, []):
                        rows.append({"轨道": track_type.upper(), "开始 (s)": item["start"], "结束 (s)": item["end"], "日文": item["jp"], "中文": item["cn"]})
                
                df = pd.DataFrame(rows).sort_values(by="开始 (s)").reset_index(drop=True)
                st.session_state['df'] = df
                st.session_state['last_loaded_file'] = uploaded_file.name
                st.rerun() 
            except Exception as e:
                st.error(f"解析失败: {e}")
        else:
            current_jp = st.session_state['jp_title']
            current_cn = st.session_state['cn_title']
            st.write(f"当前加载曲目: **{current_jp}**")
            if current_cn and current_cn != current_jp:
                st.info(f"✨ 字典命中，曲名将双语显示: **{current_cn}**")
            else:
                st.warning("⚠️ 字典未命中，曲名将单行显示。")

with tab2:
    if st.session_state['df'] is not None:
        st.write("✨ **直接双击表格修改文本或时间戳（支持右键增删行）**")
        edited_df = st.data_editor(
            st.session_state['df'],
            num_rows="dynamic",
            use_container_width=True,
            column_config={
                "轨道": st.column_config.SelectboxColumn("轨道", options=["MAIN", "CALL"], required=True),
                "开始 (s)": st.column_config.NumberColumn("开始 (s)", format="%.2f", step=0.1),
                "结束 (s)": st.column_config.NumberColumn("结束 (s)", format="%.2f", step=0.1),
            },
            height=400
        )
        st.session_state['edited_df'] = edited_df
    else:
        st.warning("请先在「步骤 1」上传 JSON 文件。")

with tab3:
    if st.session_state['df'] is not None:
        song_name = st.session_state['jp_title']
        st.write(f"正在准备渲染：**{song_name}**")
        
        if st.button("🔥 生成 FCPXML", type="primary", use_container_width=True):
            final_df = st.session_state['edited_df']
            new_json = {"metadata": st.session_state['json_data'].get("metadata", {}), "tracks": {"main": [], "call": []}}
            
            # 修复：使用正确的变量 song_name 保存标题
            new_json["metadata"]["title"] = song_name 

            for _, row in final_df.iterrows():
                track_key = "main" if row["轨道"] == "MAIN" else "call"
                new_json["tracks"][track_key].append({
                    "start": float(row["开始 (s)"]), "end": float(row["结束 (s)"]),
                    "jp": str(row["日文"]), "cn": str(row["中文"])
                })
            
            try:
                xml_string = generate_fcpxml_string(new_json, current_config)
                st.success("🎉 渲染成功！样式与时间轴已全部就绪。")
                st.download_button(
                    label=f"💾 下载 {song_name}.fcpxml",
                    data=xml_string,
                    file_name=f"{song_name}.fcpxml",
                    mime="application/xml",
                    type="primary"
                )
            except Exception as e:
                st.error(f"渲染出错: {e}")
    else:
        st.warning("请先加载并确认数据。")