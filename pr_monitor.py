import os
import json
import feedparser
import requests
from datetime import datetime, timedelta, timezone
import anthropic
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# Konfiguration
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
NEWS_API_KEY      = os.environ.get("NEWS_API_KEY")
EMAIL_SENDER      = os.environ.get("EMAIL_SENDER")
EMAIL_PASSWORD    = os.environ.get("EMAIL_PASSWORD")
EMAIL_RECEIVER    = os.environ.get("EMAIL_RECEIVER")

TARGETS = {
    "Blue Elephant Energy": [
        '"Blue Elephant Energy"',
        '"Blue Elephant Energy" solar OR wind OR battery OR energy',
    ],
    "Antin Infrastructure Partners": [
        '"Antin Infrastructure Partners"',
        '"Antin Infrastructure" infrastructure OR energy OR investment OR acquisition',
    ],
}

TRADE_FEEDS = {
    "Renews.biz":          "https://renews.biz/feed/",
    "Recharge News":       "https://www.rechargenews.com/rss",
    "PV Magazine":         "https://www.pv-magazine.com/feed/",
    "Windpower Monthly":   "https://www.windpowermonthly.com/rss",
    "Energy Storage News": "https://www.energy-storage.news/feed/",
}

SEARCH_TERMS = [
    "blue elephant energy",
    "antin infrastructure",
]


def fetch_google_news(query: str) -> list[dict]:
    """Holt Ergebnisse via Google News RSS."""
    encoded = requests.utils.quote(query)
    url = f"https://news.google.com/rss/search?q={encoded}+when:1d&hl=de&gl=DE&ceid=DE:de"
    feed = feedparser.parse(url)
    results = []
    for entry in feed.entries[:8]:
        results.append({
            "title":       entry.get("title", ""),
            "description": entry.get("summary", "")[:400],
            "url":         entry.get("link", ""),
            "source":      entry.get("source", {}).get("title", ""),
            "published":   entry.get("published", ""),
        })
    return results


def fetch_newsapi(query: str) -> list[dict]:
    """Holt Ergebnisse via NewsAPI als Ergänzung."""
    if not NEWS_API_KEY:
        return []
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    url = "https://newsapi.org/v2/everything"
    params = {
        "q":        query,
        "from":     yesterday,
        "sortBy":   "publishedAt",
        "language": "en",
        "pageSize": 5,
        "apiKey":   NEWS_API_KEY,
    }
    try:
        r = requests.get(url, params=params, timeout=10)
        if r.status_code == 200:
            return [
                {
                    "title":       a["title"],
                    "description": (a.get("description") or "")[:400],
                    "url":         a["url"],
                    "source":      a.get("source", {}).get("name", ""),
                    "published":   a.get("publishedAt", ""),
                }
                for a in r.json().get("articles", [])
            ]
    except Exception as e:
        print(f"NewsAPI error: {e}")
    return []


def fetch_trade_media() -> list[dict]:
    """Durchsucht Fachmedien-RSS nach Erwähnungen der Zielunternehmen."""
    results = []
    yesterday = datetime.now(timezone.utc) - timedelta(days=1)

    for source, url in TRADE_FEEDS.items():
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:30]:
                title   = entry.get("title", "").lower()
                summary = entry.get("summary", "").lower()

                if any(term in title or term in summary for term in SEARCH_TERMS):
                    if hasattr(entry, "published_parsed") and entry.published_parsed:
                        pub_date = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
                        if pub_date < yesterday:
                            continue
                    else:
                        continue

                    combined = title + summary
                    if "blue elephant" in combined:
                        company = "Blue Elephant Energy"
                    elif "antin" in combined:
                        company = "Antin Infrastructure Partners"
                    else:
                        continue

                    results.append({
                        "title":       entry.get("title", ""),
                        "description": entry.get("summary", "")[:400],
                        "url":         entry.get("link", ""),
                        "source":      source,
                        "company":     company,
                    })
                    print(f"  Treffer in {source}: {entry.get('title', '')[:60]}")
        except Exception as e:
            print(f"Fehler bei {source}: {e}")

    return results


def collect_all_mentions() -> dict:
    """Sammelt alle Erwähnungen für beide Zielunternehmen."""
    results  = {"Blue Elephant Energy": [], "Antin Infrastructure Partners": []}
    seen_urls = set()

    # Google News + NewsAPI
    for company, queries in TARGETS.items():
        for q in queries:
            for article in fetch_google_news(q) + fetch_newsapi(q):
                if article["url"] not in seen_urls and article["title"]:
                    seen_urls.add(article["url"])
                    results[company].append(article)

    # Fachmedien RSS
    print("Durchsuche Fachmedien...")
    for article in fetch_trade_media():
        company = article["company"]
        if article["url"] not in seen_urls:
            seen_urls.add(article["url"])
            results[company].append(article)

    for company, articles in results.items():
        results[company] = articles[:12]
        print(f"{company}: {len(results[company])} Artikel gesamt")

    return results


def create_briefing(mentions: dict) -> str:
    """Erstellt das strukturierte Briefing auf Deutsch via Claude."""
    client    = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    data_text = json.dumps(mentions, indent=2, ensure_ascii=False)

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1500,
        system="Du bist ein präziser PR-Analyst.",
        messages=[
            {"role": "user", "content": f"""Du bist PR- und Kommunikationsberater. Erstelle ein tägliches Monitoring-Briefing auf Deutsch.

Analysiere folgende Medienfunde der letzten 24 Stunden und schreibe das Briefing exakt in diesem Format:

---
**Blue Elephant Energy**
[Konkrete Befunde als Bullet Points, oder: „Keine neuen Meldungen in den letzten 24 Stunden."]

**Antin Infrastructure Partners**
[Konkrete Befunde als Bullet Points, oder: „Keine neuen Meldungen in den letzten 24 Stunden."]

**Bewertung**
[2–3 Sätze: PR-Relevanz einschätzen, Handlungsbedarf ja oder nein, kurze Begründung]
---

Fokus auf: M&A, Investitionen, Projektankündigungen, Regulatorik, Personalveränderungen, Reputationsrisiken, Energiepolitik.
Stil: sachlich, prägnant, Bullet Points mit Quellenname in Klammern.

Mediendaten:
{data_text}"""}
        ]
    )
    return message.content[0].text


def send_email(briefing: str):
    """Versendet das Briefing per E-Mail."""
    today = datetime.now().strftime("%d.%m.%Y")

    msg            = MIMEMultipart()
    msg["From"]    = EMAIL_SENDER
    msg["To"]      = EMAIL_RECEIVER
    msg["Subject"] = f"PR Monitoring – Blue Elephant Energy & Antin – {today}"

    body = f"""PR & Medien-Monitoring – {today}
{'='*50}

{briefing}

{'='*50}
Quellen: Google News, NewsAPI, Renews.biz, Recharge News, PV Magazine, Windpower Monthly, Energy Storage News
Generiert automatisch – {datetime.now().strftime('%H:%M Uhr')}
"""
    msg.attach(MIMEText(body, "plain", "utf-8"))

    assert EMAIL_SENDER is not None
    assert EMAIL_PASSWORD is not None
    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls()
        server.login(EMAIL_SENDER, EMAIL_PASSWORD)
        server.send_message(msg)
    print("E-Mail erfolgreich gesendet.")


def main():
    missing = [v for v in ["ANTHROPIC_API_KEY", "EMAIL_SENDER", "EMAIL_PASSWORD", "EMAIL_RECEIVER"] if not os.environ.get(v)]
    if missing:
        raise SystemExit(f"Fehlende Umgebungsvariablen: {', '.join(missing)}")

    print(f"PR Monitoring gestartet – {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    print("Suche nach Erwähnungen...")
    mentions = collect_all_mentions()

    print("Erstelle Briefing...")
    briefing = create_briefing(mentions)

    print("\n--- VORSCHAU ---")
    print(briefing)
    print("--- ENDE ---\n")

    print("Sende E-Mail...")
    send_email(briefing)

    print("Fertig!")


if __name__ == "__main__":
    main()