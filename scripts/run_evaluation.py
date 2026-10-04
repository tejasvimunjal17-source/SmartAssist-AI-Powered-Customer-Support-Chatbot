"""
Runs the full SmartAssist evaluation and writes:
    reports/evaluation_report.md
    reports/evaluation_results.json

Usage:
    python -m scripts.run_evaluation
    python -m scripts.run_evaluation --output-dir some/other/folder

For REAL retrieval and full-system intent numbers, run this on a machine
where you have installed the requirements and built the index:
    python -m scripts.build_index
    python -m scripts.run_evaluation

Anything that cannot be measured in the current environment is reported
as UNAVAILABLE / NO DATA — never as a made-up number.
"""

import argparse
import os

from app.config import BASE_DIR
from app.evaluation_report import write_reports
from app.evaluation_runner import run_all_evaluations


def main():
    parser = argparse.ArgumentParser(description="Run the SmartAssist evaluation and write report files.")
    parser.add_argument(
        "--output-dir",
        default=os.path.join(BASE_DIR, "reports"),
        help="Folder to write evaluation_report.md and evaluation_results.json into (default: reports/).",
    )
    args = parser.parse_args()

    results = run_all_evaluations()
    paths = write_reports(results, args.output_dir)

    intent = results["intent"]["metrics"]
    escalation = results["escalation"]["metrics"]
    print(f"Intent:      {intent['correct']}/{intent['total_samples']} correct — {results['intent']['label'][:60]}...")
    print(f"Retrieval:   {results['retrieval']['status']}" + (f" ({results['retrieval']['reason']})" if results["retrieval"]["reason"] else ""))
    print(f"Escalation:  {escalation['correct_decisions']}/{escalation['total_scenarios']} correct decisions")
    print(f"Feedback:    {results['feedback']['label']}")
    print(f"\nWrote {paths['markdown']}")
    print(f"Wrote {paths['json']}")


if __name__ == "__main__":
    main()
