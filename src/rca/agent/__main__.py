"""CLI.

python -m rca.agent triage --incident ID [--llm anthropic|replay|none]
                           [--replay tests/fixtures/llm/x.json] [--record tests/fixtures/llm/x.json]
"""

import argparse
import sys
from pathlib import Path

from rca.agent.service import format_outcome, make_llm, triage_incident
from rca.config import get_settings
from rca.observability.logging import configure_logging


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m rca.agent")
    sub = parser.add_subparsers(dest="command", required=True)
    t = sub.add_parser("triage", help="investigate one incident and store the RCA")
    t.add_argument("--incident", type=int, required=True)
    t.add_argument("--llm", choices=["anthropic", "replay", "none"], default="anthropic")
    t.add_argument("--replay", type=Path, help="transcript to replay (with --llm replay)")
    t.add_argument("--record", type=Path, help="record the live run to this transcript")
    args = parser.parse_args()

    settings = get_settings()
    configure_logging("ERROR")
    llm = make_llm(settings, args.llm, replay=args.replay, record=args.record)
    print(format_outcome(triage_incident(settings, args.incident, llm)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
