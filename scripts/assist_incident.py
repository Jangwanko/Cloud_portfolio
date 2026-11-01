"""Current evidence + retrieved runbook assistance. No cluster writes or shell tools."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ops_agent.runbook_assistant import read_json, run
from ops_agent.diagnosis_agent import OpenAIResponsesClient, load_openai_configuration


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--bundle", type=Path)
    source.add_argument("--example", choices=["matching", "similar", "unmatched"], help="Included synthetic example")
    parser.add_argument("--step", type=int, default=0, help="Example snapshot index")
    parser.add_argument("--question")
    parser.add_argument("--provenance", choices=["synthetic", "recorded"])
    parser.add_argument("--previous", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--live", action="store_true", help="Use existing model configuration (bounded calls)")
    parser.add_argument("--investigate", action="store_true", help="Model chooses recorded observation reads and runbook search; up to five calls")
    parser.add_argument("--format", choices=["json","text"], default="json", help="Text displays model wording without editorial rewriting")
    args = parser.parse_args()
    if args.example:
        if args.provenance == "recorded":
            parser.error("included examples are synthetic")
        case = next(c for c in read_json(ROOT / "ops_agent/fixtures/runbook_assistance_v1.json")["cases"] if c["id"] == args.example)
        if not 0 <= args.step < len(case["steps"]):
            parser.error("invalid example step")
        document, question, provenance = case["steps"][args.step], args.question or case["question"], "synthetic"
    else:
        if not args.question or not args.provenance or args.step:
            parser.error("--bundle requires --question and --provenance; --step is for examples only")
        document, question, provenance = read_json(args.bundle), args.question, args.provenance
    client, model = None, None
    if args.live:
        key, model = load_openai_configuration(ROOT)
        client = OpenAIResponsesClient(api_key=key, timeout_seconds=45, max_retries=0)
    runner=run
    if args.investigate:
        from ops_agent.investigation_assistant import investigate
        runner=investigate
    result = runner(document, question, provenance, args.output_dir,
        previous=read_json(args.previous) if args.previous else None, client=client, model=model)
    if "advice" in result:
        if args.format=="text":
            from ops_agent.investigation_assistant import render_explanation
            print(render_explanation(result))
            return
        print(json.dumps({"observed_at": result["input"]["observed_at"],
            "provenance": result["input"]["provenance"], "advice": result["advice"], "changes": result["changes"],
            "runbook_sources": [{key: b[key] for key in ("id", "source_path", "source_heading", "source_sha256", "conditions")}
                                for b in result["input"]["runbooks"]]}, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
