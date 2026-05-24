import base64
import io
import json
import os
import zipfile
from pathlib import Path

import gradio as gr
from PIL import Image, ImageEnhance, ImageFilter

# ── Supabase (optional — app works without it) ────────────────────────────────
try:
    from supabase import create_client
    _url = os.environ.get("SUPABASE_URL", "")
    _key = os.environ.get("SUPABASE_KEY", "")
    db = create_client(_url, _key) if (_url and _key) else None
except Exception:
    db = None


def _save_to_db(fmt: str, count: int, size_kb: float, filename: str, enhancements: str) -> None:
    if not db:
        return
    try:
        db.table("file_history").insert({
            "output_format": fmt,
            "image_count": count,
            "file_size_kb": round(size_kb, 1),
            "output_filename": filename,
            "enhancements": enhancements,
        }).execute()
    except Exception:
        pass


SESSION_LIMIT = 20

_RM_ONCLICK_JS = (
    "(function(n){"
    # Primary: find the upload area's X button for this file and click it
    "var area=document.getElementById('file_upload_area');"
    "if(area){"
    "var els=area.querySelectorAll('a,span,div');"
    "for(var i=0;i<els.length;i++){"
    "var el=els[i],ok=false;"
    "try{"
    "ok=el.getAttribute('download')===n||el.getAttribute('title')===n"
    "||(el.children.length===0&&el.textContent.trim()===n);"
    "if(!ok){var h=el.getAttribute('href');"
    "ok=!!h&&decodeURIComponent(h.split('/').pop())===n;}"
    "}catch(e){}"
    "if(ok){"
    "var row=el,btn=null;"
    "for(var k=0;k<6&&row&&row!==area;k++){"
    "btn=row.querySelector('button');if(btn)break;row=row.parentElement;}"
    "if(btn){btn.click();}"
    "}"
    "}"
    "}"
    # Fallback: sort_order_input bridge (also updates file_input via Python)
    "var w=document.getElementById('sort_order_input');"
    "var t=w&&(w.querySelector('textarea')||w.querySelector('input'));"
    "if(t){t.value='__DEL__:'+n;"
    "t.dispatchEvent(new Event('input',{bubbles:true}));"
    "t.dispatchEvent(new Event('change',{bubbles:true}));}"
    "})(this.dataset.del)"
)

_PRINT_STALE_HTML = ("<div style='background:#2d1a00;border:1px solid #c05621;border-radius:8px;"
                     "padding:10px 14px;margin-top:4px;'>"
                     "<p style='color:#f6ad55;font-weight:700;margin:0;'>"
                     "⚠️ มีการเปลี่ยนแปลงภาพ — กรุณากด <b>\"เตรียมปุ่มพิมพ์\"</b> ใหม่ก่อนสั่งพิมพ์"
                     "</p></div>")


def _session_count() -> int:
    if not db:
        return 0
    try:
        res = db.table("image_sessions").select("id", count="exact").execute()
        return res.count or 0
    except Exception:
        return 0


def _save_session(images: dict, order: list, settings: dict, auto_delete: bool) -> str:
    if not db:
        return "⚠️ ไม่ได้เชื่อมต่อ Supabase"
    count = _session_count()
    if count >= SESSION_LIMIT:
        if auto_delete:
            try:
                oldest = db.table("image_sessions").select("id").order("created_at").limit(1).execute()
                if oldest.data:
                    db.table("image_sessions").delete().eq("id", oldest.data[0]["id"]).execute()
            except Exception:
                pass
        else:
            return f"⚠️ ประวัติเต็มแล้ว ({SESSION_LIMIT} sessions) กรุณาลบประวัติเก่าก่อนบันทึก"
    thumbnails = {}
    image_data = {}
    for name in order:
        if name not in images:
            continue
        img = images[name]
        thumb = img.copy()
        thumb.thumbnail((120, 120), Image.LANCZOS)
        buf = io.BytesIO()
        prep_rgb(thumb).save(buf, format="JPEG", quality=70)
        thumbnails[name] = base64.b64encode(buf.getvalue()).decode()
        buf2 = io.BytesIO()
        prep_rgb(img).save(buf2, format="JPEG", quality=92)
        image_data[name] = base64.b64encode(buf2.getvalue()).decode()
    try:
        db.table("image_sessions").insert({
            "image_names": order,
            "thumbnails": thumbnails,
            "image_data": image_data,
            "settings": settings,
        }).execute()
        new_count = _session_count()
        warn = f" ⚠️ ใกล้เต็ม ({new_count}/{SESSION_LIMIT})" if new_count >= SESSION_LIMIT - 3 else ""
        return f"✅ บันทึกแล้ว ({len(order)} ภาพ){warn}"
    except Exception as e:
        return f"❌ บันทึกไม่สำเร็จ: {e}"


def _load_sessions() -> list:
    if not db:
        return []
    try:
        res = (db.table("image_sessions")
               .select("id,created_at,image_names,thumbnails,settings")
               .order("created_at", desc=True)
               .limit(SESSION_LIMIT)
               .execute())
        return res.data or []
    except Exception:
        return []


def _restore_session(sid: int):
    if not db:
        return None, None, "⚠️ ไม่ได้เชื่อมต่อ Supabase"
    try:
        res = (db.table("image_sessions")
               .select("image_names,image_data")
               .eq("id", sid)
               .limit(1)
               .execute())
        if not res.data:
            return None, None, "❌ ไม่พบ session"
        row = res.data[0]
        order = row["image_names"] or []
        image_data_map = row["image_data"] or {}
        images = {}
        for name in order:
            if name in image_data_map:
                buf = io.BytesIO(base64.b64decode(image_data_map[name]))
                img = Image.open(buf)
                img.load()
                images[name] = img
        return images, order, f"✅ โหลดแล้ว ({len(order)} ภาพ)"
    except Exception as e:
        return None, None, f"❌ โหลดไม่สำเร็จ: {e}"


def _delete_session(sid: int) -> str:
    if not db:
        return "⚠️ ไม่ได้เชื่อมต่อ Supabase"
    try:
        db.table("image_sessions").delete().eq("id", sid).execute()
        return "✅ ลบแล้ว"
    except Exception as e:
        return f"❌ ลบไม่สำเร็จ: {e}"


def _delete_all_sessions() -> str:
    if not db:
        return "⚠️ ไม่ได้เชื่อมต่อ Supabase"
    try:
        db.table("image_sessions").delete().neq("id", 0).execute()
        return "✅ ลบประวัติทั้งหมดแล้ว"
    except Exception as e:
        return f"❌ ลบไม่สำเร็จ: {e}"


def _render_history_html(sessions: list) -> str:
    if not sessions:
        return "<p style='color:#888;padding:8px;text-align:center;'>ยังไม่มีประวัติที่บันทึกไว้</p>"
    items = []
    for s in sessions:
        sid = s["id"]
        created = (s.get("created_at") or "")[:16].replace("T", " ")
        names = s.get("image_names") or []
        thumbs = s.get("thumbnails") or {}
        count = len(names)
        thumb_html = "".join(
            f'<img src="data:image/jpeg;base64,{thumbs[n]}" '
            f'style="width:38px;height:38px;object-fit:cover;border-radius:3px;border:1px solid #2d4a6b;"/>'
            for n in names[:6] if n in thumbs
        )
        if count > 6:
            thumb_html += f'<span style="color:#a0aec0;font-size:11px;align-self:center;">+{count-6}</span>'
        rp = json.dumps({"action": "restore", "id": sid}).replace('"', '&quot;')
        dp = json.dumps({"action": "delete", "id": sid}).replace('"', '&quot;')
        items.append(
            f'<div style="background:#1a2a3a;border-radius:8px;padding:10px;margin-bottom:8px;'
            f'border:1px solid #2d4a6b;">'
            f'<div style="display:flex;align-items:flex-start;gap:8px;">'
            f'<div style="flex:1;min-width:0;">'
            f'<div style="color:#e2e8f0;font-size:12px;font-weight:600;">🕐 {created}'
            f' &nbsp;<span style="color:#63b3ed;">{count} ภาพ</span></div>'
            f'<div style="display:flex;gap:3px;margin-top:5px;flex-wrap:wrap;">{thumb_html}</div>'
            f'</div>'
            f'<div style="display:flex;gap:5px;flex-shrink:0;margin-top:2px;">'
            f'<button onclick="(function(){{var w=document.getElementById(\'history_action_input\');'
            f'if(w){{var t=w.querySelector(\'textarea\')||w.querySelector(\'input\');'
            f'if(t){{t.value=\'{rp}\';t.dispatchEvent(new Event(\'input\',{{bubbles:true}}));}}}}}})();"'
            f' style="padding:4px 10px;background:#1a56a0;color:white;border:none;'
            f'border-radius:6px;font-size:12px;cursor:pointer;">📂 โหลด</button>'
            f'<button onclick="if(confirm(\'ลบ session {created}?\')){{(function(){{var w=document.getElementById(\'history_action_input\');'
            f'if(w){{var t=w.querySelector(\'textarea\')||w.querySelector(\'input\');'
            f'if(t){{t.value=\'{dp}\';t.dispatchEvent(new Event(\'input\',{{bubbles:true}}));}}}}}})();}}"'
            f' style="padding:4px 10px;background:#9b2335;color:white;border:none;'
            f'border-radius:6px;font-size:12px;cursor:pointer;">🗑️</button>'
            f'</div></div></div>'
        )
    cnt = len(sessions)
    warn_style = "color:#f6ad55;" if cnt >= SESSION_LIMIT - 3 else "color:#a0aec0;"
    header = (f"<p style='{warn_style}font-size:12px;margin-bottom:8px;'>"
              f"{'⚠️ ใกล้เต็ม! ' if cnt >= SESSION_LIMIT - 3 else ''}ประวัติ {cnt} / {SESSION_LIMIT}</p>")
    return header + "".join(items)


# ── Image helpers ─────────────────────────────────────────────────────────────
def _hex_to_rgb(hex_color: str) -> tuple:
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def prep_rgb(img: Image.Image, bg_hex: str = "#FFFFFF") -> Image.Image:
    if img.mode == "RGBA":
        bg = Image.new("RGB", img.size, _hex_to_rgb(bg_hex))
        bg.paste(img, mask=img.split()[3])
        return bg
    return img.convert("RGB") if img.mode != "RGB" else img


def apply_enhancements(img, sharpness, contrast, use_unsharp,
                        unsharp_radius, unsharp_pct, unsharp_thresh):
    if contrast != 1.0:
        img = ImageEnhance.Contrast(img).enhance(contrast)
    if use_unsharp:
        img = img.filter(ImageFilter.UnsharpMask(
            radius=unsharp_radius, percent=int(unsharp_pct), threshold=int(unsharp_thresh)
        ))
    elif sharpness != 1.0:
        img = ImageEnhance.Sharpness(img).enhance(sharpness)
    return img



def _render_sortable_html(order: list) -> str:
    if not order:
        return "<p style='color:#888;padding:8px;'>ยังไม่มีภาพ</p>"
    uid = abs(hash(str(order))) % 999999
    items = "".join(
        f'<div class="si" data-name="{name.replace(chr(34), "&quot;")}" '
        f'style="display:flex;align-items:center;gap:8px;padding:8px 10px;margin:3px 0;'
        f'background:#1e3a5f;border-radius:6px;border-left:3px solid #63b3ed;'
        f'cursor:grab;user-select:none;">'
        f'<span class="n" style="background:#63b3ed;color:#0a1628;border-radius:50%;'
        f'min-width:24px;height:24px;display:inline-flex;align-items:center;justify-content:center;'
        f'font-weight:bold;font-size:11px;flex-shrink:0;">{i+1}</span>'
        f'<span style="color:#e2e8f0;flex:1;word-break:break-all;font-size:13px;">☰ {name}</span>'
        f'<button type="button" class="rm-btn" data-del="{name.replace(chr(34), "&quot;")}"'
        f' onclick="{_RM_ONCLICK_JS}"'
        f' style="background:rgba(180,30,30,0.85);color:white;border:none;border-radius:4px;'
        f'padding:1px 8px;cursor:pointer;font-size:15px;line-height:1.4;flex-shrink:0;"'
        f' title="ลบภาพนี้">✕</button>'
        f'</div>'
        for i, name in enumerate(order)
    )
    return f"""<div id="sc{uid}" style="max-height:300px;overflow-y:auto;padding:2px;">{items}</div>
<style>.sg{{opacity:.5;background:#2d5a8e!important;}}</style>
<img src="x{uid}" onerror="(function(){{
  var go=function(){{
    var c=document.getElementById('sc{uid}');
    if(!c||c._s)return;c._s=true;
    new Sortable(c,{{animation:150,draggable:'.si',ghostClass:'sg',
      filter:'.rm-btn',preventOnFilter:true,onEnd:function(){{
      var it=c.querySelectorAll('.si');
      it.forEach(function(x,i){{x.querySelector('.n').textContent=i+1;}});
      var o=Array.from(it).map(function(x){{return x.dataset.name;}});
      var w=document.getElementById('sort_order_input');
      if(w){{var t=w.querySelector('textarea')||w.querySelector('input');
        if(t){{t.value=JSON.stringify(o);t.dispatchEvent(new Event('input',{{bubbles:true}}));}}}}
    }}}});
  }};
  if(window.Sortable){{go();}}
  else if(!window._sll){{window._sll=true;
    var s=document.createElement('script');
    s.src='https://cdn.jsdelivr.net/npm/sortablejs@1.15.0/Sortable.min.js';
    s.onload=go;document.head.appendChild(s);
  }}else{{setTimeout(function(){{if(window.Sortable)go();}},300);}}
}})();" style="display:none">"""


def on_sort_change(new_order_json, images_state, order_state, current_files):
    _no_change = (gr.update(), order_state, gr.update(), gr.update(), gr.update(), gr.update(), gr.update())
    if not new_order_json:
        return _no_change
    if new_order_json.startswith("__DEL__:"):
        name = new_order_json[8:]
        return on_remove_by_name(name, images_state, order_state, current_files)
    try:
        new_order = json.loads(new_order_json)
        new_order = [n for n in new_order if n in (images_state or {})]
        if not new_order:
            return _no_change
        return (gr.update(), new_order, _render_sortable_html(new_order),
                _render_sortable_gallery_html(images_state, new_order),
                gr.update(choices=new_order), _PRINT_STALE_HTML, gr.update())
    except Exception:
        return _no_change


def on_gallery_select(order_state, evt: gr.SelectData):
    idx = evt.index
    if order_state and 0 <= idx < len(order_state):
        return gr.update(value=order_state[idx])
    return gr.update()


def _render_sortable_gallery_html(images: dict, order: list) -> str:
    if not order:
        return "<p style='color:#888;padding:20px;text-align:center;'>อัปโหลดภาพเพื่อดู Preview</p>"
    uid = abs(hash("gal" + str(order))) % 999999
    items = []
    full_imgs: dict[str, str] = {}
    valid_names = []

    for i, name in enumerate(order):
        if name not in images:
            continue
        img = images[name]
        safe = name.replace('"', "&quot;").replace("'", "&#39;")
        valid_names.append(name)

        thumb = img.copy()
        thumb.thumbnail((180, 180), Image.LANCZOS)
        buf = io.BytesIO()
        prep_rgb(thumb).save(buf, format="JPEG", quality=72)
        thumb_b64 = base64.b64encode(buf.getvalue()).decode()

        full = img.copy()
        full.thumbnail((900, 900), Image.LANCZOS)
        buf = io.BytesIO()
        prep_rgb(full).save(buf, format="JPEG", quality=92)
        full_imgs[name] = base64.b64encode(buf.getvalue()).decode()

        items.append(
            f'<div class="gi" data-name="{safe}" '
            f'style="display:flex;flex-direction:column;align-items:center;cursor:grab;'
            f'background:#1e2a3a;border-radius:8px;padding:6px;user-select:none;'
            f'border:2px solid transparent;" '
            f'onmouseenter="this.style.borderColor=\'#63b3ed\'" '
            f'onmouseleave="this.style.borderColor=\'transparent\'">'
            f'<div class="n" style="background:#2d3e50;border-radius:4px;width:100%;'
            f'text-align:center;font-size:11px;color:#63b3ed;padding:2px 4px;'
            f'margin-bottom:4px;font-weight:bold;">{i+1}</div>'
            f'<div style="position:relative;width:100%;text-align:center;">'
            f'<img src="data:image/jpeg;base64,{thumb_b64}" '
            f'style="max-width:100%;max-height:140px;object-fit:contain;border-radius:4px;'
            f'pointer-events:none;display:block;margin:auto;"/>'
            f'<button class="lb-btn" onclick="lbOpen_{uid}({len(valid_names)-1})" '
            f'style="position:absolute;top:2px;right:2px;background:rgba(0,0,0,0.72);'
            f'color:white;border:none;border-radius:4px;padding:2px 7px;cursor:pointer;'
            f'font-size:14px;line-height:1.4;" title="ดูภาพเต็ม">⛶</button>'
            f'<button type="button" class="rm-btn" data-del="{safe}"'
            f' onclick="{_RM_ONCLICK_JS}"'
            f' style="position:absolute;top:2px;left:2px;background:rgba(180,30,30,0.85);'
            f'color:white;border:none;border-radius:4px;padding:2px 7px;cursor:pointer;'
            f'font-size:14px;line-height:1.4;" title="ลบภาพนี้">✕</button>'
            f'</div>'
            f'<div style="font-size:10px;color:#a0aec0;margin-top:4px;word-break:break-all;'
            f'text-align:center;width:100%;overflow:hidden;text-overflow:ellipsis;'
            f'white-space:nowrap;" title="{safe}">{name}</div>'
            f'</div>'
        )

    items_html = "".join(items)
    full_data = {"names": valid_names, "imgs": full_imgs}
    full_json = json.dumps(full_data)
    nav_btn = ("position:absolute;top:50%;transform:translateY(-50%);"
               "background:rgba(255,255,255,0.15);color:white;border:none;"
               "border-radius:50%;width:48px;height:48px;font-size:24px;"
               "cursor:pointer;z-index:3;transition:opacity 0.2s;")

    return f"""
<div id="lb{uid}" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,0.93);
  z-index:99999;align-items:center;justify-content:center;flex-direction:column;overflow:hidden;">
  <button onclick="document.getElementById('lb{uid}').style.display='none'"
    style="position:absolute;top:14px;right:18px;background:rgba(255,255,255,0.15);
    color:white;border:none;border-radius:50%;width:42px;height:42px;font-size:22px;
    cursor:pointer;z-index:3;">✕</button>
  <div style="position:absolute;top:14px;left:50%;transform:translateX(-50%);
    display:flex;gap:6px;align-items:center;z-index:3;">
    <button onclick="lbZoom_{uid}(1.3)"
      style="background:rgba(255,255,255,0.15);color:white;border:none;border-radius:6px;
      width:36px;height:36px;font-size:18px;cursor:pointer;">+</button>
    <button onclick="lbZoom_{uid}(1/1.3)"
      style="background:rgba(255,255,255,0.15);color:white;border:none;border-radius:6px;
      width:36px;height:36px;font-size:18px;cursor:pointer;">−</button>
    <button onclick="lbReset_{uid}()"
      style="background:rgba(255,255,255,0.15);color:white;border:none;border-radius:6px;
      padding:0 10px;height:36px;font-size:12px;cursor:pointer;">รีเซ็ต</button>
    <span id="lbz{uid}" style="color:rgba(255,255,255,0.7);font-size:12px;
      min-width:44px;text-align:center;">100%</span>
    <span id="lbct{uid}" style="color:rgba(255,255,255,0.85);font-size:13px;
      background:rgba(255,255,255,0.12);padding:2px 12px;border-radius:12px;">1 / 1</span>
  </div>
  <button id="lbprev{uid}" onclick="lbNav_{uid}(-1)"
    style="{nav_btn}left:12px;">❮</button>
  <button id="lbnext{uid}" onclick="lbNav_{uid}(1)"
    style="{nav_btn}right:12px;">❯</button>
  <div id="lbw{uid}" style="overflow:hidden;width:84vw;height:82vh;display:flex;
    align-items:center;justify-content:center;cursor:default;">
    <img id="lbi{uid}" style="max-width:84vw;max-height:82vh;object-fit:contain;
      border-radius:8px;box-shadow:0 8px 40px rgba(0,0,0,0.9);
      transform-origin:center;user-select:none;pointer-events:none;"/>
  </div>
  <div id="lbc{uid}" style="margin-top:8px;color:rgba(255,255,255,0.85);font-size:13px;
    background:rgba(0,0,0,0.55);padding:4px 16px;border-radius:20px;
    max-width:80vw;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;z-index:2;"></div>
</div>
<div id="gc{uid}" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));
  gap:8px;max-height:460px;overflow-y:auto;padding:4px;">{items_html}</div>
<script type="application/json" id="fi{uid}">{full_json}</script>
<img src="gi{uid}" onerror="(function(){{
  var fd=JSON.parse(document.getElementById('fi{uid}').textContent);
  var _names{uid}=fd.names,_fi{uid}=fd.imgs;
  var _sc{uid}=1,_tx{uid}=0,_ty{uid}=0,_drag{uid}=false,_sx{uid}=0,_sy{uid}=0,_idx{uid}=0;
  function _applyT{uid}(){{
    var img=document.getElementById('lbi{uid}');
    img.style.transform='scale('+_sc{uid}+') translate('+_tx{uid}/_sc{uid}+'px,'+_ty{uid}/_sc{uid}+'px)';
    document.getElementById('lbz{uid}').textContent=Math.round(_sc{uid}*100)+'%';
    document.getElementById('lbw{uid}').style.cursor=_sc{uid}>1?'grab':'default';
  }}
  window.lbZoom_{uid}=function(f){{_sc{uid}=Math.min(8,Math.max(0.5,_sc{uid}*f));_applyT{uid}();}};
  window.lbReset_{uid}=function(){{_sc{uid}=1;_tx{uid}=0;_ty{uid}=0;_applyT{uid}();}};
  function lbShow_{uid}(){{
    var name=_names{uid}[_idx{uid}];
    var b64=_fi{uid}[name];if(!b64)return;
    _sc{uid}=1;_tx{uid}=0;_ty{uid}=0;
    var img=document.getElementById('lbi{uid}');
    img.src='data:image/jpeg;base64,'+b64;
    img.style.transform='';
    document.getElementById('lbz{uid}').textContent='100%';
    document.getElementById('lbct{uid}').textContent=(_idx{uid}+1)+' / '+_names{uid}.length;
    document.getElementById('lbc{uid}').textContent=(_idx{uid}+1)+'. '+name;
    document.getElementById('lbprev{uid}').style.opacity=_idx{uid}===0?'0.25':'1';
    document.getElementById('lbnext{uid}').style.opacity=_idx{uid}===_names{uid}.length-1?'0.25':'1';
  }}
  window.lbNav_{uid}=function(d){{
    _idx{uid}=Math.max(0,Math.min(_names{uid}.length-1,_idx{uid}+d));
    lbShow_{uid}();
  }};
  window.lbOpen_{uid}=function(idx){{
    _idx{uid}=idx;lbShow_{uid}();
    document.getElementById('lb{uid}').style.display='flex';
  }};
  var lbw=document.getElementById('lbw{uid}');
  lbw.addEventListener('wheel',function(e){{
    if(!e.ctrlKey&&!e.metaKey)return;
    e.preventDefault();
    _sc{uid}=Math.min(8,Math.max(0.5,_sc{uid}*(e.deltaY<0?1.15:0.87)));
    _applyT{uid}();
  }},{{passive:false}});
  lbw.addEventListener('mousedown',function(e){{
    if(_sc{uid}<=1)return;
    _drag{uid}=true;_sx{uid}=e.clientX-_tx{uid};_sy{uid}=e.clientY-_ty{uid};
    lbw.style.cursor='grabbing';e.preventDefault();
  }});
  window.addEventListener('mousemove',function(e){{
    if(!_drag{uid})return;
    _tx{uid}=e.clientX-_sx{uid};_ty{uid}=e.clientY-_sy{uid};_applyT{uid}();
  }});
  window.addEventListener('mouseup',function(){{
    _drag{uid}=false;
    var w=document.getElementById('lbw{uid}');
    if(w)w.style.cursor=_sc{uid}>1?'grab':'default';
  }});
  document.getElementById('lbi{uid}').addEventListener('dblclick',function(){{
    if(_sc{uid}>1){{_sc{uid}=1;_tx{uid}=0;_ty{uid}=0;}}else{{_sc{uid}=2;}}
    _applyT{uid}();
  }});
  var lb=document.getElementById('lb{uid}');
  lb.onclick=function(e){{if(e.target===lb||e.target===lbw){{lbReset_{uid}();lb.style.display='none';}}}};
  document.addEventListener('keydown',function(e){{
    if(!lb||lb.style.display==='none')return;
    if(e.key==='Escape'){{lbReset_{uid}();lb.style.display='none';}}
    else if(e.key==='ArrowLeft')lbNav_{uid}(-1);
    else if(e.key==='ArrowRight')lbNav_{uid}(1);
    else if(e.key==='+'||e.key==='=')lbZoom_{uid}(1.3);
    else if(e.key==='-')lbZoom_{uid}(1/1.3);
  }});
  var go=function(){{
    var c=document.getElementById('gc{uid}');
    if(!c||c._s)return;c._s=true;
    new Sortable(c,{{animation:150,draggable:'.gi',ghostClass:'sg2',
      filter:'.lb-btn,.rm-btn',preventOnFilter:true,onEnd:function(){{
      var it=c.querySelectorAll('.gi');
      it.forEach(function(x,i){{x.querySelector('.n').textContent=i+1;}});
      var o=Array.from(it).map(function(x){{return x.dataset.name;}});
      var w=document.getElementById('sort_order_input');
      if(w){{var t=w.querySelector('textarea')||w.querySelector('input');
        if(t){{t.value=JSON.stringify(o);t.dispatchEvent(new Event('input',{{bubbles:true}}));}}}}
    }}}});
  }};
  if(window.Sortable){{go();}}
  else if(!window._sll){{window._sll=true;
    var s=document.createElement('script');
    s.src='https://cdn.jsdelivr.net/npm/sortablejs@1.15.0/Sortable.min.js';
    s.onload=go;document.head.appendChild(s);
  }}else{{setTimeout(function(){{if(window.Sortable)go();}},300);}}
}})();" style="display:none">
<style>.sg2{{opacity:.5;outline:2px dashed #63b3ed;}}</style>"""


# ── Event handlers ────────────────────────────────────────────────────────────
def on_remove_by_name(name, images_state, order_state, current_files=None):
    if not name:
        return gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), gr.update()
    order = list(order_state)
    images = dict(images_state)
    if name in order:
        order.remove(name)
    images.pop(name, None)
    new_sel = order[0] if order else None
    # Remove the file from gr.File component too, to keep upload area in sync
    new_files = gr.update()
    if current_files:
        filtered = [f for f in current_files
                    if Path(f.name if hasattr(f, "name") else str(f)).name != name]
        if len(filtered) < len(current_files):
            new_files = filtered if filtered else None
    return (
        images, order, _render_sortable_html(order),
        _render_sortable_gallery_html(images, order),
        gr.update(choices=order, value=new_sel),
        _PRINT_STALE_HTML,
        new_files,
    )


def on_upload(files, images_state, order_state):
    if not files:
        return {}, [], _render_sortable_html([]), _render_sortable_gallery_html({}, []), gr.update(choices=[], value=None), _PRINT_STALE_HTML

    images = dict(images_state) if images_state else {}

    # Load newly added files
    current_names = set()
    for f in files:
        path = f.name if hasattr(f, "name") else str(f)
        name = Path(path).name
        current_names.add(name)
        if name not in images:
            try:
                img = Image.open(path)
                img.load()
                if img.mode == "P":
                    img = img.convert("RGBA" if "transparency" in img.info else "RGB")
                images[name] = img
            except Exception:
                pass

    # Remove images deleted from the file input
    for name in list(images.keys()):
        if name not in current_names:
            del images[name]

    order = list(order_state) if order_state else []
    for name in images:
        if name not in order:
            order.append(name)
    order = [n for n in order if n in images]

    return (
        images,
        order,
        _render_sortable_html(order),
        _render_sortable_gallery_html(images, order),
        gr.update(choices=order, value=order[0] if order else None),
        _PRINT_STALE_HTML,
    )


def move_up(selected, images_state, order_state):
    order = list(order_state)
    if selected and selected in order:
        i = order.index(selected)
        if i > 0:
            order[i], order[i - 1] = order[i - 1], order[i]
    return order, _render_sortable_html(order), _render_sortable_gallery_html(images_state, order), gr.update(choices=order, value=selected), _PRINT_STALE_HTML


def move_down(selected, images_state, order_state):
    order = list(order_state)
    if selected and selected in order:
        i = order.index(selected)
        if i < len(order) - 1:
            order[i], order[i + 1] = order[i + 1], order[i]
    return order, _render_sortable_html(order), _render_sortable_gallery_html(images_state, order), gr.update(choices=order, value=selected), _PRINT_STALE_HTML


def remove_image(selected, images_state, order_state):
    order = list(order_state)
    images = dict(images_state)
    if selected and selected in order:
        order.remove(selected)
        images.pop(selected, None)
    new_sel = order[0] if order else None
    return (
        images, order, _render_sortable_html(order),
        _render_sortable_gallery_html(images, order), gr.update(choices=order, value=new_sel),
        _PRINT_STALE_HTML,
    )


def clear_all():
    return {}, [], _render_sortable_html([]), _render_sortable_gallery_html({}, []), gr.update(choices=[], value=None), "", "", _PRINT_STALE_HTML, None


def generate(
    images_state, order_state,
    output_format, output_name,
    sharpen, contrast_val, use_unsharp,
    unsharp_radius, unsharp_pct, unsharp_thresh,
    pdf_quality, pdf_page_size,
    max_dim, compress_level, bg_color,
    zip_quality,
):
    if not images_state or not order_state:
        return None, "<p style='color:#c53030'>⚠️ ยังไม่มีภาพ กรุณาอัปโหลดก่อน</p>"

    ordered = [(n, images_state[n]) for n in order_state if n in images_state]
    if not ordered:
        return None, "<p style='color:#c53030'>⚠️ ไม่มีภาพ</p>"

    names = [n for n, _ in ordered]
    imgs = [
        apply_enhancements(
            img.copy(), sharpen, contrast_val, use_unsharp,
            unsharp_radius, unsharp_pct, unsharp_thresh,
        )
        for _, img in ordered
    ]

    fname = (output_name or "merged_images").strip()
    enh_parts = []
    if sharpen != 1.0: enh_parts.append(f"sharpness×{sharpen:.1f}")
    if contrast_val != 1.0: enh_parts.append(f"contrast×{contrast_val:.1f}")
    if use_unsharp: enh_parts.append("unsharp")
    enh_str = ", ".join(enh_parts) or "none"

    buf = io.BytesIO()

    if output_format == "PDF":
        pdf_imgs = []
        for img in imgs:
            rgb = prep_rgb(img)
            if pdf_page_size == "A4 แนวตั้ง":
                pw, ph = 2480, 3508
            elif pdf_page_size == "A4 แนวนอน":
                pw, ph = 3508, 2480
            else:
                pw, ph = rgb.width, rgb.height

            if pdf_page_size != "ตามขนาดภาพ":
                rgb.thumbnail((pw, ph), Image.LANCZOS)
                page = Image.new("RGB", (pw, ph), "white")
                page.paste(rgb, ((pw - rgb.width) // 2, (ph - rgb.height) // 2))
                rgb = page
            pdf_imgs.append(rgb)

        pdf_imgs[0].save(buf, format="PDF", save_all=True,
                          append_images=pdf_imgs[1:], quality=int(pdf_quality))
        ext = "pdf"
        msg = f"✅ PDF สำเร็จ! {len(pdf_imgs)} หน้า"

    elif "แนวตั้ง" in output_format:
        rgb_imgs = [prep_rgb(img, bg_color) for img in imgs]
        d = int(max_dim)
        if d > 0:
            rgb_imgs = [
                img.resize((d, int(img.height * d / img.width)), Image.LANCZOS)
                if img.width > d else img for img in rgb_imgs
            ]
        max_w = max(img.width for img in rgb_imgs)
        total_h = sum(img.height for img in rgb_imgs)
        canvas = Image.new("RGB", (max_w, total_h), _hex_to_rgb(bg_color))
        y = 0
        for img in rgb_imgs:
            canvas.paste(img, ((max_w - img.width) // 2, y))
            y += img.height
        canvas.save(buf, format="PNG", compress_level=int(compress_level))
        ext = "png"
        msg = f"✅ ต่อภาพแนวตั้งสำเร็จ! {canvas.width}×{canvas.height} px"

    elif "แนวนอน" in output_format:
        rgb_imgs = [prep_rgb(img, bg_color) for img in imgs]
        d = int(max_dim)
        if d > 0:
            rgb_imgs = [
                img.resize((int(img.width * d / img.height), d), Image.LANCZOS)
                if img.height > d else img for img in rgb_imgs
            ]
        total_w = sum(img.width for img in rgb_imgs)
        max_h = max(img.height for img in rgb_imgs)
        canvas = Image.new("RGB", (total_w, max_h), _hex_to_rgb(bg_color))
        x = 0
        for img in rgb_imgs:
            canvas.paste(img, (x, (max_h - img.height) // 2))
            x += img.width
        canvas.save(buf, format="PNG", compress_level=int(compress_level))
        ext = "png"
        msg = f"✅ ต่อภาพแนวนอนสำเร็จ! {canvas.width}×{canvas.height} px"

    else:  # ZIP
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for name, img in zip(names, imgs):
                img_buf = io.BytesIO()
                if Path(name).suffix.lower() in (".jpg", ".jpeg"):
                    img.convert("RGB").save(img_buf, format="JPEG", quality=int(zip_quality))
                else:
                    img.save(img_buf, format="PNG")
                img_buf.seek(0)
                zf.writestr(name, img_buf.read())
        ext = "zip"
        msg = f"✅ ZIP สำเร็จ! {len(imgs)} ไฟล์"

    buf.seek(0)
    data = buf.getvalue()
    size_kb = len(data) / 1024

    _save_to_db(output_format, len(imgs), size_kb, fname, enh_str)

    mime_map = {"pdf": "application/pdf", "png": "image/png", "zip": "application/zip"}
    mime = mime_map[ext]
    b64_file = base64.b64encode(data).decode()

    # Build result preview HTML
    _grid = "display:flex;flex-wrap:wrap;gap:8px;max-height:420px;overflow-y:auto;padding:4px;"
    if ext in ("png",):
        prev = canvas.copy()
        prev.thumbnail((700, 2000), Image.LANCZOS)
        pbuf = io.BytesIO()
        prev.save(pbuf, format="JPEG", quality=82)
        prev_b64 = base64.b64encode(pbuf.getvalue()).decode()
        preview_html = (
            f"<p style='color:#888;font-size:12px;margin:4px 0;'>"
            f"ตัวอย่างผลลัพธ์ ({canvas.width}×{canvas.height} px):</p>"
            f"<img src='data:image/jpeg;base64,{prev_b64}' "
            f"style='max-width:100%;border-radius:8px;border:1px solid #2d3e50;'/>"
        )
    elif ext == "pdf":
        thumb_items = []
        for i, pimg in enumerate(pdf_imgs):
            prev = pimg.copy()
            prev.thumbnail((220, 310), Image.LANCZOS)
            pbuf = io.BytesIO()
            prev.save(pbuf, format="JPEG", quality=80)
            prev_b64 = base64.b64encode(pbuf.getvalue()).decode()
            thumb_items.append(
                f"<div style='display:flex;flex-direction:column;align-items:center;gap:4px;'>"
                f"<img src='data:image/jpeg;base64,{prev_b64}' "
                f"style='max-width:150px;border-radius:4px;border:1px solid #2d3e50;"
                f"box-shadow:0 2px 8px rgba(0,0,0,0.4);'/>"
                f"<span style='font-size:11px;color:#a0aec0;'>หน้า {i+1}</span>"
                f"</div>"
            )
        preview_html = (
            f"<p style='color:#888;font-size:12px;margin:4px 0 8px;'>ตัวอย่างทั้ง {len(pdf_imgs)} หน้า:</p>"
            f"<div style='{_grid}'>" + "".join(thumb_items) + "</div>"
        )
    else:
        thumb_items = []
        for i, (name, img) in enumerate(zip(names, imgs)):
            prev = img.copy()
            prev.thumbnail((120, 120), Image.LANCZOS)
            pbuf = io.BytesIO()
            prep_rgb(prev).save(pbuf, format="JPEG", quality=75)
            prev_b64 = base64.b64encode(pbuf.getvalue()).decode()
            safe = name.replace('"', "&quot;")
            thumb_items.append(
                f"<div style='display:flex;flex-direction:column;align-items:center;gap:2px;'>"
                f"<img src='data:image/jpeg;base64,{prev_b64}' "
                f"style='width:80px;height:80px;object-fit:contain;border-radius:4px;"
                f"border:1px solid #2d3e50;'/>"
                f"<span style='font-size:10px;color:#a0aec0;max-width:90px;overflow:hidden;"
                f"text-overflow:ellipsis;white-space:nowrap;' title='{safe}'>{name}</span>"
                f"</div>"
            )
        preview_html = (
            f"<p style='color:#888;font-size:12px;margin:4px 0 8px;'>ไฟล์ใน ZIP ({len(names)} ไฟล์):</p>"
            f"<div style='{_grid}'>" + "".join(thumb_items) + "</div>"
        )

    status_html = f"""
<p style='color:#276749;font-weight:600;margin-bottom:8px;'>{msg} ({size_kb:.0f} KB)</p>
<a href="data:{mime};base64,{b64_file}" download="{fname}.{ext}"
   style="display:inline-block;padding:11px 28px;background:#276749;color:white;
   text-decoration:none;border-radius:8px;font-weight:bold;font-size:15px;">
   📥 ดาวน์โหลด {fname}.{ext}
</a>"""

    return status_html, preview_html


def make_print_html(images_state, order_state, print_paper, print_orient, print_quality):
    if not images_state or not order_state:
        return "<p style='color:#888'>อัปโหลดภาพก่อน แล้วกดปุ่มเตรียมพิมพ์</p>"

    ordered = [(n, images_state[n]) for n in order_state if n in images_state]
    if not ordered:
        return "<p style='color:#888'>ไม่มีภาพ</p>"

    sizes = {"A4": (1654, 2339), "A3": (2339, 3307), "Letter": (1700, 2200)}
    pw, ph = sizes.get(print_paper, (1654, 2339))
    if print_orient == "แนวนอน":
        pw, ph = ph, pw

    img_b64s = []
    for _, img in ordered:
        rgb = prep_rgb(img)
        rgb.thumbnail((pw, ph), Image.LANCZOS)
        buf = io.BytesIO()
        rgb.save(buf, format="JPEG", quality=int(print_quality))
        img_b64s.append(base64.b64encode(buf.getvalue()).decode())

    orient_css = "portrait" if print_orient == "แนวตั้ง" else "landscape"
    uid = abs(hash(str([n for n, _ in ordered]))) % 999999

    # Use mm dimensions so page-break-after works correctly (vw/vh break with position:fixed)
    mm = {"A4": (210, 297), "A3": (297, 420), "Letter": (216, 279)}
    pw_mm, ph_mm = mm.get(print_paper, (210, 297))
    if print_orient == "แนวนอน":
        pw_mm, ph_mm = ph_mm, pw_mm

    pages_html = "".join(
        f'<div class="prp{uid}"><img src="data:image/jpeg;base64,{b64}"/></div>'
        for b64 in img_b64s
    )

    # Build print preview thumbnails (A4-ratio cards)
    preview_cards = []
    for i, ((name, _), b64) in enumerate(zip(ordered, img_b64s)):
        safe = name.replace('"', "&quot;")
        preview_cards.append(
            f'<div style="display:flex;flex-direction:column;align-items:center;gap:4px;">'
            f'<div style="background:#0a1628;border:1px solid #2d4a6b;border-radius:4px;'
            f'width:72px;height:102px;display:flex;align-items:center;justify-content:center;overflow:hidden;">'
            f'<img src="data:image/jpeg;base64,{b64}" '
            f'style="max-width:100%;max-height:100%;object-fit:contain;" title="{safe}"/>'
            f'</div>'
            f'<span style="font-size:10px;color:#a0aec0;">{i+1}</span>'
            f'</div>'
        )
    preview_grid = (
        f'<p style="color:#a0aec0;font-size:11px;margin:0 0 6px;">ตัวอย่างการจัดเรียง ({len(ordered)} ภาพ):</p>'
        f'<div style="display:flex;flex-wrap:wrap;gap:6px;max-height:200px;overflow-y:auto;'
        f'padding:6px;background:#0d1e30;border-radius:6px;border:1px solid #1e3a5f;margin-bottom:10px;">'
        + "".join(preview_cards) + "</div>"
    )

    # onclick: move overlay to direct body child → CSS body.prnt{uid} > *:not(overlay)
    # hides all Gradio UI → page-break works in normal block flow → one image per page.
    # onafterprint moves overlay back and removes class.
    return f"""<style>
@page{{size:{print_paper} {orient_css};margin:0}}
@media print{{
  body.prnt{uid}>*:not(#pro{uid}){{display:none!important}}
  .prp{uid}{{
    width:{pw_mm}mm!important;height:{ph_mm}mm!important;
    display:flex!important;align-items:center!important;
    justify-content:center!important;overflow:hidden!important;
    background:white!important;page-break-after:always!important;
  }}
  .prp{uid}:last-child{{page-break-after:auto!important}}
  .prp{uid} img{{max-width:100%!important;max-height:100%!important;
    object-fit:contain!important;display:block!important}}
}}
</style>
<div id="pro{uid}" style="display:none;">{pages_html}</div>
<div style="background:#162032;border-radius:10px;padding:14px;border:1px solid #2d4a6b;margin-top:4px;">
  <p style="color:#68d391;font-weight:700;margin-bottom:10px;text-align:center;font-size:14px;">
    ✅ เตรียมพร้อมแล้ว {len(ordered)} ภาพ &nbsp;|&nbsp; {print_paper} · {orient_css}
  </p>
  {preview_grid}
  <button onclick="var o=document.getElementById('pro{uid}'),op=o.parentNode,ns=o.nextSibling;document.body.appendChild(o);o.style.display='block';document.body.classList.add('prnt{uid}');var done=function(){{document.body.classList.remove('prnt{uid}');o.style.display='none';try{{ns?op.insertBefore(o,ns):op.appendChild(o);}}catch(e){{}}window.onafterprint=null;}};window.onafterprint=done;setTimeout(done,180000);window.print();"
    style="width:100%;padding:14px;background:#1a56a0;color:white;border:none;
    border-radius:8px;font-size:15px;font-weight:bold;cursor:pointer;
    margin-bottom:10px;display:block;text-align:center;">
    🖨️ สั่งพิมพ์เลย ({len(ordered)} ภาพ)
  </button>
  <div style="font-size:11px;color:#a0aec0;line-height:1.9;">
    <p>① กดปุ่มด้านบน → กล่องเลือกเครื่องพิมพ์เปิดขึ้นทันที</p>
    <p>② เลือกเครื่องพิมพ์ → กด <b style="color:#e2e8f0;">พิมพ์</b></p>
    <p>③ ทีละ 1 ภาพต่อหน้า จัดให้เต็มพื้นที่กระดาษ</p>
  </div>
</div>"""


def on_history_action(action_json, images_state, order_state, auto_delete):
    sessions = _load_sessions()
    history_html = _render_history_html(sessions)
    if not action_json:
        return gr.update(), history_html, gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), gr.update()
    try:
        payload = json.loads(action_json)
    except Exception:
        return gr.update(), history_html, gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), gr.update()

    action = payload.get("action")

    if action == "save":
        settings = payload.get("settings", {})
        msg = _save_session(images_state or {}, order_state or [], settings, bool(auto_delete))
        sessions = _load_sessions()
        status = f"<p style='color:#{'f6ad55' if '⚠️' in msg else ('c53030' if '❌' in msg else '68d391')};font-size:13px;'>{msg}</p>"
        return status, _render_history_html(sessions), gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), gr.update()

    elif action == "restore":
        sid = int(payload.get("id", 0))
        new_images, new_order, msg = _restore_session(sid)
        if new_images is None:
            status = f"<p style='color:#c53030;font-size:13px;'>{msg}</p>"
            return status, history_html, gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), gr.update()
        status = f"<p style='color:#68d391;font-size:13px;'>{msg}</p>"
        return (status, history_html,
                new_images, new_order,
                _render_sortable_html(new_order),
                _render_sortable_gallery_html(new_images, new_order),
                gr.update(choices=new_order, value=new_order[0] if new_order else None),
                _PRINT_STALE_HTML)

    elif action == "delete":
        sid = int(payload.get("id", 0))
        msg = _delete_session(sid)
        sessions = _load_sessions()
        status = f"<p style='color:#{'c53030' if '❌' in msg else '68d391'};font-size:13px;'>{msg}</p>"
        return status, _render_history_html(sessions), gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), gr.update()

    elif action == "delete_all":
        msg = _delete_all_sessions()
        sessions = _load_sessions()
        status = f"<p style='color:#{'c53030' if '❌' in msg else '68d391'};font-size:13px;'>{msg}</p>"
        return status, _render_history_html(sessions), gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), gr.update()

    elif action == "refresh":
        sessions = _load_sessions()
        return gr.update(), _render_history_html(sessions), gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), gr.update()

    return gr.update(), history_html, gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), gr.update()


# ── Gradio UI ─────────────────────────────────────────────────────────────────
CSS = """
footer { display: none !important; }
"""

with gr.Blocks(title="🖼️ รวมภาพ | Image Merger", css=CSS, theme=gr.themes.Soft()) as demo:
    images_state = gr.State({})
    order_state = gr.State([])

    gr.Markdown(
        "# 🖼️ รวมภาพหลายไฟล์\n"
        "อัปโหลดภาพหลายไฟล์ เรียงลำดับ แล้วรวมเป็น **PDF**, **ภาพเดียว** หรือ **ZIP**"
    )

    with gr.Row(equal_height=False):
        # ── Left panel ────────────────────────────────────────────────────────
        with gr.Column(scale=1, min_width=300):
            gr.Markdown("### 📁 อัปโหลดภาพ")
            file_input = gr.File(
                file_count="multiple",
                file_types=["image"],
                label="เลือกไฟล์ (.jpg .png .bmp .tiff .webp ...)",
                elem_id="file_upload_area",
            )
            btn_clear = gr.Button("🗑️ ล้างทั้งหมด", size="sm")

            gr.Markdown("### 📋 ลำดับภาพ (ลากเพื่อเปลี่ยนลำดับ)")
            sort_order_input = gr.Textbox(visible=False, elem_id="sort_order_input", label="sort")
            order_html = gr.HTML(_render_sortable_html([]))
            select_img = gr.Dropdown(label="หรือเลือกภาพแล้วกด ↑ ↓", choices=[], interactive=True)
            with gr.Row():
                btn_up = gr.Button("↑ ขึ้น", size="sm")
                btn_down = gr.Button("↓ ลง", size="sm")
                btn_remove = gr.Button("🗑️ ลบ", size="sm", variant="stop")

            gr.Markdown("---")

            with gr.Accordion("🔧 ปรับคุณภาพภาพ", open=True):
                sharpen = gr.Slider(0.5, 3.0, 1.0, step=0.1, label="ความคมชัด (Sharpness)")
                contrast_sl = gr.Slider(0.5, 2.0, 1.0, step=0.1, label="ความเปรียบต่าง (Contrast)")
                use_unsharp = gr.Checkbox(label="🔬 Unsharp Mask", value=False)
                with gr.Group(visible=False) as unsharp_group:
                    unsharp_radius = gr.Slider(0.5, 5.0, 2.0, step=0.5, label="Radius")
                    unsharp_pct = gr.Slider(50, 400, 150, step=10, label="Percent")
                    unsharp_thresh = gr.Slider(0, 10, 3, step=1, label="Threshold")

            with gr.Accordion("⚙️ ตั้งค่า Output", open=True):
                output_format = gr.Dropdown(
                    ["PDF", "ต่อภาพแนวตั้ง (PNG)", "ต่อภาพแนวนอน (PNG)", "ZIP"],
                    value="PDF", label="รูปแบบไฟล์",
                )
                output_name = gr.Textbox(value="merged_images", label="ชื่อไฟล์ (ไม่ต้องใส่นามสกุล)")

                with gr.Group() as pdf_settings:
                    pdf_quality = gr.Slider(50, 100, 85, step=1, label="คุณภาพภาพ PDF")
                    pdf_page_size = gr.Radio(
                        ["ตามขนาดภาพ", "A4 แนวตั้ง", "A4 แนวนอน"],
                        value="ตามขนาดภาพ", label="ขนาดหน้า",
                    )

                with gr.Group(visible=False) as img_settings:
                    max_dim = gr.Number(value=0, label="จำกัดความกว้าง/สูงสูงสุด (px) — 0 = ไม่จำกัด")
                    compress_level = gr.Slider(1, 9, 3, step=1, label="ระดับ Compression (PNG)")
                    bg_color = gr.ColorPicker(value="#FFFFFF", label="สีพื้นหลัง")

                with gr.Group(visible=False) as zip_settings:
                    zip_quality = gr.Slider(50, 100, 90, step=1, label="คุณภาพ JPEG ใน ZIP")

            with gr.Accordion("🖨️ ตั้งค่าการพิมพ์", open=False):
                print_paper = gr.Dropdown(["A4", "A3", "Letter"], value="A4", label="ขนาดกระดาษ")
                print_orient = gr.Radio(["แนวตั้ง", "แนวนอน"], value="แนวตั้ง", label="การวางกระดาษ")
                print_quality = gr.Slider(60, 100, 90, step=1, label="คุณภาพภาพในการพิมพ์")

        # ── Right panel ───────────────────────────────────────────────────────
        with gr.Column(scale=2):
            gr.Markdown("### 🔍 Preview (ลากภาพเพื่อเปลี่ยนลำดับได้เลย)")
            gallery = gr.HTML(_render_sortable_gallery_html({}, []))

            gr.Markdown("---")

            btn_generate = gr.Button("🔄 สร้างไฟล์", variant="primary", size="lg")
            status_html = gr.HTML()
            preview_result = gr.HTML()

            gr.Markdown("---")
            gr.Markdown("### 🖨️ สั่งพิมพ์")
            btn_print_prep = gr.Button("⚙️ เตรียมปุ่มพิมพ์", size="sm")
            print_html = gr.HTML("<p style='color:#888;'>กดปุ่มด้านบนเพื่อเตรียมพิมพ์</p>")

            gr.Markdown("---")
            with gr.Accordion("📚 ประวัติ (Sessions)", open=False):
                with gr.Row():
                    btn_save_session = gr.Button("💾 บันทึก Session ปัจจุบัน", size="sm", variant="primary")
                    btn_refresh_history = gr.Button("🔄 รีเฟรช", size="sm")
                    btn_delete_all_sessions = gr.Button("🗑️ ลบทั้งหมด", size="sm", variant="stop")
                auto_delete_cb = gr.Checkbox(
                    label="ลบประวัติเก่าสุดอัตโนมัติเมื่อเต็ม (ไม่ถาม)",
                    value=False,
                )
                history_action_input = gr.Textbox(
                    visible=False, elem_id="history_action_input", label="history_action"
                )
                history_status = gr.HTML()
                history_html_out = gr.HTML(_render_history_html([]))

    # ── Event wiring ──────────────────────────────────────────────────────────
    use_unsharp.change(lambda v: gr.update(visible=v), use_unsharp, unsharp_group)

    def _toggle_format(fmt):
        return (
            gr.update(visible=fmt == "PDF"),
            gr.update(visible="ต่อภาพ" in fmt),
            gr.update(visible=fmt == "ZIP"),
        )
    output_format.change(_toggle_format, output_format, [pdf_settings, img_settings, zip_settings])

    _img_change_outs = [images_state, order_state, order_html, gallery, select_img, print_html]
    file_input.change(on_upload, [file_input, images_state, order_state], _img_change_outs)

    sort_order_input.change(
        on_sort_change,
        [sort_order_input, images_state, order_state, file_input],
        [images_state, order_state, order_html, gallery, select_img, print_html, file_input],
    )

    _move_outs = [order_state, order_html, gallery, select_img, print_html]
    btn_up.click(move_up, [select_img, images_state, order_state], _move_outs)
    btn_down.click(move_down, [select_img, images_state, order_state], _move_outs)
    btn_remove.click(
        remove_image, [select_img, images_state, order_state],
        [images_state, order_state, order_html, gallery, select_img, print_html],
    )
    btn_clear.click(
        clear_all, [],
        [images_state, order_state, order_html, gallery, select_img, status_html, preview_result, print_html, file_input],
    )

    btn_generate.click(
        generate,
        [
            images_state, order_state,
            output_format, output_name,
            sharpen, contrast_sl, use_unsharp,
            unsharp_radius, unsharp_pct, unsharp_thresh,
            pdf_quality, pdf_page_size,
            max_dim, compress_level, bg_color,
            zip_quality,
        ],
        [status_html, preview_result],
    )

    btn_print_prep.click(
        make_print_html,
        [images_state, order_state, print_paper, print_orient, print_quality],
        print_html,
    )

    # History wiring
    _hist_outs = [history_status, history_html_out,
                  images_state, order_state, order_html, gallery, select_img, print_html]

    def _save_click(images_state, order_state, auto_delete):
        payload = json.dumps({"action": "save", "settings": {}})
        return on_history_action(payload, images_state, order_state, auto_delete)

    def _refresh_click(images_state, order_state, auto_delete):
        payload = json.dumps({"action": "refresh"})
        return on_history_action(payload, images_state, order_state, auto_delete)

    def _delete_all_click(images_state, order_state, auto_delete):
        payload = json.dumps({"action": "delete_all"})
        return on_history_action(payload, images_state, order_state, auto_delete)

    btn_save_session.click(_save_click, [images_state, order_state, auto_delete_cb], _hist_outs)
    btn_refresh_history.click(_refresh_click, [images_state, order_state, auto_delete_cb], _hist_outs)
    btn_delete_all_sessions.click(_delete_all_click, [images_state, order_state, auto_delete_cb], _hist_outs)
    history_action_input.change(
        on_history_action,
        [history_action_input, images_state, order_state, auto_delete_cb],
        _hist_outs,
    )


if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=7860)
