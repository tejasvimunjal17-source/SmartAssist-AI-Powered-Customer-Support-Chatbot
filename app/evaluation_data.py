"""
Day 13: labeled evaluation datasets.

HONESTY NOTE: every example below was written by hand for this project.
None of it comes from real customers or production traffic, so any
metric computed from it measures "performance on this hand-written
evaluation set" — NOT real-world production performance. The examples
deliberately include some phrasings that the current keyword rules do
not cover (paraphrases, indirect wording), because an evaluation set
made only of messages the rules were designed around would say nothing
useful. No example was added, removed, or reworded after seeing results
in order to improve a score.

Datasets:
- INTENT_EVALUATION_DATASET: message -> expected intent (Day 7 router)
- ESCALATION_EVALUATION_SCENARIOS: message + FIXED synthetic intent
  confidence (+ optional recent history) -> expected escalate yes/no
  (Day 9 decision logic). Confidence is a fixed input on purpose, so the
  scenarios test the escalation DECISION logic deterministically,
  independent of whether the embedding model is installed.
- Retrieval evaluation reuses the 12 existing Day 5 sample queries in
  app/evaluation.py (SAMPLE_QUERIES) rather than duplicating them here.
"""

INTENT_EVALUATION_DATASET = [
    # --- greeting (10) ---
    {"text": "Hi", "expected_intent": "greeting"},
    {"text": "Hello!", "expected_intent": "greeting"},
    {"text": "Hey there", "expected_intent": "greeting"},
    {"text": "Good morning", "expected_intent": "greeting"},
    {"text": "Good evening, is anyone there?", "expected_intent": "greeting"},
    {"text": "Hello, I have a quick question", "expected_intent": "greeting"},
    {"text": "hey", "expected_intent": "greeting"},
    {"text": "Greetings", "expected_intent": "greeting"},
    {"text": "Hi SmartAssist", "expected_intent": "greeting"},
    {"text": "Good afternoon", "expected_intent": "greeting"},
    # --- faq (10) ---
    {"text": "How do I reset my password?", "expected_intent": "faq"},
    {"text": "What payment methods do you accept?", "expected_intent": "faq"},
    {"text": "How long does shipping take?", "expected_intent": "faq"},
    {"text": "Can I change my shipping address after ordering?", "expected_intent": "faq"},
    {"text": "What is your refund policy?", "expected_intent": "faq"},
    {"text": "Do you ship internationally?", "expected_intent": "faq"},
    {"text": "Where is my order?", "expected_intent": "faq"},
    {"text": "Which browsers do you support?", "expected_intent": "faq"},
    {"text": "What are your support hours?", "expected_intent": "faq"},
    {"text": "Is there a mobile app?", "expected_intent": "faq"},
    # --- complaint (10) ---
    {"text": "I'm very unhappy with the service I received", "expected_intent": "complaint"},
    {"text": "This is unacceptable", "expected_intent": "complaint"},
    {"text": "I am so disappointed with my order", "expected_intent": "complaint"},
    {"text": "Your support has been terrible", "expected_intent": "complaint"},
    {"text": "That was the worst experience I've ever had", "expected_intent": "complaint"},
    {"text": "I want to file a complaint about my delivery", "expected_intent": "complaint"},
    {"text": "I'm really frustrated with how this was handled", "expected_intent": "complaint"},
    {"text": "This is ridiculous, I paid for premium and got nothing", "expected_intent": "complaint"},
    {"text": "I expected much better from your company", "expected_intent": "complaint"},
    {"text": "Nobody replied to my emails, awful service", "expected_intent": "complaint"},
    # --- technical_issue (10) ---
    {"text": "The app keeps crashing when I open it", "expected_intent": "technical_issue"},
    {"text": "I can't log in to my account", "expected_intent": "technical_issue"},
    {"text": "The website won't load", "expected_intent": "technical_issue"},
    {"text": "I'm getting an error message when I try to pay", "expected_intent": "technical_issue"},
    {"text": "The page is not loading", "expected_intent": "technical_issue"},
    {"text": "My screen keeps freezing", "expected_intent": "technical_issue"},
    {"text": "There's a bug in the checkout page", "expected_intent": "technical_issue"},
    {"text": "The search feature isn't working", "expected_intent": "technical_issue"},
    {"text": "The upload button does nothing", "expected_intent": "technical_issue"},
    {"text": "I keep getting logged out every few minutes", "expected_intent": "technical_issue"},
    # --- escalation (10) ---
    {"text": "I want to talk to a human", "expected_intent": "escalation"},
    {"text": "Can I speak to a manager?", "expected_intent": "escalation"},
    {"text": "Connect me to an agent please", "expected_intent": "escalation"},
    {"text": "I need a real person to help me", "expected_intent": "escalation"},
    {"text": "Let me speak to someone", "expected_intent": "escalation"},
    {"text": "Please escalate this issue", "expected_intent": "escalation"},
    {"text": "Get me a human agent", "expected_intent": "escalation"},
    {"text": "I'd like to talk to your supervisor", "expected_intent": "escalation"},
    {"text": "Is there a live person I can chat with?", "expected_intent": "escalation"},
    {"text": "Transfer me to customer support staff", "expected_intent": "escalation"},
    # --- mixed messages: the more actionable intent is the expected label ---
    {"text": "Hi, the app keeps crashing", "expected_intent": "technical_issue"},
    {"text": "Hello, I want to speak to a manager", "expected_intent": "escalation"},
]


def _scenario(name, category, message, intent, confidence, expected_escalate,
              expected_reason=None, recent_user_messages=None):
    return {
        "name": name,
        "category": category,
        "message": message,
        "intent": intent,
        "confidence": confidence,
        "expected_escalate": expected_escalate,
        "expected_reason": expected_reason,
        "recent_user_messages": recent_user_messages,
    }


ESCALATION_EVALUATION_SCENARIOS = [
    # --- explicit human request: should always escalate ---
    _scenario("explicit_talk_to_human", "explicit_request", "I want to talk to a human", "escalation", 0.95, True, "explicit_human_request"),
    _scenario("explicit_connect_agent", "explicit_request", "Connect me to an agent please", "escalation", 0.95, True, "explicit_human_request"),
    _scenario("explicit_manager", "explicit_request", "Can I speak to a manager?", "escalation", 0.95, True, "explicit_human_request"),
    _scenario("explicit_paraphrase_live_person", "explicit_request", "Is there a live person I can chat with?", "escalation", 0.9, True, "explicit_human_request"),
    # --- low intent confidence: should escalate ---
    _scenario("low_confidence_vague", "low_confidence", "asdf blah thing happened", "unknown", 0.20, True, "low_intent_confidence"),
    _scenario("low_confidence_borderline", "low_confidence", "it did the weird thing again", "unknown", 0.45, True, "low_intent_confidence"),
    _scenario("low_confidence_near_threshold", "low_confidence", "can you look into my situation", "faq", 0.55, True, "low_intent_confidence"),
    # --- clear frustration (multiple signals): should escalate ---
    _scenario("frustration_three_signals", "frustration", "This is ridiculous, it's still not working!!", "complaint", 0.95, True, "frustration_detected"),
    _scenario("frustration_urgent_and_negative", "frustration", "This is terrible, I need this fixed right now!!", "complaint", 0.95, True, "frustration_detected"),
    _scenario("frustration_plus_low_confidence", "frustration", "This is useless, still broken!!", "unknown", 0.30, True, "multiple_escalation_signals"),
    # --- repeated failure across turns: should escalate ---
    _scenario(
        "repeated_failure_across_turns", "repeated_failure", "still not working",
        "technical_issue", 0.95, True, "frustration_detected",
        recent_user_messages=["the app is not working", "it is still broken, nothing changed", "still not working"],
    ),
    _scenario(
        "repeated_failure_with_negative_language", "repeated_failure", "this is terrible, it still doesn't work",
        "technical_issue", 0.95, True, "frustration_detected",
        recent_user_messages=["it doesn't work", "still not working at all"],
    ),
    # --- normal conversations: should NOT escalate ---
    _scenario("normal_greeting", "normal", "Hello there", "greeting", 0.95, False),
    _scenario("normal_faq", "normal", "How do I reset my password?", "faq", 0.95, False),
    _scenario("normal_single_technical_signal", "normal", "The app is not working, how do I fix it?", "technical_issue", 0.95, False),
    _scenario("normal_plain_complaint", "normal", "I'm unhappy with the delivery time", "complaint", 0.95, False),
    _scenario("normal_faq_semantic_confident", "normal", "What payment methods do you accept?", "faq", 0.82, False),
    _scenario("normal_thanks", "normal", "Thanks, that helped", "unknown", 0.90, False),
    # --- harder, implicit frustration (no defined keyword signals): a human would
    #     likely read these as frustrated. Included deliberately so the evaluation
    #     can reveal whether the keyword heuristic misses them. Labeled by policy
    #     judgment, not by what the current code does. ---
    _scenario("implicit_frustration_repeat_asking", "implicit_frustration", "I've asked three times and nobody has helped me!!", "complaint", 0.90, True),
    _scenario("implicit_frustration_giving_up", "implicit_frustration", "I give up, this is going nowhere", "complaint", 0.90, True),
]
