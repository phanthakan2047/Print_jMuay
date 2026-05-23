import base64
import io
import zipfile
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components  # type: ignore[import-untyped]
from PIL import Image, ImageEnhance, ImageFilter

try:
    from streamlit_sortables import sort_items
    HAS_SORTABLES = True
except ImportError:
    HAS_SORTABLES = False

st.set_page_config(
    page_title="รวมภาพ | Image Merger",
    page_icon="🖼️",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
    [data-testid="stMetricValue"] { font-size: 1.1rem; }
    .order-badge {
        display: inline-flex; align-items: center; justify-content: center;
        background: #1f4e79; color: white; border-radius: 50%;
        width: 36px; height: 36px; font-weight: bold; font-size: 16px;
        margin-bottom: 6px; box-shadow: 0 2px 4px rgba(0,0,0,0.25);
    }
    .img-meta { font-size: 11px; color: #888; text-align: center; }
    .order-row {
        display: flex; align-items: center; gap: 10px;
        background: #f8f9fa; border-radius: 8px;
        padding: 6px 10px; margin-bottom: 4px;
        border-left: 4px solid #1f4e79;
    }
    .order-num {
        background: #1f4e79; color: white; border-radius: 50%;
        min-width: 26px; height: 26px; display: inline-flex;
        align-items: center; justify-content: center;
        font-weight: bold; font-size: 13px;
    }
    .order-name { font-size: 13px; color: #333; flex: 1; word-break: break-all; }
</style>
""", unsafe_allow_html=True)

st.title("🖼️ รวมภาพหลายไฟล์")
st.markdown("อัปโหลดภาพหลายไฟล์ เรียงลำดับ แล้วรวมเป็น **PDF**, **ภาพเดียว** หรือ **ZIP**")
st.divider()

# ─── Helper functions ─────────────────────────────────────────────────────────

def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def prep_rgb(img: Image.Image, bg_hex: str = "#FFFFFF") -> Image.Image:
    if img.mode == "RGBA":
        bg = Image.new("RGB", img.size, _hex_to_rgb(bg_hex))
        bg.paste(img, mask=img.split()[3])
        return bg
    return img.convert("RGB") if img.mode != "RGB" else img


def apply_enhancements(img: Image.Image, sharpness: float, contrast: float,
                        use_unsharp: bool, unsharp_radius: float,
                        unsharp_pct: int, unsharp_thresh: int) -> Image.Image:
    """Apply sharpness and contrast enhancements to an image."""
    if contrast != 1.0:
        img = ImageEnhance.Contrast(img).enhance(contrast)
    if use_unsharp:
        img = img.filter(ImageFilter.UnsharpMask(
            radius=unsharp_radius, percent=unsharp_pct, threshold=unsharp_thresh
        ))
    elif sharpness != 1.0:
        img = ImageEnhance.Sharpness(img).enhance(sharpness)
    return img


def show_print_component(ordered_images: list, paper: str, orient: str, quality: int) -> None:
    """Render a print button component that opens a new window and triggers print."""
    # Determine page dimensions (px at 150 dpi)
    sizes = {"A4": (1240, 1754), "A3": (1754, 2481), "Letter": (1275, 1650)}
    pw, ph = sizes.get(paper, (1240, 1754))
    if orient == "แนวนอน":
        pw, ph = ph, pw

    img_tags = []
    for i, (_, img) in enumerate(ordered_images):
        rgb = prep_rgb(img)
        rgb.thumbnail((pw, ph), Image.LANCZOS)
        buf = io.BytesIO()
        rgb.save(buf, format="JPEG", quality=quality)
        b64 = base64.b64encode(buf.getvalue()).decode()
        pb = "" if i == len(ordered_images) - 1 else "page-break-after:always;"
        img_tags.append(
            f'<div style="{pb}text-align:center;padding:4mm;">'
            f'<img src="data:image/jpeg;base64,{b64}" '
            f'style="max-width:100%;max-height:255mm;object-fit:contain;" /></div>'
        )

    imgs_html = "".join(img_tags)
    # Escape for JS template literal (backtick and ${)
    imgs_escaped = imgs_html.replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${")

    orient_css = "portrait" if orient == "แนวตั้ง" else "landscape"
    paper_css = paper

    component_html = f"""
<html><head><meta charset="utf-8">
<style>
  body {{ margin: 0; padding: 8px; font-family: sans-serif; background: #f0f2f6; }}
  .btn {{
    width: 100%; padding: 13px; background: #1f4e79; color: white;
    border: none; border-radius: 8px; font-size: 15px; font-weight: bold;
    cursor: pointer; letter-spacing: 0.5px;
  }}
  .btn:hover {{ background: #2e6da4; }}
  .status {{ text-align: center; margin-top: 6px; font-size: 12px; color: #555; min-height: 16px; }}
</style></head>
<body>
<button class="btn" onclick="doPrint()">🖨️ สั่งพิมพ์เลย ({len(ordered_images)} ภาพ)</button>
<div class="status" id="st"></div>
<script>
function doPrint() {{
  const html = `<!DOCTYPE html><html><head><meta charset="utf-8">
<style>
  body{{margin:0;padding:0;background:white;}}
  @page{{size:{paper_css} {orient_css};margin:10mm;}}
  @media print{{.no-print{{display:none;}}}}
</style></head><body>{imgs_escaped}</body></html>`;
  const blob = new Blob([html], {{type:'text/html;charset=utf-8'}});
  const url = URL.createObjectURL(blob);
  const w = window.open(url, '_blank');
  if (!w) {{
    document.getElementById('st').innerHTML = '❌ กรุณาอนุญาต Pop-up ใน browser ก่อน';
  }} else {{
    document.getElementById('st').innerHTML = '✅ เปิดหน้าต่างพิมพ์แล้ว — กด Ctrl+P หรือปุ่ม Print ในเบราว์เซอร์';
    setTimeout(function(){{ URL.revokeObjectURL(url); }}, 60000);
  }}
}}
</script>
</body></html>"""

    components.html(component_html, height=90)


# ─── Sidebar ──────────────────────────────────────────────────────────────────
with st.sidebar:

    # ── Image Enhancement ────────────────────────────────────────────────────
    st.header("🔧 ปรับคุณภาพภาพ")
    st.caption("ตั้งค่าที่นี่จะถูกใช้กับทุกภาพเมื่อสร้างไฟล์")

    sharpen_level = st.slider(
        "ความคมชัด (Sharpness)",
        min_value=0.5, max_value=3.0, value=1.0, step=0.1,
        help="1.0 = ต้นฉบับ  |  > 1.0 = คมชัดขึ้น  |  < 1.0 = นุ่มลง",
    )
    contrast_level = st.slider(
        "ความเปรียบต่าง (Contrast)",
        min_value=0.5, max_value=2.0, value=1.0, step=0.1,
        help="1.0 = ต้นฉบับ  |  > 1.0 = เข้มขึ้น",
    )

    use_unsharp = st.checkbox(
        "🔬 Unsharp Mask (คมชัดระดับสูง)",
        help="เทคนิคเพิ่มความคมชัดที่นิยมใช้ในการพิมพ์ภาพคุณภาพสูง",
    )
    if use_unsharp:
        unsharp_radius = st.slider("Radius", 0.5, 5.0, 2.0, 0.5,
                                    help="รัศมีการตรวจจับขอบ")
        unsharp_pct = st.slider("Percent (ความเข้ม)", 50, 400, 150, 10,
                                 help="150 = กลาง | 300 = แรง")
        unsharp_thresh = st.slider("Threshold", 0, 10, 3,
                                    help="ค่าต่ำ = คมชัดทุกพื้นที่ | ค่าสูง = เฉพาะขอบชัด")
    else:
        unsharp_radius, unsharp_pct, unsharp_thresh = 2.0, 150, 3

    st.divider()

    # ── Output Settings ──────────────────────────────────────────────────────
    st.header("⚙️ ตั้งค่า Output")

    output_format = st.selectbox(
        "รูปแบบไฟล์",
        ["PDF", "ต่อภาพแนวตั้ง (PNG)", "ต่อภาพแนวนอน (PNG)", "ZIP"],
        index=0,
    )
    output_name = st.text_input(
        "ชื่อไฟล์ Output",
        value="merged_images",
        help="ไม่ต้องใส่นามสกุลไฟล์",
    )

    st.divider()

    if output_format == "PDF":
        st.subheader("📄 ตั้งค่า PDF")
        pdf_quality = st.slider("คุณภาพภาพ", 50, 100, 85)
        pdf_page_size = st.radio(
            "ขนาดหน้า",
            ["ตามขนาดภาพ", "A4 แนวตั้ง", "A4 แนวนอน"],
            index=0,
        )

    elif "ต่อภาพ" in output_format:
        st.subheader("🖼️ ตั้งค่าภาพ")
        max_dim = st.number_input(
            "จำกัดความกว้าง/สูงสูงสุด (px)",
            min_value=0, max_value=20000, value=0, step=100,
            help="0 = ไม่จำกัด",
        )
        compress_level = st.slider("ระดับ Compression (PNG)", 1, 9, 3,
                                    help="1 = เร็ว/ใหญ่  |  9 = ช้า/เล็ก")
        bg_color = st.color_picker("สีพื้นหลัง", "#FFFFFF")

    elif output_format == "ZIP":
        st.subheader("📦 ตั้งค่า ZIP")
        zip_quality = st.slider("คุณภาพ JPEG (สำหรับ .jpg/.jpeg)", 50, 100, 90)

    st.divider()

    # ── Print Settings ───────────────────────────────────────────────────────
    st.header("🖨️ ตั้งค่าการพิมพ์")
    print_paper = st.selectbox("ขนาดกระดาษ", ["A4", "A3", "Letter"])
    print_orient = st.radio("การวางกระดาษ", ["แนวตั้ง", "แนวนอน"], horizontal=True)
    print_quality = st.slider("คุณภาพภาพในการพิมพ์", 60, 100, 90)

    if not HAS_SORTABLES:
        st.divider()
        st.info(
            "💡 ติดตั้ง `streamlit-sortables` เพื่อ Drag & Drop:\n\n"
            "```\npip install streamlit-sortables\n```"
        )


# ─── Upload ───────────────────────────────────────────────────────────────────
SUPPORTED_TYPES = [
    "jpg", "jpeg", "png", "bmp", "tiff", "tif", "webp",
    "gif", "ico", "jp2", "ppm", "pgm", "pbm", "pcx",
]

col_left, col_right = st.columns([1, 2], gap="large")

if "uploader_key" not in st.session_state:
    st.session_state.uploader_key = 0

with col_left:
    st.subheader("📁 อัปโหลดภาพ")
    st.caption(f"รองรับ: {', '.join(f'.{t}' for t in SUPPORTED_TYPES)}")
    uploaded_files = st.file_uploader(
        "เลือกไฟล์ภาพ",
        accept_multiple_files=True,
        type=SUPPORTED_TYPES,
        label_visibility="collapsed",
        key=f"uploader_{st.session_state.uploader_key}",
    )
    if uploaded_files and st.button("🗑️ ลบภาพทั้งหมด", use_container_width=True):
        st.session_state.uploader_key += 1
        st.session_state.image_order = []
        st.rerun()

if not uploaded_files:
    st.info("👆 กรุณาอัปโหลดภาพอย่างน้อย 1 ไฟล์เพื่อเริ่มต้น")
    st.stop()

# ─── Load images ──────────────────────────────────────────────────────────────
images_dict: dict[str, Image.Image] = {}
for f in uploaded_files:
    try:
        img = Image.open(io.BytesIO(f.getvalue()))
        img.load()
        # Normalize palette/special modes to RGB(A)
        if img.mode == "P":
            img = img.convert("RGBA" if "transparency" in img.info else "RGB")
        images_dict[f.name] = img
    except Exception as exc:
        st.warning(f"⚠️ โหลด {f.name} ไม่ได้: {exc}")

if not images_dict:
    st.error("ไม่สามารถโหลดภาพได้เลย กรุณาตรวจสอบไฟล์")
    st.stop()

# ─── Sync order state ─────────────────────────────────────────────────────────
if "image_order" not in st.session_state:
    st.session_state.image_order = list(images_dict.keys())
else:
    kept = [n for n in st.session_state.image_order if n in images_dict]
    added = [n for n in images_dict if n not in set(kept)]
    st.session_state.image_order = kept + added

# ─── Reorder (left column) ────────────────────────────────────────────────────
with col_left:
    st.subheader("📋 ลำดับภาพ")

    if HAS_SORTABLES:
        st.caption("↕️ ลากเพื่อเปลี่ยนลำดับ")
        labeled = [f"{i+1}. {name}" for i, name in enumerate(st.session_state.image_order)]
        # Key changes when ORDER changes → forces re-init so numbers always match position
        order_sig = abs(hash("_".join(st.session_state.image_order)))
        sorted_labeled = sort_items(labeled, direction="vertical", key=f"img_sorter_{order_sig}")
        st.session_state.image_order = [item.split(". ", 1)[1] for item in sorted_labeled]
    else:
        st.caption("ใช้ปุ่ม ↑ ↓ เพื่อเปลี่ยนลำดับ")
        for i, name in enumerate(st.session_state.image_order):
            # Styled row with position badge
            st.markdown(
                f'<div class="order-row">'
                f'<span class="order-num">{i+1}</span>'
                f'<span class="order-name">{name}</span>'
                f'</div>',
                unsafe_allow_html=True,
            )
            btn_cols = st.columns([1, 1, 6])
            if i > 0 and btn_cols[0].button("↑", key=f"up_{i}"):
                o = st.session_state.image_order
                o[i], o[i - 1] = o[i - 1], o[i]
                st.rerun()
            if i < len(st.session_state.image_order) - 1 and btn_cols[1].button("↓", key=f"down_{i}"):
                o = st.session_state.image_order
                o[i], o[i + 1] = o[i + 1], o[i]
                st.rerun()

ordered_images = [
    (name, images_dict[name])
    for name in st.session_state.image_order
    if name in images_dict
]

# ─── Preview with position badges (right column) ──────────────────────────────
with col_right:
    st.subheader(f"🔍 Preview ({len(ordered_images)} ภาพ)")
    n_cols = min(4, len(ordered_images))
    rows = [ordered_images[i:i + n_cols] for i in range(0, len(ordered_images), n_cols)]
    total_imgs = len(ordered_images)

    for row_idx, row in enumerate(rows):
        cols = st.columns(n_cols)
        for j, (name, img) in enumerate(row):
            global_idx = row_idx * n_cols + j
            with cols[j]:
                idx = global_idx + 1
                # Colored position badge
                st.markdown(
                    f'<div style="text-align:center;">'
                    f'<div class="order-badge">{idx}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
                thumb = img.copy()
                thumb.thumbnail((300, 300), Image.LANCZOS)
                st.image(thumb, use_column_width=True)
                st.markdown(
                    f'<div class="img-meta">{name}<br>{img.width}×{img.height} px | {img.mode}</div>',
                    unsafe_allow_html=True,
                )
                # Move left / right buttons
                bl, br = st.columns(2)
                if bl.button("◀", key=f"mv_l_{global_idx}",
                             help="ย้ายไปก่อนหน้า",
                             use_container_width=True,
                             disabled=(global_idx == 0)):
                    o = st.session_state.image_order
                    o[global_idx], o[global_idx - 1] = o[global_idx - 1], o[global_idx]
                    st.rerun()
                if br.button("▶", key=f"mv_r_{global_idx}",
                             help="ย้ายไปถัดไป",
                             use_container_width=True,
                             disabled=(global_idx == total_imgs - 1)):
                    o = st.session_state.image_order
                    o[global_idx], o[global_idx + 1] = o[global_idx + 1], o[global_idx]
                    st.rerun()


# ─── Metrics & Generate ───────────────────────────────────────────────────────
st.divider()
st.subheader("💾 สร้างและดาวน์โหลด")

mc = st.columns(4)
mc[0].metric("จำนวนภาพ", len(ordered_images))
mc[1].metric("รูปแบบ", output_format.split(" ")[0])
mc[2].metric("ชื่อไฟล์", output_name)
enh_label = []
if sharpen_level != 1.0:
    enh_label.append(f"คมชัด ×{sharpen_level:.1f}")
if contrast_level != 1.0:
    enh_label.append(f"contrast ×{contrast_level:.1f}")
if use_unsharp:
    enh_label.append("Unsharp")
mc[3].metric("ปรับภาพ", ", ".join(enh_label) if enh_label else "ไม่มี")

if st.button("🔄 สร้างไฟล์", type="primary", use_container_width=True):
    imgs_raw = [img for _, img in ordered_images]
    names = [name for name, _ in ordered_images]

    with st.spinner("⏳ กำลังสร้างไฟล์..."):

        # Apply enhancements to all images
        imgs = []
        for img in imgs_raw:
            enhanced = apply_enhancements(
                img.copy(), sharpen_level, contrast_level,
                use_unsharp, unsharp_radius, unsharp_pct, unsharp_thresh,
            )
            imgs.append(enhanced)

        # ── PDF ──────────────────────────────────────────────────────────────
        if output_format == "PDF":
            pdf_imgs: list[Image.Image] = []

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

            buf = io.BytesIO()
            pdf_imgs[0].save(
                buf, format="PDF",
                save_all=True,
                append_images=pdf_imgs[1:],
                quality=pdf_quality,
            )
            buf.seek(0)
            st.success(f"✅ สร้าง PDF สำเร็จ! ({len(pdf_imgs)} หน้า)")
            st.download_button(
                label=f"📥 ดาวน์โหลด {output_name}.pdf",
                data=buf, file_name=f"{output_name}.pdf",
                mime="application/pdf", use_container_width=True,
            )

        # ── ต่อภาพแนวตั้ง ────────────────────────────────────────────────────
        elif "แนวตั้ง" in output_format:
            rgb_imgs = [prep_rgb(img, bg_color) for img in imgs]

            if max_dim > 0:
                rgb_imgs = [
                    img.resize((max_dim, int(img.height * max_dim / img.width)), Image.LANCZOS)
                    if img.width > max_dim else img
                    for img in rgb_imgs
                ]

            max_w = max(img.width for img in rgb_imgs)
            total_h = sum(img.height for img in rgb_imgs)
            canvas = Image.new("RGB", (max_w, total_h), _hex_to_rgb(bg_color))
            y = 0
            for img in rgb_imgs:
                canvas.paste(img, ((max_w - img.width) // 2, y))
                y += img.height

            buf = io.BytesIO()
            canvas.save(buf, format="PNG", compress_level=compress_level)
            buf.seek(0)
            st.success(f"✅ ต่อภาพแนวตั้งสำเร็จ! ขนาด: {canvas.width}×{canvas.height} px")
            st.image(canvas, caption="ผลลัพธ์", use_column_width=True)
            st.download_button(
                label=f"📥 ดาวน์โหลด {output_name}.png",
                data=buf, file_name=f"{output_name}.png",
                mime="image/png", use_container_width=True,
            )

        # ── ต่อภาพแนวนอน ────────────────────────────────────────────────────
        elif "แนวนอน" in output_format:
            rgb_imgs = [prep_rgb(img, bg_color) for img in imgs]

            if max_dim > 0:
                rgb_imgs = [
                    img.resize((int(img.width * max_dim / img.height), max_dim), Image.LANCZOS)
                    if img.height > max_dim else img
                    for img in rgb_imgs
                ]

            total_w = sum(img.width for img in rgb_imgs)
            max_h = max(img.height for img in rgb_imgs)
            canvas = Image.new("RGB", (total_w, max_h), _hex_to_rgb(bg_color))
            x = 0
            for img in rgb_imgs:
                canvas.paste(img, (x, (max_h - img.height) // 2))
                x += img.width

            buf = io.BytesIO()
            canvas.save(buf, format="PNG", compress_level=compress_level)
            buf.seek(0)
            st.success(f"✅ ต่อภาพแนวนอนสำเร็จ! ขนาด: {canvas.width}×{canvas.height} px")
            st.image(canvas, caption="ผลลัพธ์", use_column_width=True)
            st.download_button(
                label=f"📥 ดาวน์โหลด {output_name}.png",
                data=buf, file_name=f"{output_name}.png",
                mime="image/png", use_container_width=True,
            )

        # ── ZIP ──────────────────────────────────────────────────────────────
        elif output_format == "ZIP":
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                for name, img in zip(names, imgs):
                    img_buf = io.BytesIO()
                    if Path(name).suffix.lower() in (".jpg", ".jpeg"):
                        img.convert("RGB").save(img_buf, format="JPEG", quality=zip_quality)
                    else:
                        img.save(img_buf, format="PNG")
                    img_buf.seek(0)
                    zf.writestr(name, img_buf.read())
            buf.seek(0)
            st.success(f"✅ บีบอัด ZIP สำเร็จ! ({len(imgs)} ไฟล์)")
            st.download_button(
                label=f"📥 ดาวน์โหลด {output_name}.zip",
                data=buf, file_name=f"{output_name}.zip",
                mime="application/zip", use_container_width=True,
            )


# ─── Print Section ────────────────────────────────────────────────────────────
st.divider()
st.subheader("🖨️ สั่งพิมพ์")

pcols = st.columns([2, 1])
with pcols[0]:
    st.markdown(
        f"กระดาษ **{print_paper}** {'แนวตั้ง' if print_orient == 'แนวตั้ง' else 'แนวนอน'}  "
        f"· คุณภาพ **{print_quality}%**  "
        f"· {len(ordered_images)} ภาพ (1 ภาพ/หน้า)"
    )
    st.caption(
        "กดปุ่มด้านล่างเพื่อเปิดหน้าต่างพิมพ์ใหม่  "
        "หาก browser บล็อก pop-up ให้กด Allow แล้วลองใหม่"
    )

with st.spinner(""):
    pass

show_print_component(ordered_images, print_paper, print_orient, print_quality)
