"""
Smoke test for app/preprocessing.py.

Run with:
    python -m tests.test_preprocessing

This is a plain script (not pytest) so it works even before pytest is
set up properly on Day 14. It prints PASS/FAIL for each case so a
beginner can see exactly what worked.
"""

from app.preprocessing import preprocess

CASES = [
    "I want to reset my password, please help!",
    "Why was I charged twice for my last order?",
    "",
    "   ",
    "The app keeps crashing when I try to log in.",
]


def run():
    failures = 0

    for text in CASES:
        result = preprocess(text)
        print("-" * 60)
        print(f"ORIGINAL     : {text!r}")
        print(f"TOKENS       : {result.tokens}")
        print(f"LEMMAS       : {result.lemmas}")
        print(f"CLEANED      : {result.cleaned_tokens}")
        print(f"CLEANED TEXT : {result.cleaned_text!r}")

        # Basic sanity checks
        if result.original != text:
            print("FAIL: original text was modified!")
            failures += 1
        elif text.strip() == "" and result.cleaned_tokens:
            print("FAIL: empty input produced non-empty cleaned tokens!")
            failures += 1
        else:
            print("PASS")

    print("-" * 60)
    if failures == 0:
        print(f"ALL {len(CASES)} CASES PASSED")
    else:
        print(f"{failures} CASE(S) FAILED")


if __name__ == "__main__":
    run()
