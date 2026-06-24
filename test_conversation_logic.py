import unittest

from conversation_logic import (
    assess_triage_risk,
    detect_symptoms,
    generate_dynamic_suggestions,
    is_dosage_request,
    is_symptom_message,
    start_triage,
    triage_suggestions,
    update_triage,
)


class ConversationLogicTests(unittest.TestCase):
    def test_detects_arabic_and_english_symptoms(self):
        self.assertEqual(detect_symptoms("I have a headache"), ["headache"])
        self.assertEqual(detect_symptoms("I feel tired and exhausted"), ["fatigue"])
        self.assertEqual(detect_symptoms("عندي صداع"), ["headache"])
        self.assertEqual(detect_symptoms("حاسس بتعب وإرهاق"), ["fatigue"])
        self.assertTrue(is_symptom_message("I have a headache"))
        self.assertTrue(is_symptom_message("تعبان ومجهد"))

    def test_dosage_detection_allows_words_between_how_many_and_tablets(self):
        self.assertTrue(is_dosage_request("How many Panadol tablets?"))
        self.assertTrue(is_dosage_request("How many Brufen pills can I take?"))
        self.assertTrue(is_dosage_request("جرعة باندول كام قرص؟"))
        self.assertFalse(is_dosage_request("What is Panadol used for?"))

    def test_triage_keeps_context_and_accepts_compound_answer(self):
        state = start_triage("headache", "ar")
        state, complete = update_triage(state, "بدأ من ساعة، شدته 6، لا")
        self.assertFalse(complete)
        self.assertEqual(state["answers"]["severity"], "6")
        self.assertIn("red_flags", state["answers"])
        self.assertEqual(state["step"], 3)

        state, complete = update_triage(state, "لا يوجد ضغط ولا أدوية سيولة")
        self.assertTrue(complete)
        self.assertEqual(state["status"], "complete")


    def test_triage_risk_levels_are_conservative(self):
        state = start_triage("headache", "ar")
        state, _ = update_triage(state, "من ساعة وبالتدريج")
        state, _ = update_triage(state, "9 من 10 وبيزيد")
        risk = assess_triage_risk(state)
        self.assertEqual(risk["level"], "high")

        emergency_state = start_triage("headache", "ar")
        emergency_state, _ = update_triage(emergency_state, "بدأ فجأة")
        emergency_state, _ = update_triage(emergency_state, "6 من 10")
        emergency_state, _ = update_triage(emergency_state, "في تنميل وضعف")
        emergency_risk = assess_triage_risk(emergency_state)
        self.assertEqual(emergency_risk["level"], "emergency")

    def test_triage_suggestions_change_with_current_question(self):
        state = start_triage("fatigue", "en")
        first = triage_suggestions(state, "en")
        state, _ = update_triage(state, "For a few days")
        second = triage_suggestions(state, "en")
        self.assertNotEqual(first, second)
        self.assertTrue(any("10" in item for item in second))

    def test_history_changes_suggested_questions(self):
        medicine_history = [
            {"role": "user", "content": "Tell me about Panadol"},
            {"role": "assistant", "content": "Medicine details"},
        ]
        symptom_history = [
            {"role": "user", "content": "I have a headache"},
            {"role": "assistant", "content": "When did it start?"},
        ]

        medicine_suggestions = generate_dynamic_suggestions(
            medicine_history,
            ["Tell me about Panadol"],
            language="en",
            last_medicine="Panadol",
        )
        symptom_suggestions = generate_dynamic_suggestions(
            symptom_history,
            ["I have a headache"],
            language="en",
        )

        self.assertTrue(any("Panadol" in item for item in medicine_suggestions))
        self.assertNotIn("Compare Panadol and Panadol", medicine_suggestions)
        self.assertTrue(any("headache" in item.lower() for item in symptom_suggestions))
        self.assertNotEqual(medicine_suggestions, symptom_suggestions)


if __name__ == "__main__":
    unittest.main()
