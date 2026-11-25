# هل تعلم؟ — نشر تلقائي على إنستغرام

ينشر هذا المستودع أربعة منشورات يومياً في الساعة 1:07 ظهراً و5:07 عصراً و8:07 مساءً و10:07 مساءً بتوقيت بغداد على حساب
@did.you.know.iq، عن طريق API إنستغرام الرسمي (Instagram API with Unified Login عبر graph.facebook.com).

## المحتويات
- `posts/` صور المنشورات (JPEG).
- `queue.json` ترتيب المنشورات والوصف لكل منشور.
- `state.json` المنشورات التي نُشرت (يحدّثه النظام تلقائياً).
- `scripts/publish.py` ينشر المنشور التالي.
- `scripts/refresh_token.py` يجدد رمز الوصول.
- `.github/workflows/publish.yml` الجدولة اليومية.

## الإعداد (مرة واحدة)
1. المستودع يجب أن يكون **Public** حتى يستطيع إنستغرام سحب الصور.
2. Settings ← Secrets and variables ← Actions ← New repository secret:
   - `IG_TOKEN`: رمز الوصول من تطبيق Meta (Generate access tokens).
   - `GH_PAT` (اختياري): رمز GitHub دقيق الصلاحيات (Fine-grained) على هذا المستودع فقط،
     بصلاحية Secrets: Read and write. يسمح بتجديد `IG_TOKEN` تلقائياً كل أسبوع.
     بدونه تحتاج تبديل `IG_TOKEN` يدوياً كل 60 يوماً.
3. للتجربة: تبويب Actions ← Publish daily post ← Run workflow.

## إضافة منشورات
أضف الصورة في `posts/` وأضف سطراً في `queue.json`:
```json
{"image": "posts/06_example.jpg", "caption": "الوصف والهاشتاغات"}
```
