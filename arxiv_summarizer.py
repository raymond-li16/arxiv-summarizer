import os
import smtplib
from email.mime.text import MIMEText
from datetime import datetime, timedelta, timezone

import arxiv
from anthropic import Anthropic

# ---------- CONFIG ----------
CATEGORIES = "cat:physics.acc-ph OR cat:physics.plasm-ph OR cat:physics.optics"
LOOKBACK_DAYS = 1          # how far back to pull
MAX_RESULTS = 300          # safety cap on how many to fetch
MODEL = "claude-haiku-4-5-20251001"

RESEARCH_FOCUS = (
    "laser plasma accelerators (LPAs / LWFA): laser wakefield acceleration, "
    "plasma-based electron/positron acceleration, high-intensity laser-plasma "
    "interactions for particle acceleration, betatron radiation from plasma "
    "accelerators, plasma injectors, staging, and related diagnostics."
)

# Email settings
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
EMAIL_FROM = os.environ["EMAIL_FROM"]        # your gmail address
EMAIL_TO = os.environ["EMAIL_TO"]            # where to send the digest
EMAIL_APP_PASSWORD = os.environ["EMAIL_APP_PASSWORD"]

client = Anthropic()  # reads ANTHROPIC_API_KEY from environment


# ---------- 1. FETCH ----------
def fetch_recent_papers():
    search = arxiv.Search(
        query=CATEGORIES,
        sort_by=arxiv.SortCriterion.SubmittedDate,
        max_results=MAX_RESULTS,
    )
    cutoff = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)
    papers = []
    for result in arxiv.Client().results(search):
        if result.published < cutoff:
            break  # results are date-sorted, so we can stop early
        papers.append(result)
    return papers


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
def build_digest(buckets):
    order = ["relevant", "mildly relevant", "not relevant"]
    headers = {
        "relevant": "🟢 RELEVANT",
        "mildly relevant": "🟡 MILDLY RELEVANT",
        "not relevant": "⚪ NOT RELEVANT",
    }
    lines = [f"arXiv digest — {datetime.now().strftime('%Y-%m-%d')}", ""]
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
    papers = fetch_recent_papers()
    buckets = {"relevant": [], "mildly relevant": [], "not relevant": []}
    for paper in papers:
        category, reason = classify(paper)
        if category not in buckets:
            category = "not relevant"
        buckets[category].append((paper, reason))
    digest = build_digest(buckets)
    send_email(digest)
    print(f"Sent digest covering {len(papers)} papers.")


if __name__ == "__main__":
    main()