import hashlib
import re
from typing import Any, Dict, List, Optional, Tuple


def normalize(text: Any) -> str:
    value = str(text or "").strip().lower()
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


SYMPTOM_ALIASES: Dict[str, List[str]] = {
    "headache": [
        "صداع", "وجع راس", "الم راس", "راسي بتوجعني",
        "headache", "head hurts", "head pain", "migraine",
    ],
    "fatigue": [
        "تعبان", "تعبانه", "تعب عام", "ارهاق", "مرهق", "مجهد", "خمول",
        "tired", "fatigue", "fatigued", "exhausted", "low energy", "weak all over",
    ],
    "dizziness": [
        "دوخه", "دوار", "حاسس اني هقع",
        "dizzy", "dizziness", "lightheaded", "vertigo",
    ],
    "fever": [
        "حراره", "سخونيه", "حمي",
        "fever", "high temperature", "temperature",
    ],
    "cough": [
        "كحه", "سعال",
        "cough", "coughing",
    ],
    "abdominal_pain": [
        "مغص", "وجع بطن", "الم بطن", "بطني بتوجعني",
        "stomach pain", "abdominal pain", "stomach ache", "belly pain",
    ],
    "diarrhea": [
        "اسهال", "براز سايل",
        "diarrhea", "diarrhoea", "loose stool", "loose stools",
    ],
    "allergy": [
        "حساسيه", "طفح", "حكه",
        "allergy", "allergic", "rash", "itching", "hives",
    ],
}


SYMPTOM_NAMES = {
    "headache": {"ar": "الصداع", "en": "headache"},
    "fatigue": {"ar": "التعب والإرهاق", "en": "fatigue"},
    "dizziness": {"ar": "الدوخة", "en": "dizziness"},
    "fever": {"ar": "الحرارة", "en": "fever"},
    "cough": {"ar": "الكحة", "en": "cough"},
    "abdominal_pain": {"ar": "ألم البطن", "en": "abdominal pain"},
    "diarrhea": {"ar": "الإسهال", "en": "diarrhea"},
    "allergy": {"ar": "الحساسية", "en": "allergy"},
}


COMMON_COMPLAINT_MARKERS = [
    "عندي", "حاسس", "حاسه", "اشعر", "اعاني", "تعبان", "تعبانه",
    "بيوجعني", "وجعني", "مش قادر", "مريض",
    "i have", "i feel", "i am", "i m", "suffering", "my", "feeling",
]


def _contains_alias(text: str, alias: str) -> bool:
    q = normalize(text)
    item = normalize(alias)
    if not item:
        return False
    if re.fullmatch(r"[a-z0-9 ]+", item):
        return bool(re.search(rf"\b{re.escape(item)}\b", q))
    return item in q


def detect_symptoms(text: str) -> List[str]:
    detected: List[str] = []
    for symptom, aliases in SYMPTOM_ALIASES.items():
        if any(_contains_alias(text, alias) for alias in sorted(aliases, key=len, reverse=True)):
            detected.append(symptom)
    return detected


def is_symptom_message(text: str) -> bool:
    detected = detect_symptoms(text)
    if not detected:
        return False
    q = normalize(text)
    has_marker = any(_contains_alias(q, marker) for marker in COMMON_COMPLAINT_MARKERS)
    return has_marker or len(q.split()) <= 7


DOSAGE_PATTERNS = [
    r"\b(dose|dosage|doseage)\b",
    r"\bhow\s+many(?:\s+\w+){0,4}\s+(tablets?|pills?|capsules?)\b",
    r"\bhow\s+much(?:\s+\w+){0,4}\s+(medicine|medication|syrup|ml)\b",
    r"\bhow\s+often\b",
    r"\bevery\s+how\s+many\s+hours\b",
    r"\bwhat\s+(?:is\s+the\s+)?(?:right\s+)?dose\b",
    r"\bcan\s+i\s+take\s+\d+",
    r"\b\d+\s*(?:mg|ml)\b",
    r"\bجرع(?:ه|ة)\b",
    r"\bكام(?:\s+\w+){0,4}\s+(?:قرص|كبسوله|كبسولة|مل)\b",
    r"\bكل\s+كام\s+ساعه\b",
    r"\bاخد(?:\s+\w+){0,3}\s+قد\s+ايه\b",
]


def is_dosage_request(text: str) -> bool:
    q = normalize(text)
    return any(re.search(pattern, q, flags=re.IGNORECASE) for pattern in DOSAGE_PATTERNS)


EMERGENCY_PHRASES = [
    "الم صدر", "ضيق تنفس", "مش قادر اتنفس", "اغماء", "فقدان وعي",
    "ضعف مفاجئ", "تنميل مفاجئ", "شلل", "لخبطه كلام", "تشنجات",
    "نزيف شديد", "صداع مفاجئ شديد", "اسوا صداع", "تيبس الرقبه",
    "تورم الوجه", "تورم الوش", "تورم الشفايف", "حساسيه شديده",
    "جرعه زياده", "جرعة زياده", "جرعه زائده", "جرعة زائدة", "تسمم",
    "طفل بلع", "بلع طفل", "زرقان", "قيء مستمر", "ترجيع مستمر",
    "chest pain", "shortness of breath", "cannot breathe", "fainting",
    "loss of consciousness", "sudden weakness", "sudden numbness",
    "seizure", "severe bleeding", "sudden severe headache", "worst headache",
    "face swelling", "lip swelling", "severe allergy", "overdose", "poisoning",
    "child swallowed", "blue lips", "persistent vomiting",
]


def has_emergency_signal(text: str) -> bool:
    q = normalize(text)
    return any(_contains_alias(q, phrase) for phrase in EMERGENCY_PHRASES)


TRIAGE_FLOWS: Dict[str, List[Dict[str, Any]]] = {
    "headache": [
        {
            "field": "onset",
            "ar": "الصداع بدأ من إمتى؟ وهل بدأ فجأة ولا بالتدريج؟",
            "en": "When did the headache start, and was the onset sudden or gradual?",
            "suggestions": {
                "ar": ["من ساعة وبالتدريج", "من امبارح", "بدأ فجأة دلوقتي"],
                "en": ["An hour ago, gradually", "Since yesterday", "It started suddenly just now"],
            },
        },
        {
            "field": "severity",
            "ar": "شدته كام من 1 لـ 10؟ وهل بيزيد؟",
            "en": "How severe is it from 1 to 10, and is it getting worse?",
            "suggestions": {
                "ar": ["3 من 10 وثابت", "6 من 10 وثابت", "9 من 10 وبيزيد"],
                "en": ["3 out of 10, stable", "6 out of 10, stable", "9 out of 10 and worsening"],
            },
        },
        {
            "field": "red_flags",
            "ar": "هل معاه زغللة شديدة، قيء متكرر، تنميل أو ضعف، سخونية مع تيبس رقبة، أو إغماء؟",
            "en": "Is there severe blurred vision, repeated vomiting, numbness or weakness, fever with a stiff neck, or fainting?",
            "suggestions": {
                "ar": ["لا، مفيش أي حاجة منهم", "في قيء أو زغللة", "في تنميل أو ضعف"],
                "en": ["No, none of these", "There is vomiting or blurred vision", "There is numbness or weakness"],
            },
        },
        {
            "field": "risk_factors",
            "ar": "هل عندك ضغط غير منتظم، حمل، إصابة حديثة في الرأس، أو بتاخد أدوية سيولة؟",
            "en": "Do you have uncontrolled blood pressure, pregnancy, a recent head injury, or take blood thinners?",
            "suggestions": {
                "ar": ["لا", "عندي ضغط", "باخد أدوية سيولة"],
                "en": ["No", "I have high blood pressure", "I take blood thinners"],
            },
        },
    ],
    "fatigue": [
        {
            "field": "onset",
            "ar": "التعب بدأ من إمتى؟ وهل هو مستمر ولا بييجي ويروح؟",
            "en": "When did the fatigue start, and is it constant or intermittent?",
            "suggestions": {
                "ar": ["من النهارده ومستمر", "من كام يوم", "بقاله أسابيع"],
                "en": ["Since today, constant", "For a few days", "For several weeks"],
            },
        },
        {
            "field": "severity",
            "ar": "التعب مأثر على نشاطك قد إيه من 1 لـ 10؟",
            "en": "How much is the fatigue affecting your activity from 1 to 10?",
            "suggestions": {
                "ar": ["3 من 10", "6 من 10", "9 من 10"],
                "en": ["3 out of 10", "6 out of 10", "9 out of 10"],
            },
        },
        {
            "field": "red_flags",
            "ar": "هل فيه ضيق نفس، ألم صدر، إغماء، نزيف، حرارة عالية، أو ضعف شديد مفاجئ؟",
            "en": "Is there shortness of breath, chest pain, fainting, bleeding, high fever, or sudden severe weakness?",
            "suggestions": {
                "ar": ["لا، مفيش", "في حرارة", "في ضيق نفس أو ألم صدر"],
                "en": ["No, none", "There is fever", "There is shortness of breath or chest pain"],
            },
        },
        {
            "field": "risk_factors",
            "ar": "نومك وأكلك وشرب المياه كويسين؟ وهل عندك مرض مزمن، حمل، أو أدوية ثابتة؟",
            "en": "Have sleep, food, and fluids been adequate? Do you have a chronic condition, pregnancy, or regular medicines?",
            "suggestions": {
                "ar": ["النوم والأكل كويسين ومفيش مرض مزمن", "نومي قليل", "عندي مرض مزمن أو أدوية ثابتة"],
                "en": ["Sleep and food are okay; no chronic condition", "I have not slept enough", "I have a chronic condition or regular medicines"],
            },
        },
    ],
    "dizziness": [
        {
            "field": "onset",
            "ar": "الدوخة بدأت من إمتى؟ وبتحصل مع الوقوف ولا حتى وإنت قاعد؟",
            "en": "When did the dizziness start, and does it happen on standing or even while sitting?",
            "suggestions": {
                "ar": ["من النهارده مع الوقوف", "مستمرة حتى وأنا قاعد", "بتيجي وتروح"],
                "en": ["Since today, on standing", "Constant even while sitting", "It comes and goes"],
            },
        },
        {
            "field": "severity",
            "ar": "شدتها كام من 1 لـ 10؟ وهل وقعت أو مش قادر تمشي بثبات؟",
            "en": "How severe is it from 1 to 10? Have you fallen or become unable to walk steadily?",
            "suggestions": {
                "ar": ["3 من 10 وبمشي عادي", "6 من 10", "شديدة ومش قادر أمشي بثبات"],
                "en": ["3 out of 10; walking normally", "6 out of 10", "Severe and I cannot walk steadily"],
            },
        },
        {
            "field": "red_flags",
            "ar": "هل معها ألم صدر، ضيق نفس، إغماء، ضعف أو تنميل مفاجئ، أو لخبطة كلام؟",
            "en": "Is there chest pain, shortness of breath, fainting, sudden weakness or numbness, or trouble speaking?",
            "suggestions": {
                "ar": ["لا، مفيش", "في إغماء أو ألم صدر", "في تنميل أو لخبطة كلام"],
                "en": ["No, none", "There is fainting or chest pain", "There is numbness or trouble speaking"],
            },
        },
        {
            "field": "risk_factors",
            "ar": "هل أكلت وشربت مياه كويس؟ وهل عندك ضغط، سكر، أنيميا، حمل، أو أدوية ثابتة؟",
            "en": "Have you eaten and had enough fluids? Do you have blood pressure problems, diabetes, anemia, pregnancy, or regular medicines?",
            "suggestions": {
                "ar": ["أكلت وشربت ومفيش أمراض", "مأكلتش أو شربت قليل", "عندي مرض مزمن"],
                "en": ["I ate and drank; no chronic condition", "I have not eaten or had enough fluids", "I have a chronic condition"],
            },
        },
    ],
}


GENERIC_TRIAGE_FLOW: List[Dict[str, Any]] = [
    {
        "field": "onset",
        "ar": "العرض بدأ من إمتى؟ وهل هو مستمر ولا بييجي ويروح؟",
        "en": "When did the symptom start, and is it constant or intermittent?",
        "suggestions": {
            "ar": ["من النهارده", "من كام يوم", "بقاله فترة"],
            "en": ["Since today", "For a few days", "For a while"],
        },
    },
    {
        "field": "severity",
        "ar": "شدته كام من 1 لـ 10؟ وهل بيزيد؟",
        "en": "How severe is it from 1 to 10, and is it getting worse?",
        "suggestions": {
            "ar": ["3 من 10 وثابت", "6 من 10 وثابت", "9 من 10 وبيزيد"],
            "en": ["3 out of 10, stable", "6 out of 10, stable", "9 out of 10 and worsening"],
        },
    },
    {
        "field": "red_flags",
        "ar": "هل فيه ألم صدر، ضيق نفس، إغماء، قيء مستمر، نزيف، أو ضعف شديد مفاجئ؟",
        "en": "Is there chest pain, shortness of breath, fainting, persistent vomiting, bleeding, or sudden severe weakness?",
        "suggestions": {
            "ar": ["لا، مفيش", "في قيء مستمر", "في ضيق نفس أو ألم صدر"],
            "en": ["No, none", "There is persistent vomiting", "There is shortness of breath or chest pain"],
        },
    },
    {
        "field": "risk_factors",
        "ar": "هل عندك مرض مزمن، حمل، حساسية أدوية، أو بتاخد أدوية ثابتة؟",
        "en": "Do you have a chronic condition, pregnancy, a medicine allergy, or regular medicines?",
        "suggestions": {
            "ar": ["لا", "عندي مرض مزمن", "باخد أدوية ثابتة"],
            "en": ["No", "I have a chronic condition", "I take regular medicines"],
        },
    },
]


FIELD_LABELS = {
    "onset": {"ar": "البداية والمدة", "en": "Onset and duration"},
    "severity": {"ar": "الشدة", "en": "Severity"},
    "red_flags": {"ar": "الأعراض المصاحبة المهمة", "en": "Important associated symptoms"},
    "risk_factors": {"ar": "الحالة الصحية والأدوية", "en": "Health conditions and medicines"},
}


def flow_for(symptom: str) -> List[Dict[str, Any]]:
    return TRIAGE_FLOWS.get(symptom, GENERIC_TRIAGE_FLOW)


def start_triage(symptom: str, language: str) -> Dict[str, Any]:
    return {
        "status": "active",
        "symptom": symptom,
        "language": language if language in {"ar", "en"} else "ar",
        "answers": {},
        "step": 0,
    }


def current_triage_item(state: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not state or state.get("status") != "active":
        return None
    flow = flow_for(str(state.get("symptom", "")))
    answers = state.get("answers", {})
    for index, item in enumerate(flow):
        if item["field"] not in answers:
            state["step"] = index
            return item
    return None


def _extract_explicit_severity(text: str, current_field: str) -> Optional[int]:
    q = normalize(text)
    severity_match = re.search(
        r"(?:شدته|شدتها|الشده|severity|pain|affecting)(?:\s+\w+){0,3}\s+(10|[1-9])\b",
        q,
    )
    if severity_match:
        return int(severity_match.group(1))
    out_of_ten = re.search(r"\b(10|[1-9])\s*(?:من|out of|/)\s*10\b", q)
    if out_of_ten:
        return int(out_of_ten.group(1))
    if current_field == "severity":
        standalone = re.search(r"\b(10|[1-9])\b", q)
        if standalone:
            return int(standalone.group(1))
    return None


def update_triage(state: Dict[str, Any], message: str) -> Tuple[Dict[str, Any], bool]:
    updated = {
        **state,
        "answers": dict(state.get("answers", {})),
    }
    item = current_triage_item(updated)
    if item is None:
        updated["status"] = "complete"
        return updated, True

    current_field = item["field"]
    updated["answers"][current_field] = str(message).strip()

    severity = _extract_explicit_severity(message, current_field)
    if severity is not None:
        updated["answers"]["severity"] = str(severity)

    q = normalize(message)
    if (
        severity is not None
        and current_field == "onset"
        and re.search(r"(?:^|\s)(?:لا|مفيش|no|none)(?:\s|$)", q)
    ):
        updated["answers"]["red_flags"] = (
            "No warning signs reported"
            if str(updated.get("language")) == "en"
            else "لا توجد أعراض تحذيرية مذكورة"
        )

    next_item = current_triage_item(updated)
    if next_item is None:
        updated["status"] = "complete"
        updated["step"] = len(flow_for(str(updated.get("symptom", ""))))
        return updated, True
    return updated, False


def triage_question_reply(
    state: Dict[str, Any],
    language: str,
    *,
    initial: bool = False,
) -> str:
    item = current_triage_item(state)
    symptom = str(state.get("symptom", ""))
    symptom_name = SYMPTOM_NAMES.get(symptom, {"ar": "العرض", "en": "the symptom"})[language]
    if item is None:
        return triage_summary_reply(state, language)

    if language == "en":
        opening = (
            f"Let’s assess {symptom_name} safely, one question at a time. I will not guess a diagnosis or medicine."
            if initial
            else "Got it. One more question:"
        )
        return (
            f"{opening}\n\n"
            f"**Question {int(state.get('step', 0)) + 1} of {len(flow_for(symptom))}**\n"
            f"{item['en']}\n\n"
            "_If any severe warning sign is present, do not wait for the rest of the questions—seek urgent medical care._"
        )

    opening = (
        f"خلينا نقيّم {symptom_name} بأمان، سؤال واحد كل مرة، من غير تشخيص أو ترشيح دواء بالتخمين."
        if initial
        else "تمام، سجلت إجابتك. سؤال كمان:"
    )
    return (
        f"{opening}\n\n"
        f"**السؤال {int(state.get('step', 0)) + 1} من {len(flow_for(symptom))}**\n"
        f"{item['ar']}\n\n"
        "_لو ظهر أي عرض خطير، متستناش باقي الأسئلة واتوجه للرعاية العاجلة._"
    )



NEGATIVE_WORDS = [
    "لا", "مفيش", "مافيش", "بدون", "no", "none", "not", "nothing",
    "لا يوجد", "مش موجود", "not present",
]

HIGH_RISK_CONTEXT_WORDS = [
    "حامل", "حمل", "مرضع", "رضاعه", "رضاعة", "طفل", "رضيع", "كبير سن", "مسن",
    "ضغط", "سكر", "قلب", "كلى", "كلي", "كبد", "ربو", "سيوله", "سيولة",
    "pregnant", "pregnancy", "breastfeeding", "child", "infant", "elderly",
    "blood pressure", "diabetes", "heart", "kidney", "liver", "asthma", "blood thinner",
]


def _answer_looks_negative(text: str) -> bool:
    q = normalize(text)
    if not q:
        return False
    return any(normalize(word) in q for word in NEGATIVE_WORDS)


def _answer_has_high_risk_context(text: str) -> bool:
    q = normalize(text)
    if not q:
        return False
    return any(normalize(word) in q for word in HIGH_RISK_CONTEXT_WORDS)


def _extract_severity_from_answers(answers: Dict[str, Any]) -> Optional[int]:
    severity_text = str(answers.get("severity", ""))
    severity_match = re.search(r"\b(10|[1-9])\b", normalize(severity_text))
    return int(severity_match.group(1)) if severity_match else None


def assess_triage_risk(state: Dict[str, Any]) -> Dict[str, str]:
    """Return a conservative risk level from the triage answers.

    This is not a diagnosis. It only decides how urgently the user should seek care.
    """
    answers = state.get("answers", {}) or {}
    joined_answers = " ".join(str(value or "") for value in answers.values())
    red_flags_text = str(answers.get("red_flags", ""))
    severity = _extract_severity_from_answers(answers)

    has_positive_red_flag = bool(red_flags_text.strip()) and not _answer_looks_negative(red_flags_text)
    emergency_signal = has_emergency_signal(joined_answers) and not _answer_looks_negative(joined_answers)
    high_risk_context = _answer_has_high_risk_context(joined_answers)

    if emergency_signal or has_positive_red_flag:
        return {
            "level": "emergency",
            "ar": "طارئة",
            "en": "Emergency",
            "reason_ar": "تم ذكر عرض تحذيري أو علامة خطورة في الإجابات.",
            "reason_en": "A warning sign or red-flag symptom was reported.",
        }
    if severity is not None and severity >= 8:
        return {
            "level": "high",
            "ar": "عالية",
            "en": "High",
            "reason_ar": "الشدة المذكورة عالية، لذلك الكشف في نفس اليوم أكثر أمانًا.",
            "reason_en": "The reported severity is high, so same-day medical assessment is safer.",
        }
    if high_risk_context:
        return {
            "level": "moderate",
            "ar": "متوسطة",
            "en": "Moderate",
            "reason_ar": "تم ذكر عامل صحي يحتاج حذرًا إضافيًا مثل حمل، طفل، مرض مزمن، أو أدوية ثابتة.",
            "reason_en": "A context needing extra caution was reported, such as pregnancy, child age, chronic disease, or regular medicines.",
        }
    return {
        "level": "low",
        "ar": "منخفضة مبدئيًا",
        "en": "Low initially",
        "reason_ar": "لم تظهر علامة طوارئ واضحة في الإجابات المسجلة.",
        "reason_en": "No clear emergency sign was identified in the recorded answers.",
    }


def triage_summary_reply(state: Dict[str, Any], language: str) -> str:
    symptom = str(state.get("symptom", ""))
    symptom_name = SYMPTOM_NAMES.get(symptom, {"ar": "العرض", "en": "the symptom"})[language]
    answers = state.get("answers", {})
    risk = assess_triage_risk(state)

    rows = []
    for item in flow_for(symptom):
        value = answers.get(item["field"])
        if value:
            label = FIELD_LABELS[item["field"]][language]
            rows.append(f"- **{label}:** {value}")

    summary_rows = chr(10).join(rows) if rows else ("- No answers recorded." if language == "en" else "- لم يتم تسجيل إجابات كافية.")

    if language == "en":
        if risk["level"] == "emergency":
            next_step = "Seek urgent medical care now. Do not wait for more chat questions."
        elif risk["level"] == "high":
            next_step = "Arrange a same-day medical assessment, especially if the symptom is worsening or unusual for you."
        elif risk["level"] == "moderate":
            next_step = "A medical or pharmacist review is safer before trying treatment, because extra caution factors were mentioned."
        else:
            next_step = "Monitor the symptom and arrange medical assessment if it persists, returns repeatedly, or gets worse."
        return (
            f"I have recorded your answers about {symptom_name}.\n\n"
            f"**Your summary**\n{summary_rows}\n\n"
            f"**Risk level**\n{risk['en']} — {risk['reason_en']}\n\n"
            f"**Safe next step**\n{next_step}\n\n"
            "This is a risk check, not a diagnosis. If chest pain, shortness of breath, fainting, sudden weakness/numbness, seizures, severe bleeding, overdose, poisoning, or a sudden very severe headache appears, seek emergency care immediately."
        )

    if risk["level"] == "emergency":
        next_step = "اتوجه للرعاية العاجلة أو الطوارئ الآن، وما تستناش باقي الأسئلة."
    elif risk["level"] == "high":
        next_step = "الأفضل كشف طبي في نفس اليوم، خصوصًا لو العرض بيزيد أو غير معتاد بالنسبة لك."
    elif risk["level"] == "moderate":
        next_step = "الأفضل مراجعة طبيب أو صيدلي قبل تجربة علاج، لأن في عوامل تحتاج حذر إضافي."
    else:
        next_step = "راقب العرض، ولو استمر أو بيتكرر أو زاد، الأفضل تعمل كشف طبي."
    return (
        f"سجلت إجاباتك عن {symptom_name}.\n\n"
        f"**ملخص إجاباتك**\n{summary_rows}\n\n"
        f"**مستوى الخطورة**\n{risk['ar']} — {risk['reason_ar']}\n\n"
        f"**الخطوة الآمنة التالية**\n{next_step}\n\n"
        "ده تقييم خطورة مبدئي مش تشخيص. لو ظهر ألم صدر، ضيق نفس، إغماء، ضعف أو تنميل مفاجئ، تشنجات، نزيف شديد، جرعة زائدة، تسمم، أو صداع مفاجئ شديد جدًا: اتوجه للطوارئ فورًا."
    )

def triage_suggestions(state: Dict[str, Any], language: str) -> List[str]:
    item = current_triage_item(state)
    if not item:
        return []
    suggestions = list(item["suggestions"][language])
    suggestions.append("Cancel symptom check" if language == "en" else "إلغاء تقييم الأعراض")
    return suggestions


def is_triage_cancel_request(text: str) -> bool:
    q = normalize(text)
    phrases = [
        "الغاء تقييم الاعراض", "الغاء التقييم", "وقف الاسئله", "مش عايز اكمل",
        "cancel symptom check", "cancel assessment", "stop questions", "stop assessment",
    ]
    return any(normalize(item) in q for item in phrases)


def _latest_symptom_from_history(user_messages: List[str]) -> Optional[str]:
    for message in reversed(user_messages):
        detected = detect_symptoms(message)
        if detected:
            return detected[0]
    return None


def generate_dynamic_suggestions(
    messages: List[Dict[str, str]],
    recent_questions: List[str],
    *,
    language: str,
    last_medicine: str = "",
    active_triage: Optional[Dict[str, Any]] = None,
    limit: int = 4,
) -> List[str]:
    if active_triage and active_triage.get("status") == "active":
        return triage_suggestions(active_triage, language)[:limit]

    user_messages = [
        str(item.get("content", ""))
        for item in messages
        if item.get("role") == "user" and str(item.get("content", "")).strip()
    ]
    history = user_messages + list(recent_questions or [])
    history_normalized = {normalize(item) for item in history}
    latest_symptom = _latest_symptom_from_history(user_messages)
    combined = " ".join(normalize(item) for item in user_messages[-8:])
    candidates: List[str] = []

    if last_medicine:
        comparison_target = (
            "Brufen"
            if "panadol" in normalize(last_medicine) or "باندول" in normalize(last_medicine)
            else "Panadol"
        )
        if language == "en":
            candidates.extend([
                f"What is {last_medicine} used for?",
                f"What are the warnings for {last_medicine}?",
                f"What are the side effects of {last_medicine}?",
                f"Compare {last_medicine} and {comparison_target}",
            ])
        else:
            candidates.extend([
                f"{last_medicine} بيستخدم في إيه؟",
                f"إيه تحذيرات {last_medicine}؟",
                f"إيه الأعراض الجانبية لـ {last_medicine}؟",
                f"قارن بين {last_medicine} و{comparison_target}",
            ])

    if latest_symptom:
        symptom_name = SYMPTOM_NAMES[latest_symptom][language]
        if language == "en":
            candidates.extend([
                f"I still have {symptom_name}",
                "Internal medicine appointment",
                "I have a different symptom",
            ])
        else:
            candidates.extend([
                f"لسه عندي {symptom_name}",
                "ميعاد دكتور باطنة",
                "عندي عرض مختلف",
            ])

    if any(word in combined for word in ["appointment", "doctor", "ميعاد", "موعد", "دكتور"]):
        candidates.extend(
            ["Appointment tomorrow", "Pediatrician appointment", "Dermatologist appointment"]
            if language == "en"
            else ["ميعاد دكتور بكرة", "ميعاد دكتور أطفال", "ميعاد دكتور جلدية"]
        )

    if any(word in combined for word in ["compare", "قارن", "مقارنه"]):
        candidates.extend(
            ["Compare Panadol and Brufen", "What are Brufen warnings?"]
            if language == "en"
            else ["قارن بين باندول وبروفين", "إيه تحذيرات بروفين؟"]
        )

    fallback = (
        [
            "I have a headache",
            "I feel tired",
            "Compare Panadol and Brufen",
            "Internal medicine appointment",
            "What are Panadol warnings?",
            "Pediatrician appointment",
        ]
        if language == "en"
        else [
            "عندي صداع",
            "حاسس بتعب وإرهاق",
            "قارن بين باندول وبروفين",
            "ميعاد دكتور باطنة",
            "إيه تحذيرات باندول؟",
            "ميعاد دكتور أطفال",
        ]
    )

    digest_source = "|".join(normalize(item) for item in history[-10:]) or language
    offset = int(hashlib.sha256(digest_source.encode("utf-8")).hexdigest()[:8], 16) % len(fallback)
    rotated_fallback = fallback[offset:] + fallback[:offset]
    candidates.extend(rotated_fallback)

    result: List[str] = []
    seen = set()
    for candidate in candidates:
        normalized = normalize(candidate)
        if not normalized or normalized in seen or normalized in history_normalized:
            continue
        seen.add(normalized)
        result.append(candidate)
        if len(result) >= limit:
            break
    return result
