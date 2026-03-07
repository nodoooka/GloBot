import streamlit as st
import streamlit.components.v1 as components
import json
import os
import pandas as pd
import subprocess
import threading
import asyncio
import hashlib
import base64
from aiohttp import web
from pathlib import Path

st.set_page_config(page_title="GloBot 演唱会打点器", layout="wide", page_icon="⏱️")

# ==========================================
# 核心黑科技 1：流媒体微服务器 (防几十G大文件崩溃)
# ==========================================
STREAM_PORT = 18080 

if 'video_server_started' not in st.session_state:
    st.session_state.video_server_started = False
    st.session_state.video_path = ""

def start_streaming_server():
    async def video_handler(request):
        b64_path = request.query.get('p', '')
        if not b64_path: return web.Response(status=404, text="Video not found")
        try:
            b64_path += '=' * (-len(b64_path) % 4) 
            v_path = base64.urlsafe_b64decode(b64_path).decode('utf-8')
        except Exception: return web.Response(status=400, text="Invalid path encoding")
        if not os.path.exists(v_path): return web.Response(status=404, text="Video not found")
        return web.FileResponse(v_path)

    async def run_server():
        app = web.Application()
        async def on_prepare(request, response):
            response.headers['Access-Control-Allow-Origin'] = '*'
        app.on_response_prepare.append(on_prepare)
        app.router.add_get('/stream_video', video_handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', STREAM_PORT)
        await site.start()
        while True: await asyncio.sleep(3600)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(run_server())

if not st.session_state.video_server_started:
    t = threading.Thread(target=start_streaming_server, daemon=True)
    t.start()
    st.session_state.video_server_started = True

# ==========================================
# 辅助函数
# ==========================================
def load_songs():
    kb_path = Path(__file__).resolve().parent.parent / "knowledge_base" / "songs.json"
    if kb_path.exists():
        try:
            with open(kb_path, 'r', encoding='utf-8') as f: return json.load(f)
        except: pass
    return {}

def pick_file_mac():
    try:
        cmd = ['osascript', '-e', 'POSIX path of (choose file with prompt "选择演唱会视频(MP4/MOV)")']
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode == 0: return res.stdout.strip()
    except: pass
    return ""

def format_time_input(time_str):
    parts = time_str.strip().split(":")
    if len(parts) == 2: return f"00:{int(parts[0]):02d}:{int(parts[1]):02d}"
    elif len(parts) == 3: return f"{int(parts[0]):02d}:{int(parts[1]):02d}:{int(parts[2]):02d}"
    return time_str

# ==========================================
# UI 状态与界面绘制
# ==========================================
if 'setlist' not in st.session_state: st.session_state.setlist = []
if 'last_uploaded' not in st.session_state: st.session_state.last_uploaded = None

st.title("⏱️ GloBot 演唱会粗轴打点器 (全自动同步版)")

col_video, col_ctrl = st.columns([1.5, 1])

with col_video:
    if st.button("📂 从本地选择演唱会视频文件...", use_container_width=True):
        selected_file = pick_file_mac()
        if selected_file and os.path.exists(selected_file):
            st.session_state.video_path = selected_file
            st.rerun()

    current_video = st.session_state.get('video_path', '')
    if current_video and os.path.exists(current_video):
        st.success(f"📺 正在播放: `{os.path.basename(current_video)}`")
        
        b64_path = base64.urlsafe_b64encode(current_video.encode('utf-8')).decode('utf-8')
        file_hash = hashlib.md5(current_video.encode()).hexdigest()
        vid_id = f"globot_vid_{file_hash}"
        
        # 1. 渲染原生视频播放器
        video_html = f"""
        <video id="{vid_id}" width="100%" controls autoplay style="border-radius: 8px; box-shadow: 0 4px 12px rgba(0,0,0,0.5);">
            <source src="http://127.0.0.1:{STREAM_PORT}/stream_video?p={b64_path}" type="video/mp4">
        </video>
        """
        st.markdown(video_html, unsafe_allow_html=True)
        
        # 2. 核心黑科技 2：注入隐形探测器，自动抓取时间并修改右侧文本框
        hijack_js = f"""
        <script>
            function initHijack() {{
                const parentDoc = window.parent.document;
                const video = parentDoc.getElementById('{vid_id}');
                if (!video) return;
                
                if (video.dataset.hijacked === 'true') return; // 防重复绑定
                video.dataset.hijacked = 'true';

                function formatTime(seconds) {{
                    let h = Math.floor(seconds / 3600);
                    let m = Math.floor((seconds % 3600) / 60);
                    let s = Math.floor(seconds % 60);
                    return h.toString().padStart(2, '0') + ':' + 
                           m.toString().padStart(2, '0') + ':' + 
                           s.toString().padStart(2, '0');
                }}

                function syncToStreamlit() {{
                    // 精准找到带有特定 placeholder 的输入框
                    const inputs = parentDoc.querySelectorAll('input[placeholder="等待视频同步..."]');
                    inputs.forEach(targetInput => {{
                        let timeStr = formatTime(video.currentTime);
                        // 骇入 React 底层，强行修改 value 并触发更新
                        let nativeInputValueSetter = Object.getOwnPropertyDescriptor(window.parent.HTMLInputElement.prototype, "value").set;
                        if (nativeInputValueSetter) {{
                            nativeInputValueSetter.call(targetInput, timeStr);
                            targetInput.dispatchEvent(new Event('input', {{ bubbles: true }}));
                        }}
                    }});
                }}

                video.addEventListener('timeupdate', syncToStreamlit);
                video.addEventListener('seeked', syncToStreamlit);
                video.addEventListener('pause', syncToStreamlit);
            }}
            
            initHijack();
            setTimeout(initHijack, 500); // 确保视频加载完毕后挂载
        </script>
        """
        components.html(hijack_js, height=0, width=0) # 0x0像素的隐身框架

    else:
        st.markdown("""
        <div style="height: 400px; display: flex; align-items: center; justify-content: center; background-color: #1e1e1e; border-radius: 8px; border: 2px dashed #444;">
            <span style="color: #666;">视频监视器区域 (请点击上方按钮加载视频)</span>
        </div>
        """, unsafe_allow_html=True)

with col_ctrl:
    st.markdown("### 📥 1. 导入原始节目单")
    uploaded_file = st.file_uploader("上传节目单 .txt 文件 (每行一个节目)", type=['txt'])
    if uploaded_file is not None:
        if st.session_state.last_uploaded != uploaded_file.name:
            content = uploaded_file.read().decode('utf-8')
            lines = [line.strip() for line in content.split('\n') if line.strip()]
            st.session_state.setlist = [{"Timestamp": "", "Node": line} for line in lines]
            st.session_state.last_uploaded = uploaded_file.name
            st.rerun()

    st.markdown("---")
    st.markdown("### 🎯 2. 快速顺序打点")
    
    untagged_idx = next((i for i, item in enumerate(st.session_state.setlist) if not item["Timestamp"]), None)
    
    if untagged_idx is not None:
        next_node = st.session_state.setlist[untagged_idx]["Node"]
        st.info(f"👉 当前等待打点: **{next_node}**")
        
        # 🚨 这里的 placeholder="等待视频同步..." 是探测器寻找目标的接头暗号，切勿修改！
        quick_time = st.text_input("⏳ 发生时间 (播放器自动同步)", placeholder="等待视频同步...", key="quick_time_input")
        
        if st.button("✅ 记录并跳到下一首", type="primary", use_container_width=True):
            if quick_time.strip():
                st.session_state.setlist[untagged_idx]["Timestamp"] = format_time_input(quick_time)
                st.session_state.setlist.sort(key=lambda x: x["Timestamp"] if x["Timestamp"] else "99:99:99")
                st.rerun()
            else:
                st.warning("请等待视频同步或手动输入时间！")
    else:
        st.success("🎉 所有节点已打点完毕！(可导入新文本或在下方手动添加)")

    with st.expander("📝 手动新增特殊节点"):
        songs_dict = load_songs()
        song_list = list(songs_dict.keys())
        
        # 重新加回 Overture 和 Encore 选项
        node_type = st.radio("节点类型", ["🎵 歌曲", "🎤 MC", "🌟 SE/Overture", "🔥 Encore"], horizontal=True)
        
        if node_type == "🎵 歌曲":
            item_name = st.selectbox("选择曲目", song_list) if song_list else st.text_input("节点名称")
        elif node_type == "🎤 MC":
            item_name = st.text_input("节点名称", value="MC")
        elif node_type == "🌟 SE/Overture":
            item_name = st.text_input("节点名称", value="SE")
        else:
            item_name = st.text_input("节点名称", value="Encore")

        manual_time = st.text_input("⏳ 发生时间", placeholder="等待视频同步...", key="manual_time")
        
        if st.button("➕ 插入列表"):
            if manual_time.strip():
                st.session_state.setlist.append({"Timestamp": format_time_input(manual_time), "Node": item_name})
                st.session_state.setlist.sort(key=lambda x: x["Timestamp"] if x["Timestamp"] else "99:99:99")
                st.rerun()
            else:
                st.warning("请等待视频同步或手动输入时间！")

    st.markdown("---")
    st.markdown("### 📋 节目单预览")
    
    if st.session_state.setlist:
        df = pd.DataFrame(st.session_state.setlist)
        edited_df = st.data_editor(df, use_container_width=True, hide_index=True, num_rows="dynamic")
        st.session_state.setlist = edited_df.to_dict('records')
        
        valid_setlist = [item for item in st.session_state.setlist if item["Timestamp"]]
        setlist_text = "\n".join([f"{item['Timestamp']} {item['Node']}" for item in valid_setlist])
        
        col_btn1, col_btn2 = st.columns(2)
        with col_btn1:
            st.download_button("💾 导出 Setlist.txt", data=setlist_text, file_name="setlist_tagged.txt", mime="text/plain", type="primary", use_container_width=True)
        with col_btn2:
            if st.button("🗑️ 清空全部", use_container_width=True):
                st.session_state.setlist = []
                st.session_state.last_uploaded = None
                st.rerun()