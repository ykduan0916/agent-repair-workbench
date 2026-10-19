"""MR-01: isolated, synthetic Cognee experiment; never a production entry point."""

import asyncio
import hashlib
import json
import os
import sqlite3
import traceback
from pathlib import Path


async def main() -> None:
    # The parent supplies an empty HOME, private output directory and loopback proxy.
    import cognee
    from cognee.api.v1.search import SearchType

    result: dict[str, object] = {"phase": "imported", "version": "1.6.5"}
    output = Path(os.environ["MR01_RESULT"])

    def save() -> None:
        output.write_text(json.dumps(result, indent=2))

    save()
    try:
        dataset = "mr01_synthetic"
        text = (
            "Synthetic repair experience. The count tool returned a string instead of an integer. "
            "Converting the count to an integer fixed the synthetic output contract test. "
            "This is synthetic test data, not a real user incident."
        )
        added = await cognee.add(text, dataset_name=dataset)
        result["phase"] = "added"
        # Do not serialize raw text, provider exceptions or arbitrary objects.
        result["add_return_type"] = type(added).__name__
        datasets = await cognee.datasets.list_datasets()
        target = next(item for item in datasets if item.name == dataset)
        data = await cognee.datasets.list_data(target.id)
        # Minimal laboratory authority, intentionally separate from Cognee and the product DB.
        authority = sqlite3.connect(output.parent / "lab-authority.sqlite")
        authority.execute(
            "CREATE TABLE memory (id TEXT, revision INTEGER, digest TEXT, state TEXT, "
            "dataset TEXT, data TEXT)"
        )
        for item in data:
            authority.execute(
                "INSERT INTO memory VALUES (?, ?, ?, ?, ?, ?)",
                (
                    "synthetic-memory",
                    1,
                    hashlib.sha256(text.encode()).hexdigest(),
                    "confirmed",
                    str(target.id),
                    str(item.id),
                ),
            )
        authority.commit()
        result["projection_ids"] = {
            "dataset_id": str(target.id),
            "data_ids": [str(item.id) for item in data],
        }
        save()
        processed = await cognee.cognify(datasets=[dataset], raise_on_error=True)
        result["cognify_return_type"] = type(processed).__name__
        result["phase"] = "cognified"
        save()
        found = await cognee.search(
            query_type=SearchType.RAG_COMPLETION,
            query_text="The count tool returns a string. What previous repair is relevant?",
            datasets=[dataset],
            only_context=True,
            verbose=True,
            include_references=True,
            top_k=3,
        )
        result["phase"] = "searched"
        result["search_return_type"] = type(found).__name__
        result["search_entries"] = len(found)
        result["evidence"] = []
        for entry in found:
            for ref in entry.get("evidence", []):
                result["evidence"].append(  # type: ignore[union-attr]
                    {
                        "kind": ref.get("kind"),
                        "artifact_id": ref.get("artifact_id"),
                        "dataset_id": ref.get("dataset_id"),
                        "data_id": ref.get("data_id"),
                        "source_ref_key": ref.get("source_ref_key"),
                    }
                )

        def eligible_count() -> int:
            return sum(
                authority.execute(
                    "SELECT count(*) FROM memory WHERE dataset=? AND data=? AND state='confirmed'",
                    (ref["dataset_id"], ref["data_id"]),
                ).fetchone()[0]
                for ref in result["evidence"]
            )

        result["mapped_evidence_before_delete"] = eligible_count()
        if not result["evidence"] or not result["mapped_evidence_before_delete"]:
            raise ValueError("missing_authority_mapping")
        # Replay the exact cached result after authority revocation, before physical cleanup.
        authority.execute("UPDATE memory SET state='deleted'")
        authority.commit()
        result["cached_evidence_after_tombstone"] = eligible_count()
        if result["cached_evidence_after_tombstone"] != 0:
            raise ValueError("stale_evidence_eligible")
        authority.close()
        save()
        await cognee.datasets.empty_dataset(target.id)
        remaining = await cognee.datasets.list_datasets()
        result["dataset_removed"] = all(item.id != target.id for item in remaining)
        if not result["dataset_removed"]:
            raise ValueError("dataset_still_present")
        result["phase"] = "completed"
    except Exception as error:
        result["error_type"] = type(error).__name__
        result["error_frames"] = [
            {"file": Path(frame.filename).name, "line": frame.lineno, "function": frame.name}
            for frame in traceback.extract_tb(error.__traceback__)[-5:]
        ]
    finally:
        save()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as error:
        Path(os.environ["MR01_RESULT"]).write_text(
            json.dumps(
                {
                    "phase": "startup",
                    "error_type": type(error).__name__,
                    "frames": [
                        {"file": Path(f.filename).name, "line": f.lineno, "function": f.name}
                        for f in traceback.extract_tb(error.__traceback__)[-5:]
                    ],
                }
            )
        )
