"""Verify that the generated activity JSON matches the activity database."""

import json
import sqlite3
from contextlib import closing
from pathlib import Path


def verify_activity_data(db_path: Path, json_path: Path) -> tuple[int, int]:
    """Return matching counts for activities exported by Generator.load()."""
    try:
        with closing(
            sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
        ) as db:
            db_ids = {
                row[0]
                for row in db.execute(
                    "SELECT run_id FROM activities WHERE distance > 0.1"
                )
            }
    except sqlite3.Error as exc:
        raise ValueError("activity database is missing or unreadable") from exc

    try:
        activities = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("activity JSON is missing or invalid") from exc

    if not isinstance(activities, list):
        raise TypeError("activity JSON must contain a list")

    json_count = len(activities)
    try:
        json_ids = {activity["run_id"] for activity in activities}
    except (KeyError, TypeError) as exc:
        raise ValueError("activity JSON entries are missing run_id") from exc

    db_count = len(db_ids)
    if db_count == 0 or json_count == 0 or db_count != json_count or db_ids != json_ids:
        raise ValueError("activity database and JSON counts are empty or inconsistent")

    return db_count, json_count


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    db_path = repo_root / "run_page" / "data.db"
    json_path = repo_root / "src" / "static" / "activities.json"
    try:
        db_count, json_count = verify_activity_data(db_path, json_path)
    except (TypeError, ValueError) as exc:
        raise SystemExit(f"Activity data verification failed: {exc}") from exc

    print(f"Verified activities: database={db_count}, json={json_count}")


if __name__ == "__main__":
    main()
