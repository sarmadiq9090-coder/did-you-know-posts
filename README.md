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
- `scripts/publish_reel.py` يصنع الريلز ويفحصه وينشره (`--dry-run` للمعاينة فقط بدون نشر).
- `tools/make_reel.py` تصميم الريلز والغلاف، `tools/validate_reel.py` فحص الجودة قبل النشر،
  `tools/reel_previews.py` صورة معاينة (الإطارات، المنطقة الآمنة، قص الشبكة 3:4 والصفحة الرئيسية 1:1).
- `tools/test_reels.py` + `tests/reel_cases/` سبع حالات اختبار للريلز.
- القصص: `scripts/publish_story.py` و`tools/make_story.py` و`.github/workflows/stories.yml`.
  حالياً قصة واحدة يومياً: 9:53 صباحاً «سؤال اليوم» عن منشور الظهر. جاهز للتفعيل لاحقاً: قصص المنشورات
  (أضف cron "20 10,17 * * *" في stories.yml) والريلز كقصص (REEL_STORY=1 في reels.yml). لا تُنشر قصة مرتين (state.json → stories). للتجربة بدون نشر:
  اكتب `question dry` أو `post dry` في `story_request.txt`.
- `.github/workflows/reels.yml` نشر ريلزين يومياً، و`reel-preview.yml` معاينات تلقائية في كل Pull Request يغيّر الريلز.

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
