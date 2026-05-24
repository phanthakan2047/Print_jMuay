import base64
import io
import json
import os
import tempfile
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
        f'</div>'
        for i, name in enumerate(order)
    )
    return f"""<div id="sc{uid}" style="max-height:300px;overflow-y:auto;padding:2px;">{items}</div>
<style>.sg{{opacity:.5;background:#2d5a8e!important;}}</style>
<img src="x{uid}" onerror="(function(){{
  var go=function(){{
    var c=document.getElementById('sc{uid}');
    if(!c||c._s)return;c._s=true;
    new Sortable(c,{{animation:150,draggable:'.si',ghostClass:'sg',onEnd:function(){{
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


def on_sort_change(new_order_json, images_state, order_state):
    if not new_order_json:
        return order_state, gr.update(), gr.update(), gr.update()
    try:
        new_order = json.loads(new_order_json)
        new_order = [n for n in new_order if n in (images_state or {})]
        if not new_order:
            return order_state, gr.update(), gr.update(), gr.update()
        return new_order, _render_sortable_html(new_order), _render_sortable_gallery_html(images_state, new_order), gr.update(choices=new_order)
    except Exception:
        return order_state, gr.update(), gr.update(), gr.update()


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
    for i, name in enumerate(order):
        if name not in images:
            continue
        thumb = images[name].copy()
        thumb.thumbnail((180, 180), Image.LANCZOS)
        buf = io.BytesIO()
        prep_rgb(thumb).save(buf, format="JPEG", quality=70)
        b64 = base64.b64encode(buf.getvalue()).decode()
        safe = name.replace('"', "&quot;")
        items.append(
            f'<div class="gi" data-name="{safe}" style="display:flex;flex-direction:column;'
            f'align-items:center;cursor:grab;background:#1e2a3a;border-radius:8px;padding:6px;'
            f'user-select:none;border:2px solid transparent;" '
            f'onmouseenter="this.style.borderColor=\'#63b3ed\'" '
            f'onmouseleave="this.style.borderColor=\'transparent\'">'
            f'<div style="background:#2d3e50;border-radius:4px;width:100%;text-align:center;'
            f'font-size:11px;color:#63b3ed;padding:2px 4px;margin-bottom:4px;font-weight:bold;">{i+1}</div>'
            f'<img src="data:image/jpeg;base64,{b64}" style="max-width:100%;max-height:140px;'
            f'object-fit:contain;border-radius:4px;pointer-events:none;"/>'
            f'<div style="font-size:10px;color:#a0aec0;margin-top:4px;word-break:break-all;'
            f'text-align:center;width:100%;overflow:hidden;text-overflow:ellipsis;'
            f'white-space:nowrap;" title="{safe}">{name}</div>'
            f'</div>'
        )
    items_html = "".join(items)
    return f"""<div id="gc{uid}" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));
gap:8px;max-height:460px;overflow-y:auto;padding:4px;">{items_html}</div>
<img src="gi{uid}" onerror="(function(){{
  var go=function(){{
    var c=document.getElementById('gc{uid}');
    if(!c||c._s)return;c._s=true;
    new Sortable(c,{{animation:150,draggable:'.gi',ghostClass:'sg2',onEnd:function(){{
      var it=c.querySelectorAll('.gi');
      it.forEach(function(x,i){{x.querySelector('div:first-child').textContent=i+1;}});
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
def on_upload(files, images_state, order_state):
    if not files:
        return {}, [], _render_sortable_html([]), _render_sortable_gallery_html({}, []), gr.update(choices=[], value=None)

    images = dict(images_state) if images_state else {}
    for f in files:
        path = f.name if hasattr(f, "name") else str(f)
        try:
            img = Image.open(path)
            img.load()
            if img.mode == "P":
                img = img.convert("RGBA" if "transparency" in img.info else "RGB")
            images[Path(path).name] = img
        except Exception:
            pass

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
    )


def move_up(selected, images_state, order_state):
    order = list(order_state)
    if selected and selected in order:
        i = order.index(selected)
        if i > 0:
            order[i], order[i - 1] = order[i - 1], order[i]
    return order, _render_sortable_html(order), _render_sortable_gallery_html(images_state, order), gr.update(choices=order, value=selected)


def move_down(selected, images_state, order_state):
    order = list(order_state)
    if selected and selected in order:
        i = order.index(selected)
        if i < len(order) - 1:
            order[i], order[i + 1] = order[i + 1], order[i]
    return order, _render_sortable_html(order), _render_sortable_gallery_html(images_state, order), gr.update(choices=order, value=selected)


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
    )


def clear_all():
    return {}, [], _render_sortable_html([]), _render_sortable_gallery_html({}, []), gr.update(choices=[], value=None), None, ""


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

    out_path = os.path.join(tempfile.gettempdir(), f"{fname}.{ext}")
    with open(out_path, "wb") as fout:
        fout.write(data)

    return out_path, f"<p style='color:#276749;font-weight:600;'>{msg} ({size_kb:.0f} KB)</p>"


def make_print_html(images_state, order_state, print_paper, print_orient, print_quality):
    if not images_state or not order_state:
        return "<p style='color:#888'>อัปโหลดภาพก่อน แล้วกดปุ่มเตรียมพิมพ์</p>"

    ordered = [(n, images_state[n]) for n in order_state if n in images_state]
    if not ordered:
        return "<p style='color:#888'>ไม่มีภาพ</p>"

    sizes = {"A4": (1240, 1754), "A3": (1754, 2481), "Letter": (1275, 1650)}
    pw, ph = sizes.get(print_paper, (1240, 1754))
    if print_orient == "แนวนอน":
        pw, ph = ph, pw

    img_tags = []
    for i, (_, img) in enumerate(ordered):
        rgb = prep_rgb(img)
        rgb.thumbnail((pw, ph), Image.LANCZOS)
        buf = io.BytesIO()
        rgb.save(buf, format="JPEG", quality=int(print_quality))
        b64 = base64.b64encode(buf.getvalue()).decode()
        pb = "" if i == len(ordered) - 1 else "page-break-after:always;"
        img_tags.append(
            f'<div style="{pb}text-align:center;padding:4mm;">'
            f'<img src="data:image/jpeg;base64,{b64}" '
            f'style="max-width:100%;max-height:255mm;object-fit:contain;"/></div>'
        )

    imgs_html = "".join(img_tags)
    imgs_escaped = imgs_html.replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${")
    orient_css = "portrait" if print_orient == "แนวตั้ง" else "landscape"

    return f"""
<button onclick="(function(){{
  var html='<!DOCTYPE html><html><head><meta charset=\\'utf-8\\'>'
    +'<style>body{{margin:0;padding:0;background:white;}}'
    +'@page{{size:{print_paper} {orient_css};margin:10mm;}}'
    +'@media print{{.np{{display:none;}}}}</style></head>'
    +'<body>{imgs_escaped}</body></html>';
  var blob=new Blob([html],{{type:'text/html;charset=utf-8'}});
  var url=URL.createObjectURL(blob);
  var w=window.open(url,'_blank');
  if(!w){{document.getElementById('pst').textContent='❌ กรุณาอนุญาต Pop-up ใน browser';}}
  else{{document.getElementById('pst').textContent='✅ เปิดหน้าต่างพิมพ์แล้ว — กด Ctrl+P';
    setTimeout(function(){{URL.revokeObjectURL(url);}},60000);}}
}})()"
style="width:100%;padding:12px;background:#1f4e79;color:white;border:none;
border-radius:8px;font-size:15px;font-weight:bold;cursor:pointer;margin-top:4px;">
🖨️ สั่งพิมพ์เลย ({len(ordered)} ภาพ)
</button>
<div id="pst" style="text-align:center;margin-top:6px;font-size:12px;color:#555;min-height:16px;"></div>
"""


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
            output_file = gr.File(label="📥 ดาวน์โหลดไฟล์")

            gr.Markdown("---")
            gr.Markdown("### 🖨️ สั่งพิมพ์")
            btn_print_prep = gr.Button("⚙️ เตรียมปุ่มพิมพ์", size="sm")
            print_html = gr.HTML("<p style='color:#888;'>กดปุ่มด้านบนเพื่อเตรียมพิมพ์</p>")

    # ── Event wiring ──────────────────────────────────────────────────────────
    use_unsharp.change(lambda v: gr.update(visible=v), use_unsharp, unsharp_group)

    def _toggle_format(fmt):
        return (
            gr.update(visible=fmt == "PDF"),
            gr.update(visible="ต่อภาพ" in fmt),
            gr.update(visible=fmt == "ZIP"),
        )
    output_format.change(_toggle_format, output_format, [pdf_settings, img_settings, zip_settings])

    _upload_outs = [images_state, order_state, order_html, gallery, select_img]
    file_input.change(on_upload, [file_input, images_state, order_state], _upload_outs)

    sort_order_input.change(
        on_sort_change,
        [sort_order_input, images_state, order_state],
        [order_state, order_html, gallery, select_img],
    )

    _move_outs = [order_state, order_html, gallery, select_img]
    btn_up.click(move_up, [select_img, images_state, order_state], _move_outs)
    btn_down.click(move_down, [select_img, images_state, order_state], _move_outs)
    btn_remove.click(
        remove_image, [select_img, images_state, order_state],
        [images_state, order_state, order_html, gallery, select_img],
    )
    btn_clear.click(
        clear_all, [],
        [images_state, order_state, order_html, gallery, select_img, output_file, status_html],
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
        [output_file, status_html],
    )

    btn_print_prep.click(
        make_print_html,
        [images_state, order_state, print_paper, print_orient, print_quality],
        print_html,
    )


if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=7860)
