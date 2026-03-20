import csv
import re
from datetime import datetime, date, time, timedelta, timezone
from urllib.parse import urlparse

import requests
from deep_translator import GoogleTranslator


BEARER_TOKEN = "null"
BASE_URL = "https://api.x.com/2"


def extract_username(profile_input: str) -> str:
    value = profile_input.strip()

    if not value:
        raise ValueError("Profile link/username cannot be empty.")

    if value.startswith("@"):
        value = value[1:]

    if "://" not in value and "/" not in value and "." not in value:
        if not re.fullmatch(r"[A-Za-z0-9_]{1,15}", value):
            raise ValueError("That does not look like a valid X username.")
        return value

    if "://" not in value:
        value = "https://" + value

    parsed = urlparse(value)
    path_parts = [p for p in parsed.path.split("/") if p]

    if not path_parts:
        raise ValueError("Could not find a username in that profile link.")

    username = path_parts[0].lstrip("@")

    if not re.fullmatch(r"[A-Za-z0-9_]{1,15}", username):
        raise ValueError("Could not extract a valid X username from that link.")

    return username


def parse_date(date_text: str) -> date:
    try:
        return datetime.strptime(date_text.strip(), "%Y-%m-%d").date()
    except ValueError:
        raise ValueError("Dates must be in YYYY-MM-DD format.")


def make_utc_bounds(start_date: date, end_date: date):
    start_dt = datetime.combine(start_date, time.min, tzinfo=timezone.utc)
    end_exclusive = datetime.combine(end_date + timedelta(days=1), time.min, tzinfo=timezone.utc)
    return start_dt, end_exclusive


def iso_z(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_x_datetime(dt_text: str) -> datetime:
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(dt_text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    raise ValueError(f"Could not parse X timestamp: {dt_text}")


def get_headers():
    if not BEARER_TOKEN:
        raise RuntimeError("Missing bearer token.")
    return {"Authorization": f"Bearer {BEARER_TOKEN}"}


def x_get(path: str, params=None):
    url = f"{BASE_URL}{path}"
    response = requests.get(url, headers=get_headers(), params=params, timeout=30)

    if response.status_code != 200:
        try:
            details = response.json()
        except Exception:
            details = response.text
        raise RuntimeError(f"X API error {response.status_code}: {details}")

    return response.json()


def get_user_id(username: str) -> str:
    data = x_get(f"/users/by/username/{username}")
    if "data" not in data or "id" not in data["data"]:
        raise RuntimeError("Could not find that user.")
    return data["data"]["id"]


def get_post_text(post: dict) -> str:
    note_tweet = post.get("note_tweet")
    if isinstance(note_tweet, dict) and note_tweet.get("text"):
        return note_tweet["text"]
    return post.get("text", "")


def translate_to_english(text: str, source_lang: str = None) -> str:
    text = text.strip()
    if not text:
        return text

    if source_lang == "en":
        return text

    try:
        return GoogleTranslator(source="auto", target="en").translate(text)
    except Exception:
        return text


def format_sheet_date(dt: datetime) -> str:
    return f"{dt.month}/{dt.day}/{str(dt.year)[-2:]}"


def fetch_user_posts(user_id: str, username: str, start_dt: datetime, end_exclusive: datetime,
                     political_figure: str, post_type: str):
    rows = []
    next_token = None

    while True:
        params = {
            "max_results": 100,
            "start_time": iso_z(start_dt),
            "end_time": iso_z(end_exclusive),
            "tweet.fields": "created_at,text,lang,note_tweet",
        }

        if next_token:
            params["pagination_token"] = next_token

        payload = x_get(f"/users/{user_id}/tweets", params=params)

        for post in payload.get("data", []):
            created_at = parse_x_datetime(post["created_at"])

            if not (start_dt <= created_at < end_exclusive):
                continue

            text = get_post_text(post).replace("\r\n", "\n").replace("\r", "\n")
            translated_text = translate_to_english(text, post.get("lang"))
            translated_text = translated_text.replace("\n", " ").strip()

            rows.append({
                "date": format_sheet_date(created_at),
                "political_figure": political_figure,
                "post_type": post_type,
                "content": translated_text,
                "post_url": f"https://x.com/{username}/status/{post['id']}",
                "sort_dt": created_at,
            })

        next_token = payload.get("meta", {}).get("next_token")
        if not next_token:
            break

    rows.sort(key=lambda row: row["sort_dt"])
    return rows


def write_csv(rows, filename: str):
    with open(filename, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)

        for row in rows:
            writer.writerow([
                row["date"],
                row["political_figure"],
                row["post_type"],
                row["content"],
                row["post_url"],
            ])


def default_filename(username: str, start_date: date, end_date: date) -> str:
    return f"{username}_{start_date.isoformat()}_{end_date.isoformat()}.csv"


def main():
    print("=== X Post Exporter ===\n")

    profile_input = input("Profile link or username: ").strip()
    political_figure = input("Political figure name to display: ").strip()
    post_type = input("Post type to display: ").strip()
    start_input = input("Start date (YYYY-MM-DD): ").strip()
    end_input = input("End date (YYYY-MM-DD): ").strip()

    username = extract_username(profile_input)
    start_date = parse_date(start_input)
    end_date = parse_date(end_input)

    if end_date < start_date:
        raise ValueError("End date must be on or after the start date.")

    output_file = input("Output CSV filename (press Enter for default): ").strip()
    if not output_file:
        output_file = default_filename(username, start_date, end_date)

    start_dt, end_exclusive = make_utc_bounds(start_date, end_date)

    user_id = get_user_id(username)
    posts = fetch_user_posts(
        user_id,
        username,
        start_dt,
        end_exclusive,
        political_figure,
        post_type
    )
    write_csv(posts, output_file)

    print("\nDone.")
    print(f"Posts exported: {len(posts)}")
    print(f"Saved CSV: {output_file}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped by user.")
    except Exception as e:
        print(f"\nError: {e}")
