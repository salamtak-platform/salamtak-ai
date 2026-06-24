# Salamtak / سلامتك — API Version

نسخة API من شات بوت **سلامتك** بدون Streamlit. النسخة دي معمولة عشان تتربط مع:

- Flutter mobile app
- Web page
- Node.js backend
- Database موجودة في الباك إند

## أهم تغيير في النسخة دي

- تم حذف Streamlit بالكامل من التشغيل والـ requirements.
- تم حذف واجهة تعديل البيانات ورفع الجداول من داخل التطبيق.
- تم حذف جدول الدكاترة الداخلي. جدول الدكاترة يصل من Node.js backend من قاعدة البيانات، ويمكن إرساله أول مرة في السيشن فقط؛ بعدها يحتفظ الـ AI بآخر جدول داخل ذاكرة نفس السيشن إلى أن يتم إغلاقه.
- تم حذف الاعتماد على قاعدة SQLite المحلية للحجوزات والسجل. التخزين والحجوزات مسؤولية Node.js backend.
- داتا الأدوية تعتمد على ملف الداتا فقط: `egypt_common_100_medicines_chatbot.csv` أو المسار المحدد في `MEDICINES_DATA_PATH`.
- الشات بقى multi-session عن طريق `session_id`، يعني كذا عميل يقدروا يستخدموا البوت في نفس الوقت بدون خلط في السياق.
- الردود تلتزم بلغة المستخدم المختارة من التطبيق: `ar` أو `en`.
- جدول الدكاترة ممكن يوصل بعناوين عربية أو إنجليزية، والرد النهائي يترجم البيانات للغة المستخدم قدر الإمكان.

## الملفات المهمة

```text
app.py                 FastAPI server
salamtak_core.py        منطق الشات والبحث والترجمة بدون UI
conversation_logic.py   منطق triage والأعراض والاقتراحات
requirements.txt        مكتبات API فقط بدون Streamlit
egypt_common_100_medicines_chatbot.csv  ملف داتا الأدوية
.env.example            مثال إعدادات البيئة
```

## التشغيل

```bash
pip install -r requirements.txt
cp .env.example .env
uvicorn app:app --host 0.0.0.0 --port 8000 --reload
```

اختبار الصحة:

```bash
GET http://localhost:8000/health
```

## Endpoint الشات

```bash
POST http://localhost:8000/chat
Content-Type: application/json
```

مثال request عربي:

```json
{
  "session_id": "user-123",
  "language": "ar",
  "message": "عايز ميعاد دكتور أطفال بكرة",
  "doctors_schedule": [
    {
      "doctor_name": "Dr. Mona Khaled",
      "specialty": "Pediatrics",
      "day": "Monday",
      "start_time": "04:00 PM",
      "end_time": "07:00 PM",
      "available_slots": 6,
      "price": "250 EGP",
      "location": "Pediatrics clinic"
    }
  ]
}
```

مثال request إنجليزي:

```json
{
  "session_id": "user-456",
  "language": "en",
  "message": "Do you have a dermatologist today?",
  "doctors_schedule": [
    {
      "اسم الدكتور": "د. سارة علي",
      "التخصص": "جلدية",
      "اليوم": "الأحد",
      "من": "03:00 مساءً",
      "إلى": "06:00 مساءً",
      "عدد المواعيد المتاحة": 7,
      "سعر الكشف": "300 جنيه",
      "المكان": "عيادة الجلدية"
    }
  ]
}
```

مثال response:

```json
{
  "session_id": "user-123",
  "reply": "...",
  "language": "ar",
  "intent": "doctor",
  "matches_count": 1,
  "model": "gemini-2.5-flash",
  "suggestions": [],
  "state": {
    "last_medicine": "",
    "last_symptom": "",
    "active_triage": null,
    "recent_questions": ["عايز ميعاد دكتور أطفال بكرة"]
  }
}
```

## شكل الربط مع Node.js

Node.js هو المسؤول عن:

1. قراءة جدول الدكاترة من قاعدة البيانات.
2. إرسال الجدول في `doctors_schedule` مع أول رسالة أو عند تغير الجدول. إذا لم يتغير الجدول يمكن إرسال `[]` في باقي رسائل نفس السيشن.
3. تمرير `session_id` ثابت لكل مستخدم أو محادثة.
4. حفظ الرسائل والحجوزات في قاعدة البيانات الخاصة بالباك.
5. إرسال `language` حسب اختيار المستخدم في التطبيق أو صفحة الويب.

مثال Node.js مختصر:

```js
const response = await fetch("http://localhost:8000/chat", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    session_id: userSessionId,
    language: userLanguage, // "ar" or "en"
    message: userMessage,
    doctors_schedule: doctorsFromDatabase
  })
});

const data = await response.json();
console.log(data.reply);
```

## حفظ جدول الدكاترة داخل السيشن

الـ AI يحتفظ بآخر `doctors_schedule` وصله لكل `session_id` داخل الذاكرة فقط. هذا يعني:

- الباك يمكنه إرسال جدول الدكاترة أول مرة عند فتح الشات.
- في الرسائل التالية داخل نفس `session_id` يمكنه إرسال `doctors_schedule: []`.
- الـ AI سيستخدم الجدول المحفوظ لنفس السيشن عند أسئلة المواعيد.
- جدول كل مستخدم منفصل عن المستخدمين الآخرين.
- عند إغلاق السيشن يتم حذف سجل المحادثة وجدول الدكاترة المحفوظ.

إغلاق السيشن:

```bash
POST http://localhost:8000/sessions/user-123/close
```

أو:

```bash
DELETE http://localhost:8000/sessions/user-123
```

بعد الإغلاق، لو المستخدم سأل عن دكتور بدون إرسال جدول جديد، سيرد الـ AI بأن جدول الدكاترة غير متاح في هذا السيشن.

## ملاحظات أمان طبية

البوت إرشادي فقط. لا يشخص، لا يحدد جرعات، ولا يغني عن الطبيب أو الصيدلي. أي سؤال عن الجرعة أو عدد الأقراص أو مدة العلاج يتم منعه برد آمن.
