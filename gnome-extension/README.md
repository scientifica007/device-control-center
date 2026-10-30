# نموذج مؤشر بطارية Bluetooth — GNOME 46

تجربة اختيارية مستقلة عن نافذة Device Control Center. لم تُختبر بعد داخل GNOME Shell الفعلي على الحاسوب.

## ما ينفذه النموذج

- قراءة الأجهزة المتصلة من BlueZ عبر System D-Bus دون تنفيذ أوامر shell.
- استخدام Battery1 أولًا، ثم UPower كبديل عند مطابقته للجهاز.
- إظهار النسبة في الشريط العلوي، أو الرمز «—» إذا كانت غير متاحة.
- عرض الأجهزة في قائمة تسمح بتحديد أحدها أثناء الجلسة، وفتح تطبيق سطح المكتب عند تثبيته.
- متابعة أحداث D-Bus دون استطلاع دوري؛ لا يجمع الصوت ولا يفصل الأجهزة ولا يبدأ التشغيل تلقائيًا.

## البناء والاختبارات

يُشغّل من جذر المستودع:

    python3 packaging/build_extension.py
    node --check gnome-extension/extension.js
    node tests/test_extension.mjs

## التثبيت اليدوي التجريبي — بعد الموافقة

    gnome-extensions install --force dist/battery@scientifica007.github.io.shell-extension.zip

على Wayland، قد يلزم تسجيل الخروج والدخول مجددًا حتى يرى GNOME الامتداد المثبت حديثًا. **لا تعِد تشغيل GNOME Shell باستخدام Alt+F2 ثم r في جلسة Wayland.**

بعد عودة الجلسة:

    gnome-extensions enable battery@scientifica007.github.io
    gnome-extensions info battery@scientifica007.github.io

لإيقافه فورًا:

    gnome-extensions disable battery@scientifica007.github.io

ولإزالته:

    gnome-extensions uninstall battery@scientifica007.github.io

## حدود الثقة والقبول

نتائج Node تختبر إسقاط حالات البيانات الاصطناعية، وليست اختبار تشغيل GNOME. يجب التحقق ميدانيًا من ظهور الرقم والقائمة، والتعامل مع ظهور البطارية واختفائها، وتشغيل التطبيق، والتصرف بعد إعادة تسجيل الدخول. لا يعرض المؤشر قيمة مخزنة بوصفها حديثة، ولا يزعم معرفة وقت إبلاغ السماعة.

## التحكم من نافذة التطبيق (v0.1.14)

في قسم «مؤشر بطارية Bluetooth في الشريط العلوي»، يقرأ التطبيق حالة امتداد `battery@scientifica007.github.io` فقط، ويعرض زر «إخفاء المؤشر» أو «إظهار المؤشر» بحسب حالة GNOME. التغيير يتطلب نقرة صريحة، وهو معادل للأوامر `gnome-extensions enable/disable battery@scientifica007.github.io` من دون استخدام shell أو sudo. زر «تحديث حالة المؤشر» يعيد قراءة حالة GNOME إذا جرى تغييرها خارج التطبيق.

**لا يؤدي إخفاء المؤشر إلى إيقاف Bluetooth أو إزالة امتداد GNOME أو التأثير على رمز Bluetooth الأصلي للنظام.** ويتحقق التطبيق كذلك من كون الامتداد نشطًا بالفعل عند الإمكان؛ في حال عدم ظهوره رغم التفعيل لا يدعي نجاح العرض. لا يغير هذا التحكم طريقة عمل المؤشر المستقل عند إغلاق نافذة GTK.
