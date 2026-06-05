import os
import json
import time
import smtplib
from email.mime.text import MIMEText
from datetime import datetime, timedelta, timezone

import arxiv
from anthropic import Anthropic

# ---------- CONFIG ----------
CATEGORIES = "cat:physics.acc-ph OR cat:physics.plasm-ph OR cat:physics.optics"
LOOKBACK_DAYS = 2          # wider window so we never miss late-announced papers
MAX_RESULTS = 200
MODEL = "claude-haiku-4-5-20251001"

SEEN_FILE = "/Users/raymondli/Agents/seen_ids.json"

RESEARCH_FOCUS = (
    "laser plasma accelerators (LPAs / LWFA): laser wakefield acceleration, "
    "plasma-based electron/positron acceleration, high-intensity laser-plasma "
    "interactions for particle acceleration, betatron radiation from plasma "
    "accelerators, plasma injectors, staging, and related diagnostics."
)

# Email settings
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
EMAIL_FROM = os.environ["EMAIL_FROM"]
EMAIL_TO = os.environ["EMAIL_TO"]
EMAIL_APP_PASSWORD = os.environ["EMAIL_APP_PASSWORD"]

client = Anthropic()  # reads ANTHROPIC_API_KEY from environment

arxiv_client = arxiv.Client(
    page_size=100,
    delay_seconds=10.0,
    num_retries=3,
)


# ---------- SEEN-IDS TRACKING ----------
def load_seen():
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE) as f:
            return set(json.load(f))
    return set()


def save_seen(seen):
    with open(SEEN_FILE, "w") as f:
        json.dump(list(seen), f)


# ---------- 1. FETCH (with backoff) ----------
def fetch_recent_papers(max_attempts=6):
    search = arxiv.Search(
        query=CATEGORIES,
        sort_by=arxiv.SortCriterion.SubmittedDate,
        max_results=MAX_RESULTS,
    )
    cutoff = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)

    for attempt in range(max_attempts):
        try:
            papers = []
            for result in arxiv_client.results(search):
                print("Fetched paper:", result.title)
                if result.published < cutoff:
                    break
                papers.append(result)
            return papers
        except Exception as e:
            if "429" in str(e) and attempt < max_attempts - 1:
                wait = 2 ** attempt * 30  # 30s, 60s, 120s, ...
                print(f"429 received, waiting {wait}s before retry...")
                time.sleep(wait)
            else:
                raise
    return []


# ---------- 2. CLASSIFY ----------
def classify(paper):
    prompt = f"""You are sorting new arxiv papers for a researcher whose field is:

{RESEARCH_FOCUS}

Classify the paper below into exactly one category:
- "relevant": directly about laser plasma accelerators or core sub-topics.
- "mildly relevant": adjacent work that might be useful (e.g., general plasma
  physics, laser tech, or accelerator methods not specific to LPAs).
- "not relevant": unrelated to the researcher's focus.

Respond in EXACTLY this format and nothing else:
CATEGORY: <relevant|mildly relevant|not relevant>
REASON: <one short sentence>

Title: {paper.title}
Abstract: {paper.summary}"""

    msg = client.messages.create(
        model=MODEL,
        max_tokens=150,
        messages=[{"role": "user", "content": prompt}],
    )
    text = msg.content[0].text.strip()

    category, reason = "not relevant", ""
    for line in text.splitlines():
        if line.upper().startswith("CATEGORY:"):
            category = line.split(":", 1)[1].strip().lower()
        elif line.upper().startswith("REASON:"):
            reason = line.split(":", 1)[1].strip()
    return category, reason


# ---------- 3. BUILD DIGEST ----------
def build_digest(buckets, total_new, total_window):
    order = ["relevant", "mildly relevant", "not relevant"]
    headers = {
        "relevant": "🟢 RELEVANT",
        "mildly relevant": "🟡 MILDLY RELEVANT",
        "not relevant": "⚪ NOT RELEVANT",
    }
    lines = [
        f"arXiv LPA digest — {datetime.now().strftime('%Y-%m-%d')}",
        f"{total_new} new papers (of {total_window} in lookback window)",
        "",
    ]
    for cat in order:
        items = buckets.get(cat, [])
        lines.append(f"{headers[cat]} ({len(items)})")
        lines.append("=" * 50)
        for paper, reason in items:
            authors = ", ".join(a.name for a in paper.authors[:3])
            if len(paper.authors) > 3:
                authors += " et al."
            lines.append(f"• {paper.title}")
            lines.append(f"  {authors}")
            lines.append(f"  {paper.entry_id}")
            lines.append(f"  Why: {reason}")
            lines.append("")
        lines.append("")
    return "\n".join(lines)


# ---------- 4. SEND EMAIL ----------
def send_email(body):
    msg = MIMEText(body)
    msg["Subject"] = f"arXiv LPA digest — {datetime.now().strftime('%Y-%m-%d')}"
    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(EMAIL_FROM, EMAIL_APP_PASSWORD)
        server.send_message(msg)


# ---------- MAIN ----------
def main():
    seen = load_seen()
    papers = fetch_recent_papers()
    new_papers = [p for p in papers if p.entry_id not in seen]
    print(f"Fetched {len(papers)} papers, {len(new_papers)} are new since last run.")

    buckets = {"relevant": [], "mildly relevant": [], "not relevant": []}
    for paper in new_papers:
        category, reason = classify(paper)
        if category not in buckets:
            category = "not relevant"
        buckets[category].append((paper, reason))

    if new_papers:
        digest = build_digest(buckets, len(new_papers), len(papers))
        send_email(digest)
        print(f"Sent digest covering {len(new_papers)} new papers "
              f"(of {len(papers)} in window).")
    else:
        print(f"No new papers since last run ({len(papers)} in window, all seen).")
    print("Categorized papers")

    # Update seen with everything currently in the window
    seen.update(p.entry_id for p in papers)
    save_seen(seen)


if __name__ == "__main__":
    main()