from __future__ import annotations

import json
import logging
import os
import random
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pandas as pd
from dotenv import load_dotenv
try:
    from google import genai
    from google.genai import types
except Exception:  # google-genai is optional at import time; local fallbacks still work.
    genai = None
    types = None
from rapidfuzz import fuzz

from conversation_logic import (
    SYMPTOM_NAMES,
    detect_symptoms,
    generate_dynamic_suggestions,
    has_emergency_signal,
    is_dosage_request,
    is_symptom_message,
    is_triage_cancel_request,
    start_triage,
    triage_question_reply,
    update_triage,
)

load_dotenv()

APP_DIR = Path(__file__).resolve().parent
APP_NAME = "سلامتك"
ASSISTANT_NAME = "حكيم AI"
ASSISTANT_IDENTITY = "حكيم AI، مساعد إلكتروني شخصي خاص بتطبيق سلامتك"
APP_NAME_EN = "Salamtak"
ASSISTANT_NAME_EN = "Hakim AI"

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip() or "gemini-2.5-flash"
MEDICINES_DATA_PATH = os.getenv("MEDICINES_DATA_PATH", "egypt_common_100_medicines_chatbot.csv").strip()

logger = logging.getLogger(APP_NAME_EN)

_gemini_client = genai.Client(api_key=GEMINI_API_KEY) if (GEMINI_API_KEY and genai is not None) else None


# =========================================================
# Normalization / Loading
# =========================================================

def normalize_text(text: Any) -> str:
    if pd.isna(text):
        return ""

    value = str(text).strip().lower()
    value = re.sub(r"[\u0617-\u061A\u064B-\u0652]", "", value)
    value = value.translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))
    value = (
        value.replace("أ", "ا")
        .replace("إ", "ا")
        .replace("آ", "ا")
        .replace("ى", "ي")
        .replace("ة", "ه")
        .replace("ؤ", "و")
        .replace("ئ", "ي")
        .replace("ـ", "")
    )
    value = re.sub(r"[^\w\s\u0600-\u06FF]", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def has_text(value: Any) -> bool:
    if value is None:
        return False
    text = str(value).strip()
    return text and text.lower() not in {"nan", "none", "null", "-", "_", "غير متاح"}


def is_empty_value(value: Any) -> bool:
    return not has_text(value)


def load_csv_safely(file_path: Path) -> pd.DataFrame:
    encodings = ["utf-8-sig", "utf-8", "cp1256"]
    last_error: Optional[Exception] = None
    for encoding in encodings:
        try:
            return pd.read_csv(file_path, encoding=encoding)
        except Exception as exc:  # pragma: no cover - preserved for compatibility with mixed encodings
            last_error = exc
    if last_error:
        raise last_error
    raise ValueError(f"Could not read CSV file: {file_path}")


def read_table_file(file_path: Path) -> pd.DataFrame:
    lowered = file_path.name.lower()
    if lowered.endswith(".csv"):
        return load_csv_safely(file_path)
    if lowered.endswith((".xlsx", ".xls")):
        return pd.read_excel(file_path)
    raise ValueError("Unsupported medicines file type. Use CSV, XLSX, or XLS.")


def clean_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    if df is None:
        return pd.DataFrame()
    clean = df.copy()
    clean = clean.drop(columns=["_search_blob"], errors="ignore")
    clean = clean.dropna(how="all")
    clean = clean.dropna(axis=1, how="all")
    clean = clean.fillna("")
    clean.columns = [str(col).strip() for col in clean.columns]
    if not clean.empty:
        clean["_search_blob"] = clean.apply(lambda row: normalize_text(" ".join(str(x) for x in row.values)), axis=1)
    else:
        clean["_search_blob"] = []
    return clean


MEDICINE_ALIASES = {
    "Panadol": "بانادول باندول panadol paracetamol باراسيتامول",
    "Brufen": "بروفين brufen ibuprofen ايبوبروفين",
    "Cetal": "سيتال cetal paracetamol باراسيتامول",
    "Augmentin": "اوجمنتين اجمنتين augmentin amoxicillin clavulanic",
    "Cataflam": "كتافلام cataflam diclofenac ديكلوفيناك",
    "Zyrtec": "زيرتك zyrtec cetirizine سيتريزين",
    "Telfast": "تلفاست telfast fexofenadine فيكسوفينادين",
    "Flagyl": "فلاجيل flagyl metronidazole مترونيدازول",
    "Gaviscon": "جافيسكون gaviscon حموضة ارتجاع",
    "Otrivin": "اوتريفين otrivin xylometazoline",
}


def standardize_medicines_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    clean = df.copy()
    column_map = {
        "اسم_تجاري_شائع_في_مصر": "اسم الدواء",
        "المادة_الفعالة": "المادة الفعالة",
        "الشكل_الصيدلي_الشائع": "الشكل الدوائي",
        "الاستخدامات_الشائعة": "الاستخدام",
        "جرعة_بالغين_عامة": "الجرعة",
        "أعراض_جانبية_شائعة": "الأعراض الجانبية",
        "تحذيرات_مختصرة": "أهم التحذيرات",
        "حالة_الصرف": "حالة الصرف",
        "ملاحظة_للبوت": "ملاحظات",
        "مصادر_للتحقق": "مصادر للتحقق",
    }
    clean = clean.rename(columns={old: new for old, new in column_map.items() if old in clean.columns})

    if "اسم الدواء" in clean.columns:
        names = clean["اسم الدواء"].fillna("").astype(str)
        active = clean["المادة الفعالة"].fillna("").astype(str) if "المادة الفعالة" in clean.columns else ""
        aliases = names.apply(lambda name: MEDICINE_ALIASES.get(str(name).strip(), ""))
        if "الأسماء الشائعة" not in clean.columns:
            clean["الأسماء الشائعة"] = names + " " + active + " " + aliases
        else:
            clean["الأسماء الشائعة"] = clean["الأسماء الشائعة"].fillna("").astype(str) + " " + names + " " + active + " " + aliases
    return clean


def load_medicines_data() -> Tuple[pd.DataFrame, str]:
    """Load medicines from file only. No internal fallback data is used."""
    configured = Path(MEDICINES_DATA_PATH)
    if not configured.is_absolute():
        configured = APP_DIR / configured
    if not configured.exists():
        raise FileNotFoundError(
            f"Medicines data file was not found: {configured}. Set MEDICINES_DATA_PATH or add egypt_common_100_medicines_chatbot.csv."
        )
    data = clean_dataframe(standardize_medicines_dataframe(read_table_file(configured)))
    return data, str(configured)


# =========================================================
# Doctor schedule standardization — API receives the schedule from Node.js
# =========================================================

DOCTOR_COLUMN_ALIASES: Dict[str, str] = {
    "اسم الدكتور": "اسم الدكتور",
    "اسم الطبيب": "اسم الدكتور",
    "الدكتور": "اسم الدكتور",
    "الطبيب": "اسم الدكتور",
    "doctor": "اسم الدكتور",
    "doctor_name": "اسم الدكتور",
    "doctorname": "اسم الدكتور",
    "dr_name": "اسم الدكتور",
    "name": "اسم الدكتور",
    "full_name": "اسم الدكتور",
    "التخصص": "التخصص",
    "تخصص": "التخصص",
    "specialty": "التخصص",
    "speciality": "التخصص",
    "department": "التخصص",
    "clinic": "التخصص",
    "اليوم": "اليوم",
    "يوم": "اليوم",
    "day": "اليوم",
    "weekday": "اليوم",
    "date": "اليوم",
    "التاريخ": "اليوم",
    "من": "من",
    "بداية": "من",
    "من الساعة": "من",
    "from": "من",
    "start": "من",
    "start_time": "من",
    "starttime": "من",
    "إلى": "إلى",
    "الى": "إلى",
    "نهاية": "إلى",
    "إلى الساعة": "إلى",
    "to": "إلى",
    "end": "إلى",
    "end_time": "إلى",
    "endtime": "إلى",
    "عدد المواعيد المتاحة": "عدد المواعيد المتاحة",
    "المواعيد المتاحة": "عدد المواعيد المتاحة",
    "available_slots": "عدد المواعيد المتاحة",
    "slots": "عدد المواعيد المتاحة",
    "capacity": "عدد المواعيد المتاحة",
    "سعر الكشف": "سعر الكشف",
    "السعر": "سعر الكشف",
    "price": "سعر الكشف",
    "fee": "سعر الكشف",
    "consultation_fee": "سعر الكشف",
    "المكان": "المكان",
    "العنوان": "المكان",
    "location": "المكان",
    "place": "المكان",
    "address": "المكان",
    "ملاحظات": "ملاحظات",
    "ملاحظة": "ملاحظات",
    "notes": "ملاحظات",
    "note": "ملاحظات",
}


def _canonical_doctor_column(column: Any) -> str:
    raw = str(column or "").strip()
    normalized = normalize_text(raw)
    for alias, canonical in DOCTOR_COLUMN_ALIASES.items():
        if normalize_text(alias) == normalized:
            return canonical
    return raw


def standardize_doctors_schedule(schedule: Optional[Iterable[Dict[str, Any]]]) -> pd.DataFrame:
    """Convert a Node.js doctors schedule JSON array to the canonical chatbot columns."""
    rows = list(schedule or [])
    if not rows:
        return clean_dataframe(pd.DataFrame())

    normalized_rows: List[Dict[str, Any]] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        normalized_item: Dict[str, Any] = {}
        for key, value in item.items():
            normalized_item[_canonical_doctor_column(key)] = value
        normalized_rows.append(normalized_item)
    return clean_dataframe(pd.DataFrame(normalized_rows))


# =========================================================
# Safety
# =========================================================

HIDDEN_MEDICAL_COLUMNS = {
    "جرعة",
    "الجرعة",
    "جرعه",
    "الجرعه",
    "جرعة بالغين عامة",
    "جرعة_بالغين_عامة",
    "dose",
    "dosage",
    "adult dose",
    "dose instructions",
}

UNSAFE_DOSAGE_REPLY_PATTERNS = [
    r"\b(?:take|use)\s+\d+\s*(?:tablet|tablets|pill|pills|capsule|capsules|ml|mg)\b",
    r"\b\d+\s*(?:tablet|tablets|pill|pills|capsule|capsules|ml|mg)\s+(?:every|daily|per day|twice|three times)\b",
    r"\b(?:اخد|خذ|استخدم|خد)\s+\d+\s*(?:قرص|اقراص|كبسوله|كبسولة|مل|مجم)\b",
    r"\b\d+\s*(?:قرص|اقراص|كبسوله|كبسولة|مل|مجم)\s*(?:كل|يوميا|يوميًا|مرات)\b",
    r"\bكل\s+\d+\s*(?:ساعه|ساعة|hours?)\b",
]


def is_hidden_medical_column(column_name: Any) -> bool:
    normalized = normalize_text(column_name)
    return any(normalized == normalize_text(item) for item in HIDDEN_MEDICAL_COLUMNS)


def user_safe_row(row: pd.Series) -> pd.Series:
    return row.drop(labels=[col for col in row.index if col == "_search_blob" or is_hidden_medical_column(col)], errors="ignore")


def contains_unsafe_dosage_advice(text: str) -> bool:
    normalized = normalize_text(text)
    return any(re.search(pattern, normalized, flags=re.IGNORECASE) for pattern in UNSAFE_DOSAGE_REPLY_PATTERNS)


def guard_medical_reply(user_question: str, intent: str, candidate_reply: str, fallback_reply: str, language: str = "ar") -> str:
    medical_intents = {"medicine", "medicine_specific_column", "medicine_comparison", "symptom_triage"}
    if intent in medical_intents and contains_unsafe_dosage_advice(candidate_reply):
        logger.warning("Blocked polished reply because it contained dose-like advice.")
        if contains_unsafe_dosage_advice(fallback_reply):
            return dosage_reply(language)
        return fallback_reply
    return candidate_reply


# =========================================================
# Translation helpers
# =========================================================

EN_LABELS = {
    "اسم الدواء": "Medicine name",
    "المادة الفعالة": "Active ingredient",
    "التصنيف": "Category",
    "الشكل الدوائي": "Dosage form",
    "حالة الصرف": "Dispensing status",
    "الاستخدام": "General use",
    "الاستخدام العام": "General use",
    "أهم التحذيرات": "Key warnings",
    "الأعراض الجانبية": "Common side effects",
    "ملاحظات": "Notes",
    "الدكتور": "Doctor",
    "التخصص": "Specialty",
    "اليوم": "Day",
    "من": "From",
    "إلى": "To",
    "سعر الكشف": "Consultation fee",
    "المكان": "Place",
    "المواعيد المتاحة": "Available slots",
    "عدد المواعيد المتاحة": "Available slots",
    "البند": "Item",
}

AR_LABELS = {value: key for key, value in EN_LABELS.items()}

VALUE_TRANSLATIONS_EN = {
    "أطفال": "Pediatrics",
    "باطنة": "Internal medicine",
    "عظام": "Orthopedics",
    "جلدية": "Dermatology",
    "أسنان": "Dentistry",
    "نساء وتوليد": "Obstetrics and gynecology",
    "السبت": "Saturday",
    "الأحد": "Sunday",
    "الاحد": "Sunday",
    "الإثنين": "Monday",
    "الاثنين": "Monday",
    "الثلاثاء": "Tuesday",
    "الأربعاء": "Wednesday",
    "الاربعاء": "Wednesday",
    "الخميس": "Thursday",
    "الجمعة": "Friday",
    "مساءً": "PM",
    "مساء": "PM",
    "صباحًا": "AM",
    "صباحا": "AM",
    "جنيه": "EGP",
    "العيادة الرئيسية": "Main clinic",
    "عيادة الأطفال": "Pediatrics clinic",
    "عيادة الجلدية": "Dermatology clinic",
    "عيادة الأسنان": "Dental clinic",
    "عيادة النساء": "Women's clinic",
    "مسكن وخافض حرارة": "Pain reliever and fever reducer",
    "مضاد التهاب غير ستيرويدي NSAID": "Non-steroidal anti-inflammatory drug (NSAID)",
    "مضاد حيوي": "Antibiotic",
    "مضاد حساسية": "Antihistamine",
    "بدون وصفة غالبًا": "Usually over the counter",
    "أقراص": "Tablets",
    "شراب": "Syrup",
    "نقط": "Drops",
    "كبسولات": "Capsules",
    "حقن": "Injections",
}

VALUE_TRANSLATIONS_AR = {value.lower(): key for key, value in VALUE_TRANSLATIONS_EN.items()}
VALUE_TRANSLATIONS_AR.update(
    {
        "pediatrics": "أطفال",
        "pediatrician": "طبيب أطفال",
        "internal medicine": "باطنة",
        "orthopedics": "عظام",
        "dermatology": "جلدية",
        "dentistry": "أسنان",
        "dentist": "طبيب أسنان",
        "obstetrics and gynecology": "نساء وتوليد",
        "saturday": "السبت",
        "sunday": "الأحد",
        "monday": "الإثنين",
        "tuesday": "الثلاثاء",
        "wednesday": "الأربعاء",
        "thursday": "الخميس",
        "friday": "الجمعة",
        "egp": "جنيه",
        "main clinic": "العيادة الرئيسية",
    }
)


def label_for(label: str, language: str = "ar") -> str:
    if language == "en":
        return EN_LABELS.get(label, label)
    return AR_LABELS.get(label, label)


def text_has_arabic(text: str) -> bool:
    return bool(re.search(r"[\u0600-\u06FF]", text or ""))


def text_has_latin(text: str) -> bool:
    return bool(re.search(r"[A-Za-z]", text or ""))


def local_translate_value(value: Any, language: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if language == "en":
        result = VALUE_TRANSLATIONS_EN.get(text, text)
        for ar, en in sorted(VALUE_TRANSLATIONS_EN.items(), key=lambda item: len(item[0]), reverse=True):
            result = result.replace(ar, en)
        return result
    lower_text = text.lower()
    result = VALUE_TRANSLATIONS_AR.get(lower_text, text)
    for en, ar in sorted(VALUE_TRANSLATIONS_AR.items(), key=lambda item: len(item[0]), reverse=True):
        result = re.sub(rf"\b{re.escape(en)}\b", ar, result, flags=re.IGNORECASE)
    return result


def needs_translation(value: str, language: str) -> bool:
    if not value:
        return False
    if language == "en":
        return text_has_arabic(value)
    return text_has_latin(value)


def translate_value_bundle_with_gemini(values: Dict[str, str], language: str) -> Dict[str, str]:
    if not _gemini_client or not values:
        return {}
    target = "Arabic" if language == "ar" else "English"
    prompt = f"""
Translate the JSON values to {target}.
Rules:
- Return one valid JSON object only with exactly the same keys.
- Translate only the text meaning already present. Do not add medical facts, diagnosis, warnings, prices, dates, or appointment slots.
- Keep names, numbers, phone numbers, URLs, and medicine active ingredients unchanged unless they are common specialty/day/place words.
- Use short, patient-friendly wording.

Input JSON:
{json.dumps(values, ensure_ascii=False)}
"""
    try:
        response = _gemini_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0,
                max_output_tokens=1400,
                response_mime_type="application/json",
            ) if types is not None else None,
        )
        raw = str(getattr(response, "text", "") or "").strip()
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            return {}
        result: Dict[str, str] = {}
        for key, original in values.items():
            translated = str(parsed.get(key, "") or "").strip()
            if translated and len(translated) <= max(1200, len(str(original)) * 5):
                result[str(key)] = translated
        return result
    except Exception as exc:  # pragma: no cover - external API is optional
        logger.warning("Gemini translation failed: %s", exc)
        return {}


def values_for_language(values: Dict[str, Any], language: str) -> Dict[str, str]:
    prepared = {str(key): str(value or "").strip() for key, value in values.items()}
    locally_translated = {key: local_translate_value(value, language) for key, value in prepared.items()}
    to_translate = {key: value for key, value in locally_translated.items() if needs_translation(value, language)}
    gemini_result = translate_value_bundle_with_gemini(to_translate, language)
    final = dict(locally_translated)
    final.update(gemini_result)
    return final


def display_value(value: Any, language: str = "ar") -> str:
    return values_for_language({"value": value}, language).get("value", str(value or "").strip())


def markdown_list(items: List[Tuple[str, Any]], language: str = "ar") -> str:
    lines = []
    for label, value in items:
        shown = display_value(value, language)
        if has_text(shown):
            lines.append(f"- **{label_for(label, language)}:** {shown}")
    return "\n".join(lines)


def markdown_table(headers: List[str], rows: List[List[Any]], language: str = "ar") -> str:
    safe_headers = [label_for(str(header), language).replace("|", "\\|") for header in headers]
    lines = [
        "| " + " | ".join(safe_headers) + " |",
        "| " + " | ".join(["---"] * len(safe_headers)) + " |",
    ]
    for row in rows:
        safe_cells = []
        for index, cell in enumerate(row):
            shown = label_for(str(cell), language) if index == 0 else display_value(cell, language)
            safe_cells.append(str(shown).replace("\n", "<br>").replace("|", "\\|"))
        lines.append("| " + " | ".join(safe_cells) + " |")
    return "\n".join(lines)


# =========================================================
# Search / Intent
# =========================================================

STOP_WORDS = {
    "ايه",
    "ما",
    "هو",
    "هي",
    "ده",
    "دي",
    "دا",
    "من",
    "عن",
    "في",
    "على",
    "علي",
    "عايز",
    "عايزه",
    "عاوز",
    "عاوزه",
    "محتاج",
    "محتاجه",
    "ينفع",
    "ممكن",
    "استخدم",
    "استخدام",
    "بتاع",
    "بتاعه",
    "بيستخدم",
    "يستخدم",
    "دوا",
    "دواء",
    "علاج",
    "دكتور",
    "دكتوره",
    "طبيب",
    "طبيبه",
    "ميعاد",
    "موعد",
    "مواعيد",
    "كشف",
    "سعر",
    "كام",
    "امتى",
    "امتي",
}


def get_keywords(text: str) -> List[str]:
    words = normalize_text(text).split()
    return [word for word in words if len(word) >= 3 and word not in STOP_WORDS]


def safe_get(row: pd.Series, col: str, default: str = "غير متاح") -> str:
    value = row.get(col, default)
    return default if is_empty_value(value) else str(value).strip()


def score_row_against_question(question: str, row: pd.Series) -> int:
    q = normalize_text(question)
    q_words = get_keywords(question)
    row_clean = row.drop(labels=["_search_blob"], errors="ignore")
    expanded_values: List[str] = []
    for value in row_clean.values:
        expanded_values.append(str(value))
        expanded_values.append(local_translate_value(value, "ar"))
        expanded_values.append(local_translate_value(value, "en"))
    full_blob = normalize_text(" ".join(expanded_values))
    scores = [fuzz.WRatio(q, full_blob), fuzz.token_set_ratio(q, full_blob), fuzz.partial_ratio(q, full_blob)]

    important_columns = [
        "اسم الدواء",
        "الأسماء الشائعة",
        "المادة الفعالة",
        "التصنيف",
        "التخصص",
        "اسم الدكتور",
        "اليوم",
        "المكان",
    ]
    for col in important_columns:
        if col not in row_clean.index:
            continue
        value = normalize_text(row_clean[col])
        if not value:
            continue
        scores.extend([fuzz.WRatio(q, value), fuzz.partial_ratio(q, value), fuzz.token_set_ratio(q, value)])
        value_words = get_keywords(value)
        for qw in q_words:
            for vw in value_words:
                scores.extend([fuzz.ratio(qw, vw), fuzz.partial_ratio(qw, vw)])
                if qw in vw or vw in qw:
                    scores.append(95)
    return int(max(scores)) if scores else 0


def find_best_matches(question: str, df: pd.DataFrame, top_k: int = 5) -> List[Dict[str, Any]]:
    if not normalize_text(question) or df is None or df.empty:
        return []
    results = []
    for index, row in df.iterrows():
        results.append({"index": index, "score": score_row_against_question(question, row), "row": row})
    return sorted(results, key=lambda item: item["score"], reverse=True)[:top_k]


def rows_to_context(matches: List[Dict[str, Any]], max_rows: int = 5) -> str:
    if not matches:
        return "لا توجد نتائج مطابقة في الداتا."
    context_lines: List[str] = []
    for i, item in enumerate(matches[:max_rows], start=1):
        row = user_safe_row(item["row"])
        context_lines.append(f"نتيجة رقم {i}")
        context_lines.append(f"نسبة التشابه: {item['score']}")
        for col, value in row.items():
            if has_text(value):
                context_lines.append(f"{col}: {value}")
        context_lines.append("-" * 40)
    return "\n".join(context_lines)


def medicine_identity_text(row: pd.Series) -> str:
    identity_columns = ["اسم الدواء", "الأسماء الشائعة", "المادة الفعالة"]
    values = [str(row.get(col, "")) for col in identity_columns if col in row.index]
    return normalize_text(" ".join(values))


def score_medicine_identity(question: str, row: pd.Series) -> int:
    q = normalize_text(question)
    identity = medicine_identity_text(row)
    if not q or not identity:
        return 0
    q_words = [word for word in q.split() if len(word) >= 3]
    identity_words = [word for word in identity.split() if len(word) >= 3]
    scores = [fuzz.WRatio(q, identity), fuzz.token_set_ratio(q, identity), fuzz.partial_ratio(q, identity)]
    for qw in q_words:
        for iw in identity_words:
            if qw == iw:
                scores.append(100)
            elif qw in iw or iw in qw:
                scores.append(92)
            else:
                scores.append(fuzz.ratio(qw, iw))
    return int(max(scores)) if scores else 0


def find_named_medicine_matches(question: str, medicines_df: pd.DataFrame, top_k: int = 4, min_score: int = 78) -> List[Dict[str, Any]]:
    if medicines_df is None or medicines_df.empty:
        return []
    if not [word for word in normalize_text(question).split() if len(word) >= 3]:
        return []
    results = []
    for index, row in medicines_df.iterrows():
        score = score_medicine_identity(question, row)
        if score >= min_score:
            results.append({"index": index, "score": score, "row": row})
    return sorted(results, key=lambda item: item["score"], reverse=True)[:top_k]


WEEKDAYS_AR = {0: "الاثنين", 1: "الثلاثاء", 2: "الأربعاء", 3: "الخميس", 4: "الجمعة", 5: "السبت", 6: "الأحد"}
WEEKDAYS_EN = {0: "Monday", 1: "Tuesday", 2: "Wednesday", 3: "Thursday", 4: "Friday", 5: "Saturday", 6: "Sunday"}
RELATIVE_DAY_OFFSETS = {
    "النهارده": 0,
    "النهاردة": 0,
    "اليوم": 0,
    "today": 0,
    "بكره": 1,
    "بكرة": 1,
    "غدا": 1,
    "غدًا": 1,
    "tomorrow": 1,
    "بعد بكره": 2,
    "بعد بكرة": 2,
    "after tomorrow": 2,
}
SPECIALTY_ALIASES = {
    "pediatrician": "أطفال",
    "paediatrician": "أطفال",
    "children doctor": "أطفال",
    "pediatrics": "أطفال",
    "dentist": "أسنان",
    "dentistry": "أسنان",
    "dermatologist": "جلدية",
    "dermatology": "جلدية",
    "skin doctor": "جلدية",
    "cardiologist": "قلب",
    "heart doctor": "قلب",
    "orthopedic": "عظام",
    "orthopedics": "عظام",
    "orthopaedic": "عظام",
    "bone doctor": "عظام",
    "ent": "أنف أذن حنجرة",
    "neurologist": "مخ وأعصاب",
    "surgeon": "جراحة",
    "gynecologist": "نساء وتوليد",
    "gynaecologist": "نساء وتوليد",
    "obgyn": "نساء وتوليد",
    "باطنه": "باطنة",
    "باطنة": "باطنة",
    "اطفال": "أطفال",
    "أطفال": "أطفال",
    "عظام": "عظام",
    "جلديه": "جلدية",
    "جلدية": "جلدية",
    "اسنان": "أسنان",
    "أسنان": "أسنان",
    "نساء": "نساء وتوليد",
    "توليد": "نساء وتوليد",
}


def today_info() -> Dict[str, str]:
    today = datetime.now().date()
    weekday = today.weekday()
    return {"date": today.isoformat(), "weekday_ar": WEEKDAYS_AR[weekday], "weekday_en": WEEKDAYS_EN[weekday]}


def today_context_text(language: str = "ar") -> str:
    info = today_info()
    if language == "en":
        return f"Today is {info['weekday_en']}, {info['date']}."
    return f"تاريخ اليوم: {info['date']}، واليوم هو {info['weekday_ar']}."


def requested_relative_day(question: str) -> Optional[Dict[str, str]]:
    q = normalize_text(question)
    for keyword, offset in RELATIVE_DAY_OFFSETS.items():
        if normalize_text(keyword) in q:
            target = datetime.now().date() + timedelta(days=offset)
            weekday = target.weekday()
            return {"date": target.isoformat(), "weekday_ar": WEEKDAYS_AR[weekday], "weekday_en": WEEKDAYS_EN[weekday]}
    return None


def question_has_alias(question: str, alias: str) -> bool:
    q = normalize_text(question)
    normalized_alias = normalize_text(alias)
    if not normalized_alias:
        return False
    if re.fullmatch(r"[a-z0-9 ]+", normalized_alias):
        alias_words = normalized_alias.split()
        q_words = q.split()
        if len(alias_words) == 1:
            return normalized_alias in q_words
        return normalized_alias in q
    return normalized_alias in q


def enrich_doctor_question_with_date(question: str) -> str:
    additions: List[str] = []
    requested_day = requested_relative_day(question)
    if requested_day:
        additions.extend([requested_day["weekday_ar"], requested_day["weekday_en"], requested_day["date"]])
    for alias, specialty in SPECIALTY_ALIASES.items():
        if question_has_alias(question, alias):
            additions.append(specialty)
    return question if not additions else f"{question} {' '.join(additions)}"


def requested_doctor_filters(question: str) -> Dict[str, str]:
    filters: Dict[str, str] = {}
    requested_day = requested_relative_day(question)
    if requested_day:
        filters["day"] = requested_day["weekday_ar"]
    for alias, specialty in SPECIALTY_ALIASES.items():
        if question_has_alias(question, alias):
            filters["specialty"] = specialty
            break
    return filters


def doctor_row_matches_filters(row: pd.Series, filters: Dict[str, str]) -> bool:
    if not filters:
        return True
    if "day" in filters:
        row_day = normalize_text(" ".join([safe_get(row, "اليوم", ""), display_value(safe_get(row, "اليوم", ""), "ar")]))
        requested_day = normalize_text(filters["day"])
        requested_day_en = normalize_text(display_value(filters["day"], "en"))
        if requested_day not in row_day and requested_day_en not in row_day:
            return False
    if "specialty" in filters:
        row_text = normalize_text(
            " ".join(
                [
                    safe_get(row, "التخصص", ""),
                    display_value(safe_get(row, "التخصص", ""), "ar"),
                    safe_get(row, "اسم الدكتور", ""),
                    safe_get(row, "المكان", ""),
                    safe_get(row, "ملاحظات", ""),
                ]
            )
        )
        requested_specialty = normalize_text(filters["specialty"])
        requested_specialty_en = normalize_text(display_value(filters["specialty"], "en"))
        if requested_specialty not in row_text and requested_specialty_en not in row_text:
            return False
    return True


def response_language(question: str, preferred_language: Optional[str] = None) -> str:
    if preferred_language in {"ar", "en"}:
        return preferred_language
    q = normalize_text(question)
    explicit_english = ["in english", "english please", "reply in english", "respond in english", "answer in english", "بالانجليزي", "انجليزي"]
    if any(phrase in q for phrase in explicit_english):
        return "en"
    letters = re.findall(r"[a-zA-Z]", str(question or ""))
    arabic_letters = re.findall(r"[\u0600-\u06FF]", str(question or ""))
    return "en" if len(letters) >= 12 and len(letters) > len(arabic_letters) * 2 else "ar"


def keyword_hits(q: str, keywords: List[str]) -> int:
    return sum(1 for word in keywords if normalize_text(word) in q)


def detect_intent(question: str) -> str:
    q = normalize_text(question)
    identity_keywords = ["انت مين", "انتي مين", "مين انت", "مين انتي", "تعرف نفسك", "عرف نفسك", "who are you", "what are you"]
    if any(word in q for word in identity_keywords):
        return "identity"
    greeting_keywords = ["اهلا", "هاي", "السلام", "مساء الخير", "صباح الخير", "ازيك", "مرحبا", "hello", "hi"]
    doctor_keywords = [
        "ميعاد",
        "موعد",
        "مواعيد",
        "حجز",
        "احجز",
        "دكتور",
        "دكتوره",
        "دكاتره",
        "طبيب",
        "طبيبه",
        "اطباء",
        "عياده",
        "عيادات",
        "كشف",
        "استشاره",
        "متاح",
        "متاحين",
        "جدول",
        "تخصص",
        "تخصصات",
        "باطنه",
        "اطفال",
        "عظام",
        "جلديه",
        "اسنان",
        "نساء",
        "appointment",
        "appointments",
        "doctor",
        "clinic",
        "specialty",
        "consultation",
        "today",
        "tomorrow",
        "pediatrician",
        "dentist",
        "dermatologist",
    ]
    medicine_keywords = [
        "دواء",
        "دوا",
        "علاج",
        "برشام",
        "اقراص",
        "شراب",
        "جرعه",
        "استخدام",
        "اعراض",
        "اضرار",
        "تحذير",
        "تحذيرات",
        "موانع",
        "ماده فعاله",
        "باندول",
        "بانادول",
        "اجمنتين",
        "اوجمنتين",
        "بروفين",
        "كتافلام",
        "panadol",
        "augmentin",
        "brufen",
        "cataflam",
        "side effects",
        "warning",
        "contraindications",
    ]
    doctor_score = keyword_hits(q, doctor_keywords)
    medicine_score = keyword_hits(q, medicine_keywords)
    if doctor_score == 0 and medicine_score == 0 and keyword_hits(q, greeting_keywords) > 0 and len(q.split()) <= 5:
        return "greeting"
    if doctor_score > medicine_score:
        return "doctor"
    if medicine_score > 0:
        return "medicine"
    return "unknown"


def has_emergency_words(question: str) -> bool:
    q = normalize_text(question)
    emergency_keywords = [
        "الم صدر شديد",
        "الم في الصدر",
        "ضيق تنفس",
        "مش قادر اتنفس",
        "اغماء",
        "نزيف شديد",
        "تورم في الوش",
        "تورم في الوجه",
        "قيء مستمر",
        "تشنجات",
        "جرعه زياده",
        "جرعه زائده",
        "تسمم",
        "صداع مفاجئ",
        "تنميل",
        "شلل",
        "chest pain",
        "shortness of breath",
        "cannot breathe",
        "fainting",
        "seizure",
        "overdose",
        "poisoning",
        "sudden headache",
        "stroke",
    ]
    return has_emergency_signal(question) or any(word in q for word in emergency_keywords)


def asks_for_comparison(question: str) -> bool:
    q = normalize_text(question)
    return any(word in q for word in ["قارن", "مقارنه", "مقارنة", "الفرق", "فرق بين", "ايه الفرق", "مين احسن", "افضل", "احسن", "ولا", "vs", "versus", "compare"])


def has_strong_comparison_word(question: str) -> bool:
    q = normalize_text(question)
    return any(word in q for word in ["قارن", "مقارنه", "مقارنة", "الفرق", "فرق بين", "ايه الفرق", "مين احسن", "افضل", "احسن", "vs", "versus", "compare"])


def asks_for_alternatives(question: str) -> bool:
    q = normalize_text(question)
    return any(word in q for word in ["بديل", "بدائل", "البديل", "البدائل", "alternative", "alternatives", "substitute"])


def is_contextual_medicine_followup(question: str) -> bool:
    q = normalize_text(question)
    followup_keywords = [
        "استخدامه",
        "استخدامها",
        "بيستخدم",
        "بتستخدم",
        "لايه",
        "تحذيراته",
        "اضراره",
        "اعراضه",
        "مادته",
        "الماده الفعاله",
        "شكله",
        "حالة الصرف",
        "بوصفه",
        "بديله",
        "بدائل",
    ]
    pronouns = ["هو", "هي", "ده", "دي", "دا"]
    return any(word in q for word in followup_keywords) or (len(q.split()) <= 5 and any(word in q for word in pronouns))


# =========================================================
# Replies
# =========================================================

MEDICAL_DISCLAIMERS = [
    "المعلومة دي للتوضيح فقط، والأفضل تتأكد من طبيب أو صيدلي قبل الاستخدام.",
    "خلي بالك إن ده شرح عام من البيانات المتاحة، ومش بديل عن رأي طبيب أو صيدلي.",
    "لو عندك مرض مزمن أو بتاخد أدوية تانية، راجع مختص قبل ما تستخدم الدواء.",
]
MEDICINE_OPENINGS = [
    "لقيت الدواء في البيانات، وخليني أرتب لك المعلومة بشكل واضح.",
    "أيوه، الدواء ظاهر عندي. ده ملخصه بهدوء.",
    "حاضر، ده أقرب ملخص واضح للدواء من البيانات الموجودة.",
]
DOCTOR_OPENINGS = [
    "لقيت لك اختيار مناسب في الجدول، وده تفصيله.",
    "تمام، ده أقرب ميعاد واضح عندي.",
    "أيوه، فيه نتيجة مناسبة لسؤالك في جدول الدكاترة.",
]


def emergency_reply(language: str = "ar") -> str:
    if language == "en":
        return "What you described may be urgent. Please seek emergency care immediately or contact a doctor/pharmacist urgently."
    return "الكلام اللي وصفته ممكن يكون طارئ. الأفضل تتوجه للطوارئ فورًا أو تتواصل مع طبيب/صيدلي بشكل عاجل."


def dosage_reply(language: str = "ar") -> str:
    if language == "en":
        return (
            "I can explain general use and warnings from the available data, but I cannot set a dose, number of tablets, "
            "or treatment duration. A doctor or pharmacist must decide that based on age, weight, health condition, and other medicines."
        )
    return "أقدر أوضح الاستخدام العام والتحذيرات من البيانات المتاحة، لكن مقدرش أحدد جرعة أو عدد أقراص أو مدة استخدام. الجرعة لازم يحددها طبيب أو صيدلي."


def unclear_reply(language: str = "ar") -> str:
    if language == "en":
        return "I cannot tell whether you are asking about a medicine or a doctor appointment. Please write the medicine name or specialty more clearly."
    return "مش قادر أحدد من سؤالك هل بتسأل عن دواء ولا عن ميعاد دكتور. اكتب اسم الدواء أو التخصص بشكل أوضح."


def not_found_reply(intent: str, language: str = "ar") -> str:
    if intent == "doctor":
        if language == "en":
            return "I could not find a clearly matching doctor or appointment in the schedule sent by the backend. Try another specialty, doctor name, or day."
        return "مش لاقي دكتور أو ميعاد مطابق بوضوح في جدول الدكاترة اللي وصلني من الباك. جرّب تكتب التخصص، اسم الدكتور، أو اليوم."
    if language == "en":
        return "I could not clearly find that medicine in the current medicines file. Try writing the brand name or active ingredient another way."
    return "مش لاقي الدواء ده بوضوح في ملف الأدوية الحالي. جرّب تكتب الاسم التجاري أو المادة الفعالة بطريقة تانية."


def detect_requested_column(question: str, df: pd.DataFrame) -> Optional[str]:
    q = normalize_text(question)
    if df is None or df.empty:
        return None
    columns = [col for col in df.columns if col != "_search_blob"]
    column_aliases = {
        "اسم الدواء": ["اسم الدواء", "اسم العلاج", "اسم الدوا"],
        "الأسماء الشائعة": ["الاسماء الشائعه", "الاسم التجاري", "اسم تاني"],
        "المادة الفعالة": ["الماده الفعاله", "ماده فعاله", "active ingredient", "active substance"],
        "التصنيف": ["التصنيف", "نوع الدواء", "category"],
        "الاستخدام": ["الاستخدام", "دواعي الاستخدام", "بيستخدم في ايه", "لايه", "indication", "used for"],
        "الشكل الدوائي": ["الشكل الدوائي", "الشكل", "form", "dosage form"],
        "أهم التحذيرات": ["التحذيرات", "تحذير", "احتياطات", "warnings", "precautions"],
        "ملاحظات": ["ملاحظات", "ملاحظه", "note", "notes"],
        "الأعراض الجانبية": ["الاعراض الجانبيه", "اضرار", "اثار جانبيه", "side effects", "adverse effects"],
        "حالة الصرف": ["حالة الصرف", "بوصفه", "بدون وصفه", "otc", "prescription"],
        "مصادر للتحقق": ["مصدر", "مصادر", "source", "reference"],
        "موانع الاستخدام": ["موانع الاستخدام", "موانع", "contraindications"],
        "البدائل": ["بديل", "بدائل", "alternative", "alternatives"],
    }
    for col, aliases in column_aliases.items():
        if col in columns and any(normalize_text(alias) in q for alias in aliases):
            return col
    for col in columns:
        if normalize_text(col) and normalize_text(col) in q:
            return col
    return None


def reply_with_specific_medicine_column(question: str, medicines_df: pd.DataFrame, min_score: int = 35, language: str = "ar") -> Optional[Dict[str, Any]]:
    requested_col = detect_requested_column(question, medicines_df)
    if not requested_col:
        return None
    if is_hidden_medical_column(requested_col):
        return {"reply": dosage_reply(language), "intent": "medicine_specific_column", "matches_count": 0, "model": GEMINI_MODEL}
    matches = [item for item in find_best_matches(question, medicines_df, top_k=1) if item["score"] >= min_score]
    if not matches:
        return {"reply": not_found_reply("medicine", language), "intent": "medicine_specific_column", "matches_count": 0, "model": GEMINI_MODEL}
    row = user_safe_row(matches[0]["row"])
    medicine_name = safe_get(row, "اسم الدواء", "الدواء")
    value = row.get(requested_col, "")
    if is_empty_value(value):
        if language == "en":
            reply = f"I found **{display_value(medicine_name, language)}**, but **{label_for(requested_col, language)}** is not recorded in the available medicines file."
        else:
            reply = f"لقيت **{medicine_name}**، لكن خانة **{requested_col}** مش متسجلة في ملف الأدوية المتاح."
        return {"reply": reply, "intent": "medicine_specific_column", "matches_count": 1, "model": GEMINI_MODEL, "primary_medicine": str(medicine_name)}
    shown_name = display_value(medicine_name, language)
    shown_value = display_value(value, language)
    if language == "en":
        reply = f"I found this medicine in the available data.\n\n**{shown_name}**\n**{label_for(requested_col, language)}:** {shown_value}"
        reply += "\n\n**Important Note**\nThis information is for guidance only and does not replace advice from a doctor or pharmacist."
    else:
        reply = f"{random.choice(MEDICINE_OPENINGS)}\n\n**{medicine_name}**\n**{requested_col}:** {shown_value}"
        reply += f"\n\n**تنبيه مهم**\n{random.choice(MEDICAL_DISCLAIMERS)}"
    return {"reply": reply, "intent": "medicine_specific_column", "matches_count": 1, "model": GEMINI_MODEL, "primary_medicine": str(medicine_name)}


def format_medicine_reply(row: pd.Series, language: str = "ar") -> str:
    row = user_safe_row(row)
    values = values_for_language(
        {
            "medicine": safe_get(row, "اسم الدواء"),
            "active": safe_get(row, "المادة الفعالة"),
            "category": safe_get(row, "التصنيف"),
            "usage": safe_get(row, "الاستخدام"),
            "form": safe_get(row, "الشكل الدوائي"),
            "warning": safe_get(row, "أهم التحذيرات"),
            "side_effects": safe_get(row, "الأعراض الجانبية", ""),
            "dispensing": safe_get(row, "حالة الصرف", ""),
            "notes": safe_get(row, "ملاحظات", ""),
        },
        language,
    )
    summary = markdown_list(
        [
            ("اسم الدواء", values["medicine"]),
            ("المادة الفعالة", values["active"]),
            ("التصنيف", values["category"]),
            ("الشكل الدوائي", values["form"]),
            ("حالة الصرف", values["dispensing"]),
        ],
        language=language,
    )
    reply = "I found this medicine in the available file. Here is a clear summary." if language == "en" else random.choice(MEDICINE_OPENINGS)
    if summary:
        reply += f"\n\n**{'Summary' if language == 'en' else 'الخلاصة'}**\n{summary}"
    if has_text(values["usage"]):
        reply += f"\n\n**{'General Use' if language == 'en' else 'الاستخدام العام'}**\n{values['usage']}"
    if has_text(values["warning"]):
        reply += f"\n\n**{'Key Warning' if language == 'en' else 'خد بالك'}**\n{values['warning']}"
    if has_text(values["side_effects"]):
        reply += f"\n\n**{'Common Side Effects' if language == 'en' else 'أعراض جانبية شائعة'}**\n{values['side_effects']}"
    if has_text(values["notes"]):
        reply += f"\n\n**{'Additional Note' if language == 'en' else 'ملاحظة إضافية'}**\n{values['notes']}"
    if language == "en":
        reply += "\n\n**Important Note**\nThis information is for guidance only and does not replace advice from a doctor or pharmacist."
    else:
        reply += f"\n\n**تنبيه مهم**\n{random.choice(MEDICAL_DISCLAIMERS)}"
    return reply


def format_medicine_comparison_reply(rows: List[pd.Series], language: str = "ar") -> str:
    clean_rows = [user_safe_row(row) for row in rows[:2]]
    first, second = clean_rows[0], clean_rows[1]
    first_name = safe_get(first, "اسم الدواء", "الدواء الأول")
    second_name = safe_get(second, "اسم الدواء", "الدواء الثاني")
    fields = [
        ("المادة الفعالة", "المادة الفعالة"),
        ("التصنيف", "التصنيف"),
        ("الاستخدام", "الاستخدام العام"),
        ("الشكل الدوائي", "الشكل الدوائي"),
        ("حالة الصرف", "حالة الصرف"),
        ("أهم التحذيرات", "أهم التحذيرات"),
        ("الأعراض الجانبية", "الأعراض الجانبية الشائعة"),
        ("ملاحظات", "ملاحظات"),
    ]
    table_rows = []
    for col, label in fields:
        first_value = safe_get(first, col)
        second_value = safe_get(second, col)
        if has_text(first_value) or has_text(second_value):
            table_rows.append([label, first_value, second_value])
    table = markdown_table(["البند", first_name, second_name], table_rows, language=language)
    if language == "en":
        return "\n".join(
            [
                "Here is a structured comparison from the available medicines file, without choosing what is medically best for you.",
                "",
                f"**Comparison Between {display_value(first_name, language)} and {display_value(second_name, language)}**",
                "",
                table,
                "",
                "**Safe Summary**",
                "Which one is more suitable depends on your health condition, age, other medicines, allergies, and chronic diseases. Ask a doctor or pharmacist before using either medicine.",
            ]
        )
    return "\n".join(
        [
            "تمام، دي مقارنة منظمة من ملف الأدوية، من غير ما أختار الأفضل طبيًا.",
            "",
            f"**المقارنة بين {first_name} و {second_name}**",
            "",
            table,
            "",
            "**الخلاصة الآمنة**",
            "مين الأنسب لك يعتمد على حالتك الصحية، سنك، الأدوية التانية، والحساسية أو الأمراض المزمنة. اسأل طبيب أو صيدلي قبل الاستخدام.",
        ]
    )


def format_doctor_reply(row: pd.Series, language: str = "ar") -> str:
    row = user_safe_row(row)
    raw_values = {
        "doctor": safe_get(row, "اسم الدكتور"),
        "specialty": safe_get(row, "التخصص"),
        "day": safe_get(row, "اليوم"),
        "start": safe_get(row, "من"),
        "end": safe_get(row, "إلى", safe_get(row, "الى")),
        "slots": safe_get(row, "عدد المواعيد المتاحة", ""),
        "price": safe_get(row, "سعر الكشف", ""),
        "place": safe_get(row, "المكان", ""),
        "notes": safe_get(row, "ملاحظات", ""),
    }
    values = values_for_language(raw_values, language)
    appointment = markdown_list(
        [
            ("الدكتور", values["doctor"]),
            ("التخصص", values["specialty"]),
            ("اليوم", values["day"]),
            ("من", values["start"]),
            ("إلى", values["end"]),
        ],
        language=language,
    )
    details = markdown_list(
        [
            ("سعر الكشف", values["price"]),
            ("المكان", values["place"]),
            ("المواعيد المتاحة", values["slots"]),
            ("ملاحظات", values["notes"]),
        ],
        language=language,
    )
    reply = "I found a suitable option in the schedule sent by the backend." if language == "en" else random.choice(DOCTOR_OPENINGS)
    if appointment:
        reply += f"\n\n**{'Available Appointment' if language == 'en' else 'الموعد المتاح'}**\n{appointment}"
    if details:
        reply += f"\n\n**{'Extra Details' if language == 'en' else 'تفاصيل إضافية'}**\n{details}"
    if language == "en":
        reply += "\n\nIf this appointment does not suit you, send another day or specialty and I will check the schedule."
    else:
        reply += "\n\nلو الموعد مش مناسب، اكتب اليوم أو التخصص اللي يناسبك وأنا أشوف أقرب اختيار من الجدول."
    return reply


def is_probably_incomplete_reply(text: str, draft_reply: str) -> bool:
    stripped = text.strip()
    if len(stripped) < 60:
        return True
    if len(stripped) < max(120, int(len(draft_reply) * 0.45)):
        return True
    return False


def has_too_much_wrong_language(text: str, language: str) -> bool:
    arabic = len(re.findall(r"[\u0600-\u06FF]", text or ""))
    latin = len(re.findall(r"[A-Za-z]", text or ""))
    if language == "en":
        return arabic > 12 and arabic > latin * 0.08
    return latin > 60 and latin > arabic * 0.8


def polish_reply_with_gemini(
    user_question: str,
    intent: str,
    context: str,
    draft_reply: str,
    recent_questions: Optional[List[str]] = None,
    language: str = "ar",
) -> str:
    if _gemini_client is None:
        return draft_reply
    language_instruction = (
        "Write the full final answer in clear English. Translate any Arabic schedule/data values into English."
        if language == "en"
        else "اكتب الرد النهائي بالعربية المصرية الواضحة. ترجم أي قيم إنجليزية عادية إلى العربية، مع الحفاظ على أسماء الأشخاص والأدوية كما هي."
    )
    recent_context = "\n".join(f"- {q}" for q in (recent_questions or [])[:5]) or "لا يوجد"
    prompt = f"""
أنت {ASSISTANT_NAME if language == 'ar' else ASSISTANT_NAME_EN} داخل تطبيق {APP_NAME if language == 'ar' else APP_NAME_EN}.
مهمتك تحسين صياغة مسودة دقيقة فقط.

قواعد إلزامية:
- اعتمد فقط على المسودة والبيانات المتاحة.
- ممنوع تضيف دواء، دكتور، سعر، موعد، تشخيص، جرعة، مدة علاج، أو عدد أقراص غير مذكور.
- لو معلومة غير موجودة، قل إنها غير متاحة ولا تخمن.
- لا تحفظ أو تفترض وجود جدول دكاترة داخلي؛ الجدول الوحيد هو المرسل من الباك في هذا الطلب.
- {language_instruction}
- استخدم Markdown منظم ومختصر.
- لو السؤال طبي، أكد أن المعلومة إرشادية ولا تغني عن الطبيب/الصيدلي.

نوع السؤال: {intent}
سياق التاريخ: {today_context_text(language)}
آخر أسئلة للمرجعية الأسلوبية فقط:
{recent_context}
سؤال المستخدم:
{user_question[:350]}
البيانات المتاحة:
{context[:2400]}
المسودة الدقيقة:
{draft_reply[:1400]}

اكتب الرد النهائي فقط:
"""
    try:
        response = _gemini_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(temperature=0.25, max_output_tokens=900) if types is not None else None,
        )
        text = str(getattr(response, "text", "") or "").strip()
        if text and not is_probably_incomplete_reply(text, draft_reply) and not has_too_much_wrong_language(text, language):
            return guard_medical_reply(user_question, intent, text, draft_reply, language)
    except Exception as exc:  # pragma: no cover - external API is optional
        logger.warning("Gemini polishing failed: %s", exc)
    return draft_reply


def is_explicit_triage_interrupt(question: str, intent: str, named_medicine_matches: List[Dict[str, Any]], medicines_df: pd.DataFrame) -> bool:
    if intent == "doctor":
        return True
    if not named_medicine_matches:
        return False
    q = normalize_text(question)
    if q in {"لا", "مفيش", "نعم", "اه", "ايوه", "no", "none", "yes", "nope"}:
        return False
    direct_medicine_terms = ["استخدام", "تحذير", "اضرار", "اعراض جانبيه", "ماده فعاله", "قارن", "بديل", "دواء", "used for", "warning", "side effect", "active ingredient", "compare", "alternative", "medicine"]
    return len(q.split()) <= 3 or any(term in q for term in direct_medicine_terms) or detect_requested_column(question, medicines_df) is not None


def get_chatbot_reply(
    message: str,
    medicines_df: pd.DataFrame,
    doctors_df: pd.DataFrame,
    top_k: int = 3,
    min_score: int = 55,
    recent_questions: Optional[List[str]] = None,
    last_medicine: Optional[str] = None,
    preferred_language: Optional[str] = None,
    active_triage: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    user_question = str(message or "").strip()
    language = response_language(user_question, preferred_language)
    doctor_question = enrich_doctor_question_with_date(user_question)

    if not user_question:
        return {"reply": "Write your question first." if language == "en" else "اكتب سؤالك الأول.", "intent": "empty", "matches_count": 0, "model": GEMINI_MODEL}

    top_k = max(1, min(int(top_k), 10))
    min_score = max(0, min(int(min_score), 100))
    intent = detect_intent(user_question)
    contextual_question = user_question
    named_medicine_matches = find_named_medicine_matches(contextual_question, medicines_df, top_k=4)

    if last_medicine and not named_medicine_matches and is_contextual_medicine_followup(user_question):
        contextual_question = f"{last_medicine} {user_question}"
        named_medicine_matches = find_named_medicine_matches(contextual_question, medicines_df, top_k=4)
        if named_medicine_matches:
            intent = "medicine"

    if has_emergency_words(user_question):
        return {"reply": emergency_reply(language), "intent": "emergency", "matches_count": 0, "model": GEMINI_MODEL, "triage_state": None}

    if is_dosage_request(user_question):
        return {"reply": dosage_reply(language), "intent": "medicine_dosage_blocked", "matches_count": 0, "model": GEMINI_MODEL, "triage_state": None}

    if active_triage and active_triage.get("status") == "active":
        if is_triage_cancel_request(user_question):
            return {
                "reply": "Symptom assessment cancelled." if language == "en" else "تم إلغاء تقييم الأعراض.",
                "intent": "symptom_triage_cancelled",
                "matches_count": 0,
                "model": GEMINI_MODEL,
                "triage_state": None,
            }
        detected_now = detect_symptoms(user_question)
        current_symptom = str(active_triage.get("symptom", ""))
        if detected_now and detected_now[0] != current_symptom and is_symptom_message(user_question):
            new_state = start_triage(detected_now[0], language)
            return {"reply": triage_question_reply(new_state, language, initial=True), "intent": "symptom_triage", "matches_count": 0, "model": GEMINI_MODEL, "triage_state": new_state, "symptom_key": detected_now[0]}
        if not is_explicit_triage_interrupt(user_question, intent, named_medicine_matches, medicines_df):
            updated_state, complete = update_triage(active_triage, user_question)
            return {"reply": triage_question_reply(updated_state, language), "intent": "symptom_triage", "matches_count": 0, "model": GEMINI_MODEL, "triage_state": None if complete else updated_state, "symptom_key": current_symptom, "triage_complete": complete}

    if intent == "identity":
        reply = (
            f"I am {ASSISTANT_NAME_EN}, your digital assistant inside {APP_NAME_EN}. I can help with medicines from the loaded file and doctor appointments sent by the backend."
            if language == "en"
            else f"أنا {ASSISTANT_NAME} داخل تطبيق {APP_NAME}. أقدر أساعدك في معلومات الأدوية من ملف الداتا، ومواعيد الدكاترة اللي الباك بيبعتها في الطلب."
        )
        return {"reply": reply, "intent": "identity", "matches_count": 0, "model": GEMINI_MODEL}

    if intent == "greeting":
        reply = (
            f"Hi, I am {ASSISTANT_NAME_EN}. Send a medicine name or specialty and I will help."
            if language == "en"
            else f"أهلًا بيك، أنا {ASSISTANT_NAME}. ابعتلي اسم الدواء أو التخصص اللي بتدور عليه وأنا أساعدك."
        )
        return {"reply": reply, "intent": "greeting", "matches_count": 0, "model": GEMINI_MODEL}

    comparison_requested = asks_for_comparison(user_question)
    strong_comparison_requested = has_strong_comparison_word(user_question)
    if comparison_requested and last_medicine and len(named_medicine_matches) == 1:
        last_medicine_matches = find_named_medicine_matches(last_medicine, medicines_df, top_k=1)
        if last_medicine_matches and last_medicine_matches[0]["index"] != named_medicine_matches[0]["index"]:
            named_medicine_matches = last_medicine_matches + named_medicine_matches

    if comparison_requested and (strong_comparison_requested or len(named_medicine_matches) >= 2):
        if len(named_medicine_matches) >= 2:
            selected_rows = [item["row"] for item in named_medicine_matches[:2]]
            context = rows_to_context(named_medicine_matches[:2], max_rows=2)
            draft = format_medicine_comparison_reply(selected_rows, language=language)
            reply = polish_reply_with_gemini(user_question, "medicine_comparison", context, draft, recent_questions, language)
            return {"reply": reply, "intent": "medicine_comparison", "matches_count": 2, "model": GEMINI_MODEL, "comparison_medicines": [safe_get(row, "اسم الدواء", "") for row in selected_rows], "primary_medicine": safe_get(selected_rows[0], "اسم الدواء", "")}
        if len(named_medicine_matches) == 1:
            found_name = safe_get(named_medicine_matches[0]["row"], "اسم الدواء", "الدواء الأول")
            reply = f"I found **{display_value(found_name, language)}**, but I need the second medicine name to compare." if language == "en" else f"لقيت **{found_name}**، لكن محتاج اسم الدواء التاني عشان أقارن."
            return {"reply": reply, "intent": "medicine_comparison", "matches_count": 1, "model": GEMINI_MODEL}
        return {"reply": "Write the two medicine names you want to compare." if language == "en" else "اكتب اسم الدوائين اللي تحب أقارن بينهم.", "intent": "medicine_comparison", "matches_count": 0, "model": GEMINI_MODEL}

    if is_symptom_message(user_question) and not named_medicine_matches:
        detected = detect_symptoms(user_question)
        symptom_key = detected[0] if detected else "generic"
        triage_state = start_triage(symptom_key, language)
        return {"reply": triage_question_reply(triage_state, language, initial=True), "intent": "symptom_triage", "matches_count": 0, "model": GEMINI_MODEL, "triage_state": triage_state, "symptom_key": symptom_key}

    if intent == "unknown":
        medicine_matches = find_best_matches(contextual_question, medicines_df, top_k=top_k)
        doctor_matches = find_best_matches(doctor_question, doctors_df, top_k=top_k)
        best_medicine = medicine_matches[0]["score"] if medicine_matches else 0
        best_doctor = doctor_matches[0]["score"] if doctor_matches else 0
        if max(best_medicine, best_doctor) < min_score:
            return {"reply": unclear_reply(language), "intent": "unknown", "matches_count": 0, "model": GEMINI_MODEL}
        intent = "doctor" if best_doctor > best_medicine else "medicine"

    if intent == "doctor":
        if doctors_df is None or doctors_df.empty:
            reply = "No doctors schedule was sent by the backend in this request." if language == "en" else "مفيش جدول دكاترة وصل من الباك في الطلب ده."
            return {"reply": reply, "intent": "doctor", "matches_count": 0, "model": GEMINI_MODEL}
        filters = requested_doctor_filters(user_question)
        search_k = 10 if filters else top_k
        matches = [item for item in find_best_matches(doctor_question, doctors_df, top_k=search_k) if item["score"] >= min_score]
        if filters:
            matches = [item for item in matches if doctor_row_matches_filters(item["row"], filters)]
        matches = matches[:top_k]
        if not matches:
            return {"reply": not_found_reply("doctor", language), "intent": "doctor", "matches_count": 0, "model": GEMINI_MODEL}
        context = today_context_text(language) + "\n\n" + rows_to_context(matches, max_rows=top_k)
        draft = format_doctor_reply(matches[0]["row"], language=language)
        reply = polish_reply_with_gemini(user_question, "doctor", context, draft, recent_questions, language)
        return {"reply": reply, "intent": "doctor", "matches_count": len(matches), "model": GEMINI_MODEL}

    if intent == "medicine":
        if asks_for_alternatives(user_question) and "البدائل" not in medicines_df.columns:
            reply = "Alternative information is not available in the current medicines file." if language == "en" else "معلومة البدائل مش موجودة في ملف الأدوية الحالي."
            return {"reply": reply, "intent": "medicine", "matches_count": 0, "model": GEMINI_MODEL}
        specific = reply_with_specific_medicine_column(contextual_question, medicines_df, min_score=min_score, language=language)
        if specific is not None:
            specific["reply"] = polish_reply_with_gemini(user_question, "medicine_specific_column", specific["reply"], specific["reply"], recent_questions, language)
            return specific

    matches = [item for item in find_best_matches(contextual_question, medicines_df, top_k=top_k) if item["score"] >= min_score]
    if not matches:
        return {"reply": not_found_reply("medicine", language), "intent": "medicine", "matches_count": 0, "model": GEMINI_MODEL}
    context = rows_to_context(matches, max_rows=top_k)
    draft = format_medicine_reply(matches[0]["row"], language=language)
    reply = polish_reply_with_gemini(user_question, "medicine", context, draft, recent_questions, language)
    return {"reply": reply, "intent": "medicine", "matches_count": len(matches), "model": GEMINI_MODEL, "primary_medicine": safe_get(matches[0]["row"], "اسم الدواء", "")}


def build_suggestions(messages: List[Dict[str, str]], recent_questions: List[str], language: str, last_medicine: str = "", active_triage: Optional[Dict[str, Any]] = None) -> List[str]:
    return generate_dynamic_suggestions(messages, recent_questions, language=language, last_medicine=last_medicine, active_triage=active_triage, limit=4)
