---
title: รวมภาพ Image Merger
emoji: 🖼️
colorFrom: blue
colorTo: indigo
sdk: gradio
sdk_version: 4.44.0
app_file: app.py
pinned: false
license: mit
---

# 🖼️ รวมภาพหลายไฟล์

อัปโหลดภาพหลายไฟล์ เรียงลำดับ แล้วรวมเป็น PDF, ภาพเดียว หรือ ZIP

## Features
- รองรับไฟล์ภาพหลายฟอร์แมต (.jpg, .png, .bmp, .tiff, .webp ...)
- เรียงลำดับภาพด้วยปุ่ม ↑ ↓
- ปรับ Sharpness / Contrast / Unsharp Mask
- Export เป็น PDF, PNG (แนวตั้ง/แนวนอน), ZIP
- สั่งพิมพ์โดยตรงจาก browser

## Setup (Supabase — optional)

สร้าง table ใน Supabase ด้วย SQL นี้:

```sql
create table file_history (
  id           bigserial primary key,
  created_at   timestamptz default now(),
  output_format text,
  image_count  int,
  file_size_kb numeric(10,1),
  output_filename text,
  enhancements text
);
```

แล้วเพิ่ม Secrets ใน HF Spaces Settings:
- `SUPABASE_URL` — Project URL จาก Supabase Dashboard
- `SUPABASE_KEY` — anon/public key จาก Supabase Dashboard
