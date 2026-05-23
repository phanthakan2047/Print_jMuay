import io
import zipfile
from pathlib import Path

import streamlit as st
from PIL import Image

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
    [data-testid="stFileUploader"] { border: 2px dashed #aaa; border-radius: 8px; padding: 8px; }
</style>
""", unsafe_allow_html=True)

st.title("🖼️ รวมภาพหลายไฟล์")
st.markdown("อัปโหลดภาพหลายไฟล์ เรียงลำดับ แล้วรวมเป็น **PDF**, **ภาพเดียว** หรือ **ZIP**")
st.divider()

# ─── Sidebar: Settings ────────────────────────────────────────────────────────
with st.sidebar:
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
        compress_level = st.slider(
            "ระดับ Compression (PNG)",
            1, 9, 3,
            help="1 = เร็ว/ไฟล์ใหญ่  |  9 = ช้า/ไฟล์เล็ก",
        )
        bg_color = st.color_picker("สีพื้นหลัง", "#FFFFFF")

    elif output_format == "ZIP":
        st.subheader("📦 ตั้งค่า ZIP")
        zip_quality = st.slider("คุณภาพ JPEG (สำหรับ .jpg/.jpeg)", 50, 100, 90)

    if not HAS_SORTABLES:
        st.divider()
        st.info(
            "💡 ติดตั้ง `streamlit-sortables` เพื่อเปิดใช้ Drag & Drop:\n\n"
            "```\npip install streamlit-sortables\n```"
        )


# ─── Upload ───────────────────────────────────────────────────────────────────
col_left, col_right = st.columns([1, 2], gap="large")

with col_left:
    st.subheader("📁 อัปโหลดภาพ")
    uploaded_files = st.file_uploader(
        "เลือกไฟล์ภาพ",
        accept_multiple_files=True,
        type=["jpg", "jpeg", "png", "bmp", "tiff", "tif", "webp"],
        label_visibility="collapsed",
    )

if not uploaded_files:
    st.info("👆 กรุณาอัปโหลดภาพอย่างน้อย 1 ไฟล์เพื่อเริ่มต้น")
    st.stop()

# ─── Load images ──────────────────────────────────────────────────────────────
images_dict: dict[str, Image.Image] = {}
for f in uploaded_files:
    try:
        img = Image.open(io.BytesIO(f.getvalue()))
        img.load()
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

# ─── Reorder ──────────────────────────────────────────────────────────────────
with col_left:
    st.subheader("📋 ลำดับภาพ")

    if HAS_SORTABLES:
        st.caption("↕️ ลากเพื่อเปลี่ยนลำดับ")
        labeled = [f"{i+1}. {name}" for i, name in enumerate(st.session_state.image_order)]
        sorted_labeled = sort_items(labeled, direction="vertical", key="img_sorter")
        st.session_state.image_order = [item.split(". ", 1)[1] for item in sorted_labeled]
    else:
        st.caption("ใช้ปุ่ม ↑ ↓ เพื่อเปลี่ยนลำดับ")
        for i, name in enumerate(st.session_state.image_order):
            r = st.columns([4, 1, 1])
            r[0].markdown(f"**{i+1}.** {name}")
            if i > 0 and r[1].button("↑", key=f"up_{i}"):
                o = st.session_state.image_order
                o[i], o[i - 1] = o[i - 1], o[i]
                st.rerun()
            if i < len(st.session_state.image_order) - 1 and r[2].button("↓", key=f"down_{i}"):
                o = st.session_state.image_order
                o[i], o[i + 1] = o[i + 1], o[i]
                st.rerun()

ordered_images = [
    (name, images_dict[name])
    for name in st.session_state.image_order
    if name in images_dict
]

# ─── Preview ──────────────────────────────────────────────────────────────────
with col_right:
    st.subheader(f"🔍 Preview ({len(ordered_images)} ภาพ)")
    n_cols = min(4, len(ordered_images))
    rows = [ordered_images[i:i + n_cols] for i in range(0, len(ordered_images), n_cols)]
    for row in rows:
        cols = st.columns(n_cols)
        for j, (name, img) in enumerate(row):
            with cols[j]:
                thumb = img.copy()
                thumb.thumbnail((300, 300), Image.LANCZOS)
                idx = st.session_state.image_order.index(name) + 1
                st.image(thumb, use_column_width=True)
                st.caption(f"**{idx}.** {name}  \n`{img.width}×{img.height} px`")


# ─── Generate & Download ──────────────────────────────────────────────────────
st.divider()
st.subheader("💾 สร้างและดาวน์โหลด")

mc = st.columns(4)
mc[0].metric("จำนวนภาพ", len(ordered_images))
mc[1].metric("รูปแบบ", output_format.split(" ")[0])
mc[2].metric("ชื่อไฟล์", output_name)
mc[3].metric("Drag & Drop", "✅ พร้อม" if HAS_SORTABLES else "❌ ไม่มี")


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def prep_rgb(img: Image.Image, bg_hex: str = "#FFFFFF") -> Image.Image:
    if img.mode == "RGBA":
        bg = Image.new("RGB", img.size, _hex_to_rgb(bg_hex))
        bg.paste(img, mask=img.split()[3])
        return bg
    return img.convert("RGB") if img.mode != "RGB" else img


if st.button("🔄 สร้างไฟล์", type="primary", use_container_width=True):
    imgs = [img for _, img in ordered_images]
    names = [name for name, _ in ordered_images]

    with st.spinner("⏳ กำลังสร้างไฟล์..."):

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
                data=buf,
                file_name=f"{output_name}.pdf",
                mime="application/pdf",
                use_container_width=True,
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
                data=buf,
                file_name=f"{output_name}.png",
                mime="image/png",
                use_container_width=True,
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
                data=buf,
                file_name=f"{output_name}.png",
                mime="image/png",
                use_container_width=True,
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
                data=buf,
                file_name=f"{output_name}.zip",
                mime="application/zip",
                use_container_width=True,
            )
