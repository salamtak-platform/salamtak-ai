from __future__ import annotations

import logging
import re
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field

from salamtak_core import (
    APP_NAME_EN,
    GEMINI_MODEL,
    build_suggestions,
    clean_dataframe,
    get_chatbot_reply,
    load_medicines_data,
    normalize_text,
    standardize_doctors_schedule,
)


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(APP_NAME_EN)


app = FastAPI(
    title=f"{APP_NAME_EN} Chatbot API",
    description="Salamtak AI chatbot API. patientId is used as the session id.",
    version="2.4.0",
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


try:
    MEDICINES_DF, MEDICINES_SOURCE = load_medicines_data()
except Exception as exc:
    logger.exception("Failed to load medicines data: %s", exc)
    MEDICINES_DF = clean_dataframe(pd.DataFrame())
    MEDICINES_SOURCE = "not-loaded"
    STARTUP_ERROR = str(exc)
else:
    STARTUP_ERROR = ""


def infer_language_from_message(message: str) -> Literal["ar", "en"]:
    arabic_chars = re.findall(r"[\u0600-\u06FF]", message or "")
    latin_chars = re.findall(r"[A-Za-z]", message or "")

    if arabic_chars and len(arabic_chars) >= len(latin_chars):
        return "ar"

    return "en"


def has_value(value: Any) -> bool:
    return value is not None and str(value).strip() not in {"", "None", "null", "nan"}


def compact_dict(row: Dict[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in row.items() if has_value(value)}


def normalize_backend_doctors_context(
    doctors_context: Optional[List[Dict[str, Any]]]
) -> List[Dict[str, Any]]:
    normalized_rows: List[Dict[str, Any]] = []

    for doctor in doctors_context or []:
        if not isinstance(doctor, dict):
            continue

        doctor_name = (
            doctor.get("doctor_name")
            or doctor.get("doctorName")
            or doctor.get("name")
            or doctor.get("اسم الدكتور")
        )

        specialty = (
            doctor.get("specialty")
            or doctor.get("speciality")
            or doctor.get("التخصص")
        )

        base_day = (
            doctor.get("day")
            or doctor.get("weekday")
            or doctor.get("date")
            or doctor.get("اليوم")
        )

        base_start_time = (
            doctor.get("start_time")
            or doctor.get("startTime")
            or doctor.get("from")
            or doctor.get("من")
        )

        base_end_time = (
            doctor.get("end_time")
            or doctor.get("endTime")
            or doctor.get("to")
            or doctor.get("إلى")
            or doctor.get("الى")
        )

        base_slots = (
            doctor.get("available_slots")
            or doctor.get("availableSlots")
            or doctor.get("slots")
            or doctor.get("عدد المواعيد المتاحة")
        )

        base_location = (
            doctor.get("location")
            or doctor.get("place")
            or doctor.get("address")
            or doctor.get("المكان")
        )

        base_notes = doctor.get("notes") or doctor.get("ملاحظات")

        base_price = (
            doctor.get("price")
            or doctor.get("fee")
            or doctor.get("fees")
            or doctor.get("consultation_fee")
            or doctor.get("سعر الكشف")
        )

        flat_row = compact_dict({
            "doctor_name": doctor_name,
            "specialty": specialty,
            "day": base_day,
            "start_time": base_start_time,
            "end_time": base_end_time,
            "available_slots": base_slots,
            "price": base_price,
            "location": base_location,
            "notes": base_notes,
        })

        if len(flat_row) > 2:
            normalized_rows.append(flat_row)

        online = doctor.get("onlineClinicDetails") or doctor.get("online_clinic_details")

        if isinstance(online, dict):
            online_fee = (
                online.get("fees")
                or online.get("fee")
                or online.get("price")
                or online.get("consultationFee")
            )

            call_duration = (
                online.get("callDurationInMinutes")
                or online.get("call_duration_minutes")
                or online.get("duration")
            )

            online_notes = ["Online consultation"]

            if has_value(call_duration):
                online_notes.append(f"Call duration: {call_duration} minutes")

            if has_value(base_notes):
                online_notes.append(str(base_notes))

            online_row = compact_dict({
                "doctor_name": doctor_name,
                "specialty": specialty,
                "day": base_day,
                "start_time": base_start_time,
                "end_time": base_end_time,
                "available_slots": base_slots,
                "price": online_fee,
                "location": "Online clinic",
                "notes": "; ".join(online_notes),
                "consultation_type": "online",
            })

            if len(online_row) > 2:
                normalized_rows.append(online_row)

        physical = doctor.get("physicalClinicDetails") or doctor.get("physical_clinic_details")

        if isinstance(physical, dict):
            physical_fee = (
                physical.get("appointmentFees")
                or physical.get("fees")
                or physical.get("fee")
                or physical.get("price")
            )

            booking_type = (
                physical.get("typeOfBooking")
                or physical.get("bookingType")
            )

            physical_location = (
                physical.get("location")
                or physical.get("address")
                or base_location
                or "Physical clinic"
            )

            physical_notes = ["Physical clinic"]

            if has_value(booking_type):
                physical_notes.append(f"Booking type: {booking_type}")

            if has_value(base_notes):
                physical_notes.append(str(base_notes))

            physical_row = compact_dict({
                "doctor_name": doctor_name,
                "specialty": specialty,
                "day": base_day,
                "start_time": base_start_time,
                "end_time": base_end_time,
                "available_slots": base_slots,
                "price": physical_fee,
                "location": physical_location,
                "notes": "; ".join(physical_notes),
                "consultation_type": "physical",
            })

            if len(physical_row) > 2:
                normalized_rows.append(physical_row)

    return normalized_rows


class ChatRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    patient_id: str = Field(..., alias="patientId")
    message: str = Field(..., min_length=1)
    is_new_chat: bool = Field(default=False, alias="isNewChat")
    doctors_context: List[Dict[str, Any]] = Field(default_factory=list, alias="doctorsContext")

    language: Optional[Literal["ar", "en"]] = None
    min_score: int = Field(default=55, ge=0, le=100)
    top_k: int = Field(default=3, ge=1, le=10)


class ChatResponse(BaseModel):
    patientId: str
    session_id: str
    reply: str
    language: Literal["ar", "en"]
    intent: str
    matches_count: int
    model: str
    suggestions: List[str]
    doctor_recommendations: List[Dict[str, Any]] = Field(default_factory=list)
    state: Dict[str, Any]


class ClearSessionResponse(BaseModel):
    patientId: str
    session_id: str
    cleared: bool


class SessionStateStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._sessions: Dict[str, Dict[str, Any]] = {}

    def get(self, patient_id: str) -> Dict[str, Any]:
        with self._lock:
            if patient_id not in self._sessions:
                self._sessions[patient_id] = {
                    "messages": [],
                    "recent_questions": [],
                    "last_medicine": "",
                    "last_symptom": "",
                    "active_triage": None,
                    # Cached per-session doctor schedule. It is filled when the backend
                    # sends doctors_schedule for this session, reused on later messages,
                    # and deleted when the session is closed.
                    "doctors_schedule": [],
                    "doctors_schedule_updated_at": None,
                }

            return self._sessions[patient_id]

    def update_doctors_schedule(
        self,
        patient_id: str,
        doctors_schedule: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Cache the latest doctors schedule for this session only.

        The schedule is intentionally not persisted to a database. It lives only in
        memory and is removed with the rest of the session when /close is called.
        If the backend sends an empty list, the previous cached schedule is
        kept so follow-up doctor questions can still work during the same session.
        """
        with self._lock:
            state = self.get(patient_id)

            if doctors_schedule:
                state["doctors_schedule"] = doctors_schedule
                state["doctors_schedule_updated_at"] = datetime.now(timezone.utc).isoformat()

            return state

    def update_after_turn(
        self,
        patient_id: str,
        question: str,
        reply: str,
        result: Dict[str, Any],
    ) -> Dict[str, Any]:
        with self._lock:
            state = self.get(patient_id)

            state["messages"].append({
                "role": "user",
                "content": question,
            })

            state["messages"].append({
                "role": "assistant",
                "content": reply,
            })

            normalized_question = normalize_text(question)

            recent_questions = [
                item
                for item in state["recent_questions"]
                if normalize_text(item) != normalized_question
            ]

            state["recent_questions"] = [question] + recent_questions[:7]

            if result.get("primary_medicine"):
                state["last_medicine"] = str(result["primary_medicine"])

            if result.get("symptom_key"):
                state["last_symptom"] = str(result["symptom_key"])

            if "triage_state" in result:
                state["active_triage"] = result["triage_state"]
            elif state.get("active_triage") and result.get("intent") != "symptom_triage":
                state["active_triage"] = None

            return state

    def clear(self, patient_id: str) -> bool:
        with self._lock:
            existed = patient_id in self._sessions
            self._sessions.pop(patient_id, None)
            return existed


SESSION_STORE = SessionStateStore()


@app.post("/chat", response_model=ChatResponse)
def chat(payload: ChatRequest) -> ChatResponse:
    if STARTUP_ERROR:
        raise HTTPException(
            status_code=500,
            detail=f"Medicines data failed to load: {STARTUP_ERROR}",
        )

    patient_id = payload.patient_id

    language: Literal["ar", "en"] = (
        payload.language
        or infer_language_from_message(payload.message)
    )

    if payload.is_new_chat:
        SESSION_STORE.clear(patient_id)

    state = SESSION_STORE.get(patient_id)

    incoming_doctors_schedule = normalize_backend_doctors_context(payload.doctors_context)

    if incoming_doctors_schedule:
        state = SESSION_STORE.update_doctors_schedule(
            patient_id,
            incoming_doctors_schedule,
        )

    cached_doctors_schedule = state.get("doctors_schedule", [])
    doctors_df = standardize_doctors_schedule(cached_doctors_schedule)

    result = get_chatbot_reply(
        message=payload.message,
        medicines_df=MEDICINES_DF,
        doctors_df=doctors_df,
        top_k=payload.top_k,
        min_score=payload.min_score,
        recent_questions=state.get("recent_questions", []),
        last_medicine=state.get("last_medicine", ""),
        preferred_language=language,
        active_triage=state.get("active_triage"),
    )

    reply = str(result.get("reply", ""))

    new_state = SESSION_STORE.update_after_turn(
        patient_id,
        payload.message,
        reply,
        result,
    )

    suggestions = build_suggestions(
        new_state.get("messages", []),
        new_state.get("recent_questions", []),
        language,
        last_medicine=new_state.get("last_medicine", ""),
        active_triage=new_state.get("active_triage"),
    )

    return ChatResponse(
        patientId=patient_id,
        session_id=patient_id,
        reply=reply,
        language=language,
        intent=str(result.get("intent", "unknown")),
        matches_count=int(result.get("matches_count", 0) or 0),
        model=str(result.get("model", GEMINI_MODEL)),
        suggestions=suggestions,
        doctor_recommendations=[],
        state={
            "last_medicine": new_state.get("last_medicine", ""),
            "last_symptom": new_state.get("last_symptom", ""),
            "active_triage": new_state.get("active_triage"),
            "recent_questions": new_state.get("recent_questions", []),
            "doctors_schedule_cached_count": len(new_state.get("doctors_schedule", [])),
            "doctors_schedule_updated_at": new_state.get("doctors_schedule_updated_at"),
        },
    )


@app.post("/sessions/{patient_id}/close", response_model=ClearSessionResponse)
def close_session(patient_id: str) -> ClearSessionResponse:
    return ClearSessionResponse(
        patientId=patient_id,
        session_id=patient_id,
        cleared=SESSION_STORE.clear(patient_id),
            )
