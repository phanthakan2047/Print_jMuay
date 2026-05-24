---
title: รวมภาพ Image Merger
emoji: 🖼️
colorFrom: blue
colorTo: indigo
sdk: docker
pinned: false
license: mit
---

# 🖼️ รวมภาพหลายไฟล์

อัปโหลดภาพหลายไฟล์ เรียงลำดับ แล้วรวมเป็น PDF, ภาพเดียว หรือ ZIP

## Features
- รองรับไฟล์ภาพหลายฟอร์แมต (.jpg, .png, .bmp, .tiff, .webp ...)
- เรียงลำดับภาพด้วยปุ่ม ↑ ↓ หรือลากเปลี่ยนลำดับใน Preview
- ปรับ Sharpness / Contrast / Unsharp Mask
- Export เป็น PDF, PNG (แนวตั้ง/แนวนอน), ZIP
- สั่งพิมพ์โดยตรงจาก browser พร้อม Preview ก่อนพิมพ์
- บันทึก/โหลด/ลบประวัติ sessions ผ่าน Supabase (สูงสุด 20 sessions)

## Setup (Supabase — optional)

สร้าง tables ใน Supabase ด้วย SQL นี้:

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

create table image_sessions (
  id           bigserial primary key,
  created_at   timestamptz default now(),
  image_names  jsonb,
  thumbnails   jsonb,
  image_data   jsonb,
  settings     jsonb
);
```

แล้วเพิ่ม Secrets ใน HF Spaces Settings:
- `SUPABASE_URL` — Project URL จาก Supabase Dashboard
- `SUPABASE_KEY` — anon/public key จาก Supabase Dashboard
