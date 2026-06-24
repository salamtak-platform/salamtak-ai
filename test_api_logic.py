import unittest

from salamtak_core import get_chatbot_reply, load_medicines_data, standardize_doctors_schedule


class ApiLogicTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.medicines_df, _ = load_medicines_data()

    def test_doctor_schedule_comes_from_request_only(self):
        doctors_df = standardize_doctors_schedule([])
        result = get_chatbot_reply(
            "عايز ميعاد دكتور أطفال",
            medicines_df=self.medicines_df,
            doctors_df=doctors_df,
            preferred_language="ar",
        )
        self.assertEqual(result["intent"], "doctor")
        self.assertIn("مفيش جدول دكاترة", result["reply"])

    def test_accepts_english_doctor_schedule_and_arabic_reply(self):
        doctors_df = standardize_doctors_schedule([
            {
                "doctor_name": "Dr. Mona Khaled",
                "specialty": "Pediatrics",
                "day": "Monday",
                "start_time": "04:00 PM",
                "end_time": "07:00 PM",
                "available_slots": 6,
                "price": "250 EGP",
                "location": "Pediatrics clinic",
            }
        ])
        result = get_chatbot_reply(
            "عايز ميعاد دكتور أطفال",
            medicines_df=self.medicines_df,
            doctors_df=doctors_df,
            preferred_language="ar",
            min_score=35,
        )
        self.assertEqual(result["intent"], "doctor")
        self.assertGreaterEqual(result["matches_count"], 1)
        self.assertIn("الموعد", result["reply"])

    def test_english_language_is_forced_by_user_preference(self):
        result = get_chatbot_reply(
            "باندول بيستخدم في ايه",
            medicines_df=self.medicines_df,
            doctors_df=standardize_doctors_schedule([]),
            preferred_language="en",
            min_score=35,
        )
        self.assertIn(result["intent"], {"medicine", "medicine_specific_column"})
        self.assertIn("Important", result["reply"])


if __name__ == "__main__":
    unittest.main()

from fastapi.testclient import TestClient
from app import app, SESSION_STORE


class ApiSessionScheduleTests(unittest.TestCase):
    def setUp(self):
        SESSION_STORE.clear("session-cache-test")

    def test_doctor_schedule_is_cached_for_same_session_until_closed(self):
        client = TestClient(app)
        schedule = [
            {
                "doctor_name": "Dr. Mona Khaled",
                "specialty": "Pediatrics",
                "day": "Monday",
                "start_time": "04:00 PM",
                "end_time": "07:00 PM",
                "available_slots": 6,
                "price": "250 EGP",
                "location": "Pediatrics clinic",
            }
        ]

        first = client.post(
            "/chat",
            json={
                "session_id": "session-cache-test",
                "language": "ar",
                "message": "عايز دكتور أطفال",
                "doctors_schedule": schedule,
                "min_score": 35,
            },
        )
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["state"]["doctors_schedule_cached_count"], 1)

        second = client.post(
            "/chat",
            json={
                "session_id": "session-cache-test",
                "language": "ar",
                "message": "طب في نفس الدكتور تاني؟",
                "doctors_schedule": [],
                "min_score": 35,
            },
        )
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()["state"]["doctors_schedule_cached_count"], 1)

        closed = client.post("/sessions/session-cache-test/close")
        self.assertEqual(closed.status_code, 200)
        self.assertTrue(closed.json()["cleared"])

        third = client.post(
            "/chat",
            json={
                "session_id": "session-cache-test",
                "language": "ar",
                "message": "عايز دكتور أطفال",
                "doctors_schedule": [],
                "min_score": 35,
            },
        )
        self.assertEqual(third.status_code, 200)
        self.assertEqual(third.json()["state"]["doctors_schedule_cached_count"], 0)
