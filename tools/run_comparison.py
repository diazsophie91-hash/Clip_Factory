"""Run deterministic local engine/profile comparisons on saved transcripts."""
import argparse
from pathlib import Path

from .validation_harness import DEFAULT_OUTPUT, TRANSCRIPTS, run_comparison, write_baseline_manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("transcripts", nargs="*", type=Path, help="Transcript JSON files; defaults to validation/transcripts/*_small.json")
    parser.add_argument("--min-duration", type=float, default=20)
    parser.add_argument("--max-duration", type=float, default=45)
    parser.add_argument("--limit", type=int, default=8)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--freeze-baseline", action="store_true", help="Create/update the input hash manifest for the saved reference outputs")
    args = parser.parse_args()
    paths = args.transcripts or sorted(TRANSCRIPTS.glob("*_small.json"))
    if args.freeze_baseline:
        write_baseline_manifest(paths, args.min_duration, args.max_duration, args.limit)
    result = run_comparison(paths, args.output, args.min_duration, args.max_duration, args.limit)
    print(f"Wrote {args.output}")
    print(f"sources/transcripts={len(result['runs'])}; profiles per input={len(result['runs'][0]['versions']) if result['runs'] else 0}")
    for run in result["runs"]:
        print(run["transcript"], run["source"])
        for version in run["versions"]:
            print("  {version}: raw={raw}, kept={kept}, reference_recall={recall}, human_reviews={reviews}".format(
                version=version["version"], raw=version.get("raw_candidate_count"),
                kept=version.get("kept_candidate_count"),
                recall=version["metrics"].get("independent_reference_recall"),
                reviews=version["metrics"].get("candidate_review_count")))


if __name__ == "__main__":
    main()
