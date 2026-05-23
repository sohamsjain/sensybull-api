"""
Stream 8-K filings from SEC EDGAR, extract item-wise text and press release content.
"""

import os
import time
from dotenv import load_dotenv
from edgar import set_identity, get_current_filings

load_dotenv()
# SEC requires identification for API access
identity = os.environ.get("EDGAR_IDENTITY", "Sensybull sensybull@example.com")
set_identity(identity)


def extract_items(eightk):
    """Extract text from each item in an 8-K filing.

    eightk[key] returns a plain string (the item text), not an object.
    """
    extracted = {}
    for item_key in eightk.items:
        text = eightk[item_key]
        if text:
            extracted[item_key] = str(text)
    return extracted


def extract_press_release(filing):
    """Extract press release text from 8-K exhibits.

    Press releases are typically filed as EX-99.x attachments.
    """
    press_releases = []
    for attachment in filing.attachments:
        doc_type = (attachment.document_type or "").upper()
        if doc_type.startswith("EX-99"):
            desc = attachment.description or doc_type
            try:
                text = attachment.text()
                press_releases.append({
                    "description": desc,
                    "text": text,
                })
            except Exception as e:
                press_releases.append({
                    "description": desc,
                    "error": str(e),
                })
    return press_releases


def process_filing(filing):
    """Process a single 8-K filing: extract items and press releases."""
    print("=" * 80)
    print(f"Company: {filing.company}")
    print(f"Filed:   {filing.filing_date}")
    print(f"Form:    {filing.form}")
    print(f"URL:     {filing.filing_url}")
    print("=" * 80)

    # Parse the 8-K object
    try:
        eightk = filing.obj()
    except Exception as e:
        print(f"  [ERROR] Could not parse 8-K: {e}\n")
        return

    # Extract items
    items = extract_items(eightk)
    if items:
        print("\n--- ITEMS ---")
        for key, text in items.items():
            print(f"\n[{key}]")
            print("-" * 40)
            print(text[:2000] if len(text) > 2000 else text)
            if len(text) > 2000:
                print(f"  ... ({len(text)} total characters)")
    else:
        print("\n  No parseable items found.")

    # Extract press releases
    press_releases = extract_press_release(filing)
    if press_releases:
        print("\n--- PRESS RELEASES ---")
        for i, pr in enumerate(press_releases, 1):
            print(f"\n[Press Release {i}] {pr.get('description', 'N/A')}")
            print("-" * 40)
            if "error" in pr:
                print(f"  [ERROR] {pr['error']}")
            else:
                text = pr["text"]
                print(text[:3000] if len(text) > 3000 else text)
                if len(text) > 3000:
                    print(f"  ... ({len(text)} total characters)")
    else:
        print("\n  No press releases found in exhibits.")

    print()


POLL_INTERVAL = 60  # seconds between polls


def main():
    seen = set()
    print("Streaming 8-K filings (Ctrl+C to stop)...\n")

    while True:
        try:
            filings = get_current_filings(form="8-K")
            for filing in filings:
                accession = filing.accession_number
                if accession in seen:
                    continue
                seen.add(accession)
                process_filing(filing)
        except KeyboardInterrupt:
            print("\nStopped.")
            break
        except Exception as e:
            print(f"[ERROR] {e}")

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
