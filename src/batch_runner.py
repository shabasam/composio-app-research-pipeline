import argparse
import os
import random
from typing import Dict, List, Optional

from src.common import (
    IMPORTANT_FIELDS,
    apply_verification,
    find_by_app,
    load_json,
    save_json,
)
from src.models import AppResearch
from src.researcher import Researcher
from src.verifier import Verifier


DEFAULT_APPS = "data/apps.json"
RESEARCH_FILE = "data/research_raw.json"
VERIFICATION_FILE = "data/verification.json"
VERIFIED_FILE = "data/verified_results.json"
LOG_FILE = "data/verification_log.json"


def parse_args():
    p = argparse.ArgumentParser(description="Composio App Research Pipeline")
    p.add_argument("--apps-file", default=DEFAULT_APPS)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--verify-sample", type=int, default=15)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--research-only", action="store_true")
    p.add_argument("--verify-only", action="store_true")
    p.add_argument("--reset", action="store_true")
    return p.parse_args()


def empty_verification_status():
    return {field: "UNVERIFIED" for field in IMPORTANT_FIELDS}


def find_research_record(
    research_records: List[dict],
    app_name: str,
) -> Optional[dict]:
    """
    Find a research record using the canonical app name.

    Exact match is preferred. As a fallback, tolerate a researcher-added
    parenthetical alias such as:
        "NotebookLM (now Gemini Notebook)"
    for canonical input:
        "NotebookLM"
    """
    exact = find_by_app(research_records, app_name)
    if exact:
        return exact

    canonical = app_name.strip()

    for record in research_records:
        record_name = str(record.get("app", "")).strip()

        if record_name.startswith(canonical + " ("):
            return record

    return None


def canonicalize_research_record(
    research_record: dict,
    canonical_app_name: str,
) -> dict:
    """
    Return a copy of the research record with the canonical app name restored.

    This prevents researcher-added aliases/rebrands from breaking the
    verification lookup and final merge.
    """
    record = dict(research_record)
    record["app"] = canonical_app_name
    return record


def choose_verification_sample(
    apps: List[dict],
    research_records: List[dict],
    n: int,
    seed: int,
):
    """
    Adaptive sample:
      - up to half from lowest-confidence/uncertain records
      - rest random

    This avoids verifying only easy apps while preserving a reproducible
    sample.
    """
    if n <= 0 or not apps:
        return set()

    research_map = {}

    for record in research_records:
        app_name = record.get("app")
        if app_name:
            research_map[app_name] = record

    ranked = sorted(
        apps,
        key=lambda x: float(
            research_map.get(x["app"], {}).get("confidence", 0.0)
        ),
    )

    adaptive_n = min(max(1, n // 2), len(apps))
    selected = ranked[:adaptive_n]
    selected_names = {x["app"] for x in selected}

    remaining = [
        x for x in apps
        if x["app"] not in selected_names
    ]

    rng = random.Random(seed)
    rng.shuffle(remaining)

    selected.extend(
        remaining[: max(0, n - len(selected))]
    )

    return {
        x["app"]
        for x in selected[:n]
    }


def load_or_empty(path: str):
    """
    Load an existing JSON file, or return [] when the file does not exist.
    """
    if not os.path.exists(path):
        return []

    try:
        data = load_json(path)
        return data if isinstance(data, list) else []
    except Exception as exc:
        print(f"WARNING: Could not load {path}: {exc}")
        return []


def main():
    args = parse_args()

    # ------------------------------------------------------------
    # Reset generated pipeline files when explicitly requested.
    # ------------------------------------------------------------
    if args.reset:
        for path in (
            RESEARCH_FILE,
            VERIFICATION_FILE,
            VERIFIED_FILE,
            LOG_FILE,
        ):
            if os.path.exists(path):
                os.remove(path)

    # ------------------------------------------------------------
    # Load app list
    # ------------------------------------------------------------
    apps = load_json(args.apps_file)

    if args.limit:
        apps = apps[:args.limit]

    # ------------------------------------------------------------
    # Load existing pipeline state
    # ------------------------------------------------------------
    research_records = load_or_empty(RESEARCH_FILE)
    verification_records = load_or_empty(VERIFICATION_FILE)
    verified_records = load_or_empty(VERIFIED_FILE)
    verification_log = load_or_empty(LOG_FILE)

    print("=" * 78)
    print("COMPOSIO APP RESEARCH PIPELINE")
    print(f"Apps: {len(apps)} | Verification sample: {args.verify_sample}")
    print("=" * 78)

    researcher = None if args.verify_only else Researcher()
    verifier = None if args.research_only else Verifier()

    # ============================================================
    # PASS 1: Research
    # ============================================================
    if not args.verify_only:
        for i, app_info in enumerate(apps, 1):
            name = app_info["app"]
            category = app_info["category"]
            website = app_info.get("website")

            # ----------------------------------------------------
            # Look for an existing valid research record.
            # ----------------------------------------------------
            existing = find_research_record(
                research_records,
                name,
            )

            if existing:
                try:
                    # Validate the existing record.
                    existing_obj = AppResearch.model_validate(existing)

                    # Canonicalize the app name so future stages use
                    # the exact name from apps.json.
                    if existing_obj.app != name:
                        existing = canonicalize_research_record(
                            existing_obj.model_dump(),
                            name,
                        )

                        research_records = [
                            (
                                existing
                                if r is existing_obj.model_dump()
                                else r
                            )
                            for r in research_records
                        ]

                        # Simpler and safer replacement by matching
                        # the original record.
                        replaced = False
                        new_records = []

                        for r in research_records:
                            if (
                                not replaced
                                and r.get("app") != name
                                and str(r.get("app", "")).startswith(name + " (")
                            ):
                                new_records.append(existing)
                                replaced = True
                            else:
                                new_records.append(r)

                        research_records = new_records

                        save_json(
                            RESEARCH_FILE,
                            research_records,
                        )

                    print(f"[{i}/{len(apps)}] skip research: {name}")
                    continue

                except Exception:
                    # Invalid existing record:
                    # remove it so the researcher can regenerate it.
                    research_records = [
                        r
                        for r in research_records
                        if r.get("app") != name
                        and not str(r.get("app", "")).startswith(name + " (")
                    ]

            # ----------------------------------------------------
            # Research app
            # ----------------------------------------------------
            try:
                result, _ = researcher.research_app(
                    name,
                    category,
                    website,
                )

                result_dict = result.model_dump()

                # IMPORTANT:
                # Never allow the researcher to change the canonical
                # application name provided by apps.json.
                result_dict["app"] = name

                # Remove any stale duplicate/alias record before append.
                research_records = [
                    r for r in research_records
                    if r.get("app") != name
                    and not str(r.get("app", "")).startswith(name + " (")
                ]

                research_records.append(result_dict)

                save_json(
                    RESEARCH_FILE,
                    research_records,
                )

                print(f"[{i}/{len(apps)}] researched: {name}")

            except Exception as exc:
                print(
                    f"[{i}/{len(apps)}] RESEARCH ERROR: "
                    f"{name}: {type(exc).__name__}: {exc}"
                )

    if args.research_only:
        print("Research-only run finished.")
        return

    # ============================================================
    # PASS 2: Choose independent verification sample
    # ============================================================
    sample_names = choose_verification_sample(
        apps,
        research_records,
        args.verify_sample,
        args.seed,
    )

    print("Verification sample:", sorted(sample_names))

    # ============================================================
    # PASS 3: Independent verification
    # ============================================================
    verified_map: Dict[str, dict] = {}

    for record in verified_records:
        app_name = record.get("app")
        if app_name:
            verified_map[app_name] = record

    verification_map: Dict[str, dict] = {}

    for record in verification_records:
        app_name = record.get("app")
        if app_name:
            verification_map[app_name] = record

    for name in sorted(sample_names):
        # Already successfully verified.
        if name in verification_map:
            continue

        # --------------------------------------------------------
        # Find canonical app metadata.
        # --------------------------------------------------------
        app_info = next(
            (x for x in apps if x["app"] == name),
            None,
        )

        # --------------------------------------------------------
        # IMPORTANT:
        # Use tolerant lookup in case the researcher renamed the app.
        # --------------------------------------------------------
        research = find_research_record(
            research_records,
            name,
        )

        if not app_info or not research:
            print(
                f"VERIFY SKIP {name}: "
                f"research record not found"
            )
            continue

        # --------------------------------------------------------
        # Restore canonical application name before verification.
        # --------------------------------------------------------
        research = canonicalize_research_record(
            research,
            name,
        )

        try:
            research_obj = AppResearch.model_validate(
                research
            )

            verification = verifier.verify(
                research_obj,
                app_info.get("website"),
            )

            verification_map[name] = verification

            verification_records = list(
                verification_map.values()
            )

            save_json(
                VERIFICATION_FILE,
                verification_records,
            )

            # ----------------------------------------------------
            # Apply verification to produce corrected final record.
            # ----------------------------------------------------
            final, corrections, uncertainties = apply_verification(
                research_obj,
                verification,
                scope="sample",
            )

            # Guarantee canonical app name in final output.
            final.app = name

            verified_map[name] = final.model_dump()

            # ----------------------------------------------------
            # Save verification log.
            # ----------------------------------------------------
            verification_log.append(
                {
                    "app": name,
                    "overall_verdict": verification.get(
                        "overall_verdict"
                    ),
                    "corrections": corrections,
                    "uncertainties": uncertainties,
                    "independent_source_count": len(
                        verification.get(
                            "independent_sources",
                            [],
                        )
                    ),
                    "mcp_official_source_indexes": verification.get(
                        "mcp_official_sources",
                        [],
                    ),
                    "mcp_third_party_source_indexes": verification.get(
                        "mcp_third_party_sources",
                        [],
                    ),
                }
            )

            save_json(
                VERIFIED_FILE,
                list(verified_map.values()),
            )

            save_json(
                LOG_FILE,
                verification_log,
            )

            print(
                f"verified sample: {name} | "
                f"verdict={verification.get('overall_verdict')} | "
                f"corrections={len(corrections)} | "
                f"unverified={len(uncertainties)}"
            )

        except Exception as exc:
            print(
                f"VERIFY ERROR {name}: "
                f"{type(exc).__name__}: {exc}"
            )

    # ============================================================
    # PASS 4: Build a complete final dataset
    #
    # Every researched app MUST appear in the final file.
    #
    # Sample-verified records:
    #     -> corrected using verification
    #
    # Non-sample / failed-verification records:
    #     -> retain research values
    #     -> explicitly marked UNVERIFIED
    # ============================================================
    for app_info in apps:
        name = app_info["app"]

        research = find_research_record(
            research_records,
            name,
        )

        if not research:
            print(
                f"FINAL FILE WARNING {name}: "
                f"no research record found"
            )
            continue

        # --------------------------------------------------------
        # Already have a verified/corrected record.
        # --------------------------------------------------------
        if name in verified_map:
            continue

        # --------------------------------------------------------
        # Canonicalize the research record before validation.
        # --------------------------------------------------------
        research = canonicalize_research_record(
            research,
            name,
        )

        try:
            result = AppResearch.model_validate(
                research
            )

            # Non-verified fields are explicitly marked.
            result.field_verification = empty_verification_status()
            result.verification_scope = "not_verified"

            # Guarantee canonical name.
            result.app = name

            verified_map[name] = result.model_dump()

        except Exception as exc:
            print(
                f"FINAL FILE ERROR {name}: "
                f"{type(exc).__name__}: {exc}"
            )

    # ============================================================
    # Final save
    # ============================================================
    final_records = list(verified_map.values())

    save_json(
        VERIFIED_FILE,
        final_records,
    )

    # ============================================================
    # Validation / diagnostics
    # ============================================================
    expected_names = {
        app["app"]
        for app in apps
    }

    actual_names = {
        record.get("app")
        for record in final_records
        if record.get("app")
    }

    missing_names = sorted(
        expected_names - actual_names
    )

    duplicate_names = sorted(
        {
            name
            for name in actual_names
            if sum(
                1
                for record in final_records
                if record.get("app") == name
            ) > 1
        }
    )

    print("\n" + "=" * 78)
    print("PIPELINE COMPLETE")
    print(f"Research records: {len(research_records)}")
    print(
        f"Independent verifications: "
        f"{len(verification_records)}"
    )
    print(
        f"Final dataset records: "
        f"{len(final_records)}"
    )

    if missing_names:
        print(
            f"WARNING: Missing final records ({len(missing_names)}): "
            f"{missing_names}"
        )
    else:
        print(
            f"Final dataset coverage: "
            f"{len(actual_names)}/{len(expected_names)} apps"
        )

    if duplicate_names:
        print(
            f"WARNING: Duplicate app names: "
            f"{duplicate_names}"
        )
    else:
        print("Duplicate app names: none")

    print("=" * 78)


if __name__ == "__main__":
    main()