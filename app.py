import re
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st
from rapidfuzz import process, fuzz


# =========================================================
# Page Config
# =========================================================
st.set_page_config(
    page_title="Pharmacy AI Chatbot",
    page_icon="💊",
    layout="wide"
)


# =========================================================
# Arabic Text Cleaning
# =========================================================
def normalize_arabic(text):
    
    if pd.isna(text):
        return ""

    text = str(text).strip().lower()

    arabic_diacritics = re.compile(r"[\u0617-\u061A\u064B-\u0652]")
    text = re.sub(arabic_diacritics, "", text)

    arabic_numbers = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
    text = text.translate(arabic_numbers)

    text = text.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
    text = text.replace("ى", "ي")
    text = text.replace("ة", "ه")
    text = text.replace("ؤ", "و")
    text = text.replace("ئ", "ي")

    text = re.sub(r"[^\w\s\u0600-\u06FF]", " ", text)
    text = re.sub(r"\s+", " ", text)

    return text.strip()


def safe_text(value):
    """
    عشان نضمن إن أي قيمة تتحول لنص بدون أخطاء
    """
    if pd.isna(value):
        return ""
    return str(value)


# =========================================================
# Column Aliases
# =========================================================
COLUMN_ALIASES = {
    "name_ar": [
        "name_ar", "arabic_name", "drug_name_ar", "اسم الدواء", "اسم الدواء عربي",
        "الاسم التجاري", "الاسم", "اسم الصنف", "brand_ar", "brand name ar"
    ],
    "name_en": [
        "name_en", "english_name", "drug_name_en", "اسم الدواء انجليزي",
        "brand", "brand_name", "trade_name", "brand name", "drug name"
    ],
    "generic_name": [
        "generic_name", "active_ingredient", "ingredient", "active ingredient",
        "المادة الفعالة", "الماده الفعاله", "scientific_name", "اسم المادة الفعالة"
    ],
    "price": [
        "price", "السعر", "سعر", "public_price", "selling_price", "unit price"
    ],
    "currency": [
        "currency", "العملة", "currency_code"
    ],
    "form": [
        "form", "dosage_form", "dosage form", "الشكل", "الشكل الدوائي", "شكل الدواء"
    ],
    "dosage": [
        "dosage", "strength", "التركيز", "الجرعة", "dose", "concentration"
    ],
    "usage": [
        "usage", "uses", "indications", "indication",
        "الاستخدام", "الاستخدامات", "دواعي الاستعمال", "دواعى الاستعمال"
    ],
    "side_effects": [
        "side_effects", "side effects", "adverse_effects", "adverse effects",
        "الاثار الجانبية", "الآثار الجانبية", "الأعراض الجانبية", "اعراض جانبية"
    ],
    "warnings": [
        "warnings", "warning", "precautions", "precaution",
        "تحذيرات", "التحذيرات", "احتياطات", "موانع الاستعمال"
    ],
    "category": [
        "category", "class", "therapeutic_class", "therapeutic class",
        "الفئة", "التصنيف", "الفئة العلاجية", "تصنيف"
    ],
    "pregnancy": [
        "pregnancy", "pregnancy_status", "الحمل", "الحامل", "اثناء الحمل"
    ],
    "storage": [
        "storage", "الحفظ", "طريقة الحفظ", "التخزين", "طريقة التخزين"
    ]
}


def find_matching_column(df_columns, possible_names):
    """
    بيدور على اسم العمود حتى لو مكتوب بطريقة مختلفة
    """
    normalized_cols = {normalize_arabic(col): col for col in df_columns}

    for name in possible_names:
        n = normalize_arabic(name)
        if n in normalized_cols:
            return normalized_cols[n]

    return None


# =========================================================
# Data File Settings
# =========================================================
DATA_FILES = [
    "medicines.csv"
   
]


def find_data_file():
    """
    يدور تلقائيًا على ملف الداتا داخل فولدر المشروع
    """
    for file_name in DATA_FILES:
        if Path(file_name).exists():
            return file_name

    return None


# =========================================================
# Price Cleaning
# =========================================================
def clean_price_value(value):
    """
    تنظيف السعر وتحويله لرقم
    أمثلة:
    '25 EGP' -> 25
    '25 جنيه' -> 25
    """
    if pd.isna(value):
        return 0

    text = str(value).strip().lower()

    arabic_numbers = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
    text = text.translate(arabic_numbers)

    text = text.replace("egp", "")
    text = text.replace("جنيه", "")
    text = text.replace("جم", "")
    text = text.replace(",", "")

    numbers = re.findall(r"\d+(?:\.\d+)?", text)

    if numbers:
        return float(numbers[0])

    return 0


# =========================================================
# Read Raw Data
# =========================================================
@st.cache_data
def read_raw_data(file_path):
    """
    قراءة ملف الداتا سواء CSV أو Excel
    """
    file_path = Path(file_path)
    extension = file_path.suffix.lower()

    if extension == ".csv":
        try:
            df = pd.read_csv(file_path, encoding="utf-8-sig")
        except UnicodeDecodeError:
            df = pd.read_csv(file_path, encoding="cp1256")

    elif extension == ".xlsx":
        df = pd.read_excel(file_path)

    else:
        raise ValueError("نوع ملف غير مدعوم. استخدم CSV أو Excel فقط.")

    return df


# =========================================================
# Standardize Columns
# =========================================================
def standardize_dataframe(df):
    """
    توحيد أسماء الأعمدة عشان التطبيق يشتغل مع داتا مختلفة
    """
    df = df.copy()

    rename_map = {}

    for standard_col, aliases in COLUMN_ALIASES.items():
        matched_col = find_matching_column(df.columns, aliases)

        if matched_col:
            rename_map[matched_col] = standard_col

    df = df.rename(columns=rename_map)

    required_cols = [
        "name_ar", "name_en", "generic_name", "price", "currency",
        "form", "dosage", "usage", "side_effects", "warnings",
        "category", "pregnancy", "storage"
    ]

    for col in required_cols:
        if col not in df.columns:
            df[col] = ""

    return df


# =========================================================
# Preprocessing
# =========================================================
def preprocess_data(raw_df):
    """
    تجهيز وتنظيف الداتا قبل استخدامها في الشات بوت
    """
    report = {}

    report["عدد الصفوف قبل التنظيف"] = len(raw_df)
    report["عدد الأعمدة الأصلية"] = len(raw_df.columns)

    df = standardize_dataframe(raw_df)

    text_cols = [
        "name_ar", "name_en", "generic_name", "currency",
        "form", "dosage", "usage", "side_effects",
        "warnings", "category", "pregnancy", "storage"
    ]

    for col in text_cols:
        df[col] = df[col].fillna("").astype(str).str.strip()
        df[col] = df[col].replace("nan", "")

    df["price"] = df["price"].apply(clean_price_value)

    df["currency"] = df["currency"].replace("", np.nan).fillna("EGP")

    df["name_ar"] = df["name_ar"].replace("", np.nan).fillna(df["name_en"])

    df["name_en"] = df["name_en"].replace("", np.nan).fillna(df["name_ar"])


    return df, report


# =========================================================
# Load Data Automatically
# =========================================================
@st.cache_data
def load_data():
    
    data_file = find_data_file()

    if data_file is None:
        return None, None, None

    raw_df = read_raw_data(data_file)
    processed_df, report = preprocess_data(raw_df)

    return processed_df, report, data_file


# =========================================================
# Fuzzy Matching
# =========================================================
def find_drug(query, df, threshold=65):
    """
    البحث عن دواء حتى لو المستخدم كتب الاسم غلط
    """
    query_clean = normalize_arabic(query)

    choices = []

    for idx, row in df.iterrows():
        names = [
            row["name_ar"],
            row["name_en"],
            row["generic_name"]
        ]

        for name in names:
            name_clean = normalize_arabic(name)

            if name_clean and name_clean != "غير متوفر":
                choices.append((name_clean, idx))

    if not choices:
        return None, 0

    choice_texts = [x[0] for x in choices]

    result = process.extractOne(
        query_clean,
        choice_texts,
        scorer=fuzz.partial_ratio
    )

    if result and result[1] >= threshold:
        matched_text = result[0]
        score = result[1]

        for text, idx in choices:
            if text == matched_text:
                return df.loc[idx], score

    return None, 0


def find_two_drugs(query, df, threshold=65):
    
    query_clean = normalize_arabic(query)
    found = []

    for idx, row in df.iterrows():
        possible_names = [
            row["name_ar"],
            row["name_en"],
            row["generic_name"]
        ]

        for name in possible_names:
            name_clean = normalize_arabic(name)

            if name_clean and name_clean != "غير متوفر" and name_clean in query_clean:
                found.append(idx)
                break

    found = list(dict.fromkeys(found))

    if len(found) >= 2:
        return df.loc[found[0]], df.loc[found[1]]

    all_names = []

    for idx, row in df.iterrows():
        all_names.append((normalize_arabic(row["name_ar"]), idx))
        all_names.append((normalize_arabic(row["name_en"]), idx))
        all_names.append((normalize_arabic(row["generic_name"]), idx))

    all_names = [
        (name, idx)
        for name, idx in all_names
        if name and name != "غير متوفر"
    ]

    if not all_names:
        return None, None

    matches = process.extract(
        query_clean,
        [x[0] for x in all_names],
        scorer=fuzz.partial_ratio,
        limit=10
    )

    indexes = []

    for match in matches:
        name, score, _ = match

        if score >= threshold:
            for n, idx in all_names:
                if n == name:
                    indexes.append(idx)
                    break

    indexes = list(dict.fromkeys(indexes))

    if len(indexes) >= 2:
        return df.loc[indexes[0]], df.loc[indexes[1]]

    return None, None


# =========================================================
# Intent Detection
# =========================================================
def detect_intent(message):
    
    text = normalize_arabic(message)

    if any(word in text for word in ["مرحبا", "اهلا", "السلام", "هاي", "hello", "hi"]):
        return "greeting"

    if any(word in text for word in ["سعر", "بكام", "كام", "ثمن", "price", "cost"]):
        return "price"

    if any(word in text for word in ["اعراض", "اثار", "جانبيه", "side effect", "side effects"]):
        return "side_effects"

    if any(word in text for word in ["تحذير", "تحذيرات", "ممنوع", "احتياطات", "warning"]):
        return "warnings"

    if any(word in text for word in ["استخدام", "دواعي", "ليه", "بيستخدم", "فايده", "usage", "uses"]):
        return "usage"

    if any(word in text for word in ["قارن", "مقارنه", "افضل", "احسن", "ولا", "compare"]):
        return "compare"

    if any(word in text for word in ["بديل", "بدائل", "alternative", "similar"]):
        return "alternatives"

    if any(word in text for word in ["حمل", "حامل", "pregnancy"]):
        return "pregnancy"

    if any(word in text for word in ["حفظ", "يتخزن", "تخزين", "storage"]):
        return "storage"

    return "general_info"


# =========================================================
# Response Generator
# =========================================================
def drug_basic_info(drug):
   
    return f"""
💊 **{drug['name_ar']} - {drug['name_en']}**

**المادة الفعالة:** {drug['generic_name']}  
**الفئة:** {drug['category']}  
**الشكل الدوائي:** {drug['form']}  
**التركيز:** {drug['dosage']}  
**السعر:** {drug['price']} {drug['currency']}  

**الاستخدام:**  
{drug['usage']}

⚠️ **تنبيه:** المعلومات للتوعية فقط ولا تغني عن استشارة الطبيب أو الصيدلي.
"""


def generate_response(message, df):
    """
    توليد رد الشات بوت
    """
    intent = detect_intent(message)

    if intent == "greeting":
        return """
أهلًا بيك 👋  
أنا مساعد صيدلي ذكي.  
ممكن تسألني عن سعر دواء، استخدامه، آثاره الجانبية، التحذيرات، التخزين، الحمل، البدائل، أو تقارن بين دوائين.
""", intent

    if intent == "compare":
        drug1, drug2 = find_two_drugs(message, df)

        if drug1 is None or drug2 is None:
            return "اكتب اسم دوائين واضحين للمقارنة، مثل: قارن بين بنادول وفولتارين.", intent

        response = f"""
📊 **مقارنة بين {drug1['name_ar']} و {drug2['name_ar']}**

| العنصر | {drug1['name_ar']} | {drug2['name_ar']} |
|---|---|---|
| المادة الفعالة | {drug1['generic_name']} | {drug2['generic_name']} |
| الفئة | {drug1['category']} | {drug2['category']} |
| الشكل | {drug1['form']} | {drug2['form']} |
| التركيز | {drug1['dosage']} | {drug2['dosage']} |
| السعر | {drug1['price']} {drug1['currency']} | {drug2['price']} {drug2['currency']} |
| الحمل | {drug1['pregnancy']} | {drug2['pregnancy']} |

**استخدام {drug1['name_ar']}:**  
{drug1['usage']}

**استخدام {drug2['name_ar']}:**  
{drug2['usage']}

⚠️ المقارنة للتوعية فقط، والاختيار النهائي يكون مع الطبيب أو الصيدلي.
"""
        return response, intent

    drug, score = find_drug(message, df)

    if drug is None:
        return """
مش لاقي اسم الدواء في الداتا الحالية.  
جرب تكتب الاسم التجاري أو المادة الفعالة بطريقة مختلفة.
""", intent

    if intent == "price":
        response = f"""
💰 **سعر {drug['name_ar']} - {drug['name_en']}**

السعر: **{drug['price']} {drug['currency']}**

الشكل: {drug['form']}  
التركيز: {drug['dosage']}  

درجة المطابقة: {score}%
"""
        return response, intent

    if intent == "side_effects":
        response = f"""
⚠️ **الآثار الجانبية لـ {drug['name_ar']}**

{drug['side_effects']}

⚠️ لو ظهرت أعراض شديدة أو حساسية، لازم الرجوع للطبيب فورًا.
"""
        return response, intent

    if intent == "warnings":
        response = f"""
🚨 **تحذيرات {drug['name_ar']}**

{drug['warnings']}

⚠️ لا تستخدم الدواء بدون استشارة الطبيب خصوصًا في الحمل أو الأمراض المزمنة.
"""
        return response, intent

    if intent == "usage":
        response = f"""
📌 **استخدامات {drug['name_ar']}**

{drug['usage']}

**المادة الفعالة:** {drug['generic_name']}  
**الفئة:** {drug['category']}
"""
        return response, intent

    if intent == "pregnancy":
        response = f"""
🤰 **استخدام {drug['name_ar']} أثناء الحمل**

{drug['pregnancy']}

⚠️ في الحمل والرضاعة لازم استشارة الطبيب قبل أي دواء.
"""
        return response, intent

    if intent == "storage":
        response = f"""
📦 **طريقة حفظ {drug['name_ar']}**

{drug['storage']}
"""
        return response, intent

    if intent == "alternatives":
        same_category = df[
            (df["category_clean"] == drug["category_clean"]) &
            (df["name_ar"] != drug["name_ar"])
        ]

        if same_category.empty:
            return f"مش لاقي بدائل واضحة لـ {drug['name_ar']} في نفس الفئة داخل الداتا الحالية.", intent

        names = same_category["name_ar"].head(5).tolist()

        response = f"""
🔁 **بدائل أو أدوية مشابهة لـ {drug['name_ar']} داخل نفس الفئة:**

{", ".join(names)}

⚠️ البديل لا يعني نفس الجرعة أو نفس الأمان. لازم مراجعة الطبيب أو الصيدلي.
"""
        return response, intent

    return drug_basic_info(drug), intent


# =========================================================
# Load Data
# =========================================================
df, preprocessing_report, data_file = load_data()

if df is None:
    st.error("ملف الداتا مش موجود. حط ملف باسم drugs.csv أو drugs.xlsx جنب app.py")
    st.stop()


# =========================================================
# Sidebar
# =========================================================
with st.sidebar:
    st.header("📂 Data")

    st.success(f"تم تحميل ملف الداتا: {data_file}")
    st.info(f"عدد الأدوية بعد التنظيف: {len(df)}")

    st.divider()

    st.subheader("🧹 Preprocessing Report")

    for key, value in preprocessing_report.items():
        st.write(f"**{key}:** {value}")

    st.divider()

    st.subheader("📊 Data Preview")
    st.dataframe(df.head(10), use_container_width=True)

    st.divider()

    st.subheader("🔎 بحث سريع")
    search_query = st.text_input("اكتب اسم دواء")

    if search_query:
        drug, score = find_drug(search_query, df)

        if drug is not None:
            st.info(f"أقرب نتيجة: {drug['name_ar']} - درجة المطابقة {score}%")
        else:
            st.warning("لم يتم العثور على نتيجة")


# =========================================================
# Main UI
# =========================================================
st.title("💊 Pharmacy AI Chatbot")
st.caption("Streamlit version بدون API — بيقرأ الداتا تلقائيًا من ملف CSV أو Excel")

tab1, tab2, tab3, tab4 = st.tabs([
    "🤖 Chatbot",
    "💊 Drugs Table",
    "🧹 Preprocessing",
    "📌 Required Columns"
])


# =========================================================
# Tab 1: Chatbot
# =========================================================
with tab1:
    st.subheader("اسأل عن أي دواء")

    if "messages" not in st.session_state:
        st.session_state.messages = []

    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    user_message = st.chat_input("مثال: سعر بنادول؟ أو استخدامات أوجمنتين؟")

    if user_message:
        st.session_state.messages.append({
            "role": "user",
            "content": user_message
        })

        with st.chat_message("user"):
            st.markdown(user_message)

        response, intent = generate_response(user_message, df)

        st.session_state.messages.append({
            "role": "assistant",
            "content": response
        })

        with st.chat_message("assistant"):
            st.markdown(response)
            st.caption(f"Intent: {intent}")


# =========================================================
# Tab 2: Drugs Table
# =========================================================
with tab2:
    st.subheader("جدول الأدوية بعد التنظيف")

    columns_to_show = [
        "name_ar", "name_en", "generic_name", "price", "currency",
        "form", "dosage", "category", "usage", "side_effects", "warnings"
    ]

    existing_columns = [col for col in columns_to_show if col in df.columns]

    st.dataframe(df[existing_columns], use_container_width=True)

    csv = df.to_csv(index=False).encode("utf-8-sig")

    st.download_button(
        label="تحميل الداتا بعد التنظيف CSV",
        data=csv,
        file_name="cleaned_drugs_data.csv",
        mime="text/csv"
    )


# =========================================================
# Tab 3: Preprocessing
# =========================================================
with tab3:
    st.subheader("تقرير الـ Preprocessing")

    report_df = pd.DataFrame(
        list(preprocessing_report.items()),
        columns=["العملية", "القيمة"]
    )

    st.dataframe(report_df, use_container_width=True)

    st.markdown("### أعمدة البحث التي تم إنشاؤها")
    st.write("""
تم إنشاء أعمدة cleaned مثل:

- name_ar_clean
- name_en_clean
- generic_name_clean
- category_clean
- search_text_clean

هذه الأعمدة تساعد في البحث حتى لو المستخدم كتب عربي بدون تشكيل أو كتب الاسم بشكل قريب.
""")


# =========================================================
# Tab 4: Required Columns
# =========================================================
with tab4:
    st.subheader("الأعمدة الأفضل تكون موجودة في ملف الداتا")

    st.markdown("""
| Column | معناها |
|---|---|
| name_ar | اسم الدواء بالعربي |
| name_en | اسم الدواء بالإنجليزي |
| generic_name | المادة الفعالة |
| price | السعر |
| currency | العملة |
| form | الشكل الدوائي |
| dosage | التركيز أو الجرعة |
| usage | الاستخدامات |
| side_effects | الآثار الجانبية |
| warnings | التحذيرات |
| category | الفئة العلاجية |
| pregnancy | الحمل |
| storage | طريقة الحفظ |
""")

    st.info("مش لازم أسماء الأعمدة تكون بنفس الشكل بالضبط، الكود بيحاول يتعرف على أسماء عربية وإنجليزية مختلفة.")
