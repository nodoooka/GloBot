from common.text_sanitizer import sanitize_for_bilibili
from common.config_loader import BILI_ACCOUNT_NAME

def build_repost_context(prev_tw_id, dyn_map, settings_obj, id_retention_level, is_video_mode=False):
    if not prev_tw_id or prev_tw_id not in dyn_map: return ""
    prev_info = dyn_map[prev_tw_id]
    if not isinstance(prev_info, dict): return "" 
        
    p_mode = prev_info.get("publish_mode", "repost")
    if p_mode == "original": return "" 
        
    p_handle = prev_info.get("author_handle", "")
    p_disp = prev_info.get("author_display_name", p_handle)
    p_node_type = prev_info.get("node_type", "ORIGINAL")
    p_dt = prev_info.get("dt_str", "")
    p_trans = prev_info.get("translated_text", "")
    p_raw = prev_info.get("raw_text", "")
    
    p_name = settings_obj.targets.account_title_map.get(p_handle, p_disp)
    my_account = BILI_ACCOUNT_NAME
    
    if is_video_mode:
        c_trans_p = p_trans.replace('\n', ' ')
        if len(c_trans_p) > 25: c_trans_p = c_trans_p[:25] + "..."
        if p_node_type == 'REPLY':
            return f"\n//@{my_account}: 💬{p_name}回复: {c_trans_p}"
        else:
            return f"\n//@{my_account}: {p_name}: {c_trans_p}"
    else:
        if p_node_type == 'REPLY':
            c_trans_p = p_trans.replace('\n', ' ')
            c_raw_p = p_raw.replace('\n', ' ')
            return f"\n//@{my_account}: 💬{p_name}回复说： {c_trans_p} 【原文】 {c_raw_p}"
        else:
            retention_str = ""
            if id_retention_level < 3:
                retention_str = f"\n\n{prev_tw_id}\n-由GloBot驱动"
            return f"\n//@{my_account}: {p_name}\n\n{p_dt}\n\n{p_trans}\n\n【原文】\n{p_raw}{retention_str}"

def build_safe_dynamic_text(c_name, c_time, c_trans, c_raw, c_id, c_node_type, ret_level, context_suffix, ref_link, limit, debug_status=False):
    """
    B站极限裁切安全排版引擎。
    debug_status: 若开启，则返回 (内容, 裁切状态诊断文本)，专门服务于 format_tester 排版沙盒。
    """
    if c_node_type == 'RETWEET':
        text = f"{c_name} 转发\n{c_time}"
        if context_suffix: text += context_suffix
        if ref_link: text += f"\n\n🔗 溯源: {ref_link}"
        if ret_level < 3: text += f"\n\n{c_id}\n-由GloBot驱动"
        res = sanitize_for_bilibili(text[:limit])
        return (res, "✅ 纯转发: 直接裁切") if debug_status else res

    def assemble(include_tail, include_raw, truncate_trans_len=None):
        res = f"💬{c_name}回复说：\n" if c_node_type == 'REPLY' else f"{c_time}\n\n"
            
        if truncate_trans_len is not None:
            res += c_trans[:truncate_trans_len] + "..."
        else:
            res += c_trans
            
        if include_raw:
            if c_raw: res += f"\n\n(原文: {c_raw})" if c_node_type == 'REPLY' else f"\n\n【原文】\n{c_raw}"
        elif c_raw:
            res += "\n\n(原文过长已被截断)" if c_node_type == 'REPLY' else "\n\n【原文】\n...(日文原文过长，已被自动截断)"
                
        if context_suffix: res += context_suffix
        if ref_link: res += f"\n\n(🔗 溯源: {ref_link})" if c_node_type == 'REPLY' else f"\n\n🔗 溯源: {ref_link}"
                
        if include_tail and ret_level < 3:
            res += f"\n\n{c_id}"
            if c_node_type != 'REPLY': res += "\n-由GloBot驱动"
                
        return sanitize_for_bilibili(res)

    t0 = assemble(True, True)
    if len(t0) <= limit: 
        return (t0, "✅ 形态0: 完美保留全部内容") if debug_status else t0
    
    t1 = assemble(False, True)
    if len(t1) <= limit: 
        return (t1, "🟡 形态1: 切除小尾巴") if debug_status else t1
    
    t2 = assemble(False, False)
    if len(t2) <= limit: 
        return (t2, "🟠 形态2: 彻底丢弃日文原文") if debug_status else t2
    
    fixed_len = len(assemble(False, False, truncate_trans_len=0))
    avail = limit - fixed_len - 5
    if avail > 0:
        res = assemble(False, False, truncate_trans_len=avail)
        return (res, "🔴 形态3: 发生中文极限裁切") if debug_status else res
    else:
        res = t2[:limit-3] + "..."
        return (res, "💀 形态4: 彻底崩坏级裁切") if debug_status else res