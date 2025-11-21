import os
import time
import json
from typing import Dict, List, Optional, Tuple

import google.generativeai as genai
from google.generativeai.types import GenerationConfig, HarmCategory, HarmBlockThreshold
from google.api_core import exceptions as google_exceptions

from core_lib.db import get_db_connection
from core_lib.locks import get_advisory_lock
import re

# --- Constants ---
ADVISORY_LOCK_NAME = "nlp_batch"
NLP_BATCH_SIZE = 20  # Max items to process in one API call
MAX_RETRIES = 3
INITIAL_BACKOFF = 2  # seconds
CATALYST_KEYWORDS = {
    "M&A": ["M&A", "sáp nhập", "thâu tóm"],
    "EARNINGS_SURPRISE": ["KQKD vượt kỳ vọng", "lợi nhuận đột biến"],
    "CAPITAL_INCREASE": ["tăng vốn", "phát hành thêm"],
}


# --- Gemini Configuration ---
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
if not GOOGLE_API_KEY:
    raise ValueError("GOOGLE_API_KEY environment variable not set.")

genai.configure(api_key=GOOGLE_API_KEY)

# Define the structured output schema for Gemini
RESPONSE_SCHEMA = {
    "sentiment_score": "float",  # -1.0 (very negative) to +1.0 (very positive)
    "catalysts": "list",         # List of detected events/catalysts
    "summary": "str",            # A very brief, one-sentence summary
}

GENERATION_CONFIG = GenerationConfig(
    response_mime_type="application/json",
    temperature=0.2,
)

SAFETY_SETTINGS = {
    HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_ONLY_HIGH,
    HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_ONLY_HIGH,
    HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_ONLY_HIGH,
    HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_ONLY_HIGH,
}

PROMPT_TEMPLATE = """
Analyze the sentiment of the following Vietnamese stock market news snippets.
For each snippet, provide:
1.  `sentiment_score`: A float between -1.0 (very negative) and 1.0 (very positive).
2.  `catalysts`: A list of any relevant catalysts from this list: {catalyst_keywords}. If none, return an empty list.
3.  `summary`: A concise, one-sentence summary in Vietnamese.

Return a JSON object containing a key "results", which is a list of JSON objects matching this exact schema: {schema}.
Do not include any other text or explanations in your response.

Here are the news snippets:
---
{news_snippets}
---
"""

# --- Core Logic ---

def process_nlp_batch(docs: List[Tuple[str, str]]) -> List[Dict]:
    """
    Processes a batch of texts using the Gemini API with structured output,
    batching, and retry logic.
    """
    if not docs:
        return []

    model = genai.GenerativeModel(
        "gemini-2.0-flash",
        generation_config=GENERATION_CONFIG,
        safety_settings=SAFETY_SETTINGS
    )

    # Format the input for the prompt
    formatted_snippets = "\n".join([f'{doc_id}: "{text}"' for doc_id, text in docs])
    prompt = PROMPT_TEMPLATE.format(
        catalyst_keywords=list(CATALYST_KEYWORDS.keys()),
        schema=json.dumps(RESPONSE_SCHEMA),
        news_snippets=formatted_snippets
    )

    for attempt in range(MAX_RETRIES):
        try:
            response = model.generate_content(prompt)

            # Clean and parse the JSON response
            response_text = response.text.strip().replace("```json", "").replace("```", "")
            results_data = json.loads(response_text)

            # Add doc_id back to each result for traceability
            processed_results = []
            for i, (doc_id, _) in enumerate(docs):
                 if i < len(results_data.get("results", [])):
                    res = results_data["results"][i]
                    res['doc_id'] = doc_id
                    processed_results.append(res)
            return processed_results

        except (google_exceptions.ResourceExhausted, google_exceptions.ServiceUnavailable) as e:
            wait_time = INITIAL_BACKOFF ** attempt
            print(f"WARN: Rate limit or server error encountered. Retrying in {wait_time}s... (Attempt {attempt + 1}/{MAX_RETRIES})")
            time.sleep(wait_time)
        except (json.JSONDecodeError, KeyError) as e:
            print(f"ERROR: Failed to parse Gemini response or key error: {e}")
            print(f"Raw response was:\n{response.text}")
            return [] # Fail the batch if parsing fails
        except Exception as e:
            print(f"ERROR: An unexpected error occurred with Gemini API: {e}")
            # For other unexpected errors, fail the batch immediately
            return []

    print(f"ERROR: Batch failed after {MAX_RETRIES} retries.")
    return []


def extract_symbols(text: str) -> List[str]:
    """Extract stock symbols from text using regex pattern [A-Z]{3,7}."""
    if not text:
        return []
    # Match 3-7 uppercase letters (Vietnamese stock symbols)
    pattern = r'\b[A-Z]{3,7}\b'
    symbols = re.findall(pattern, text)
    # Deduplicate and filter common false positives
    false_positives = {'THE', 'AND', 'FOR', 'ARE', 'WAS', 'NOT', 'BUT', 'ALL', 'CAN', 'HAD', 'HER', 'WAS', 'ONE', 'OUR', 'OUT', 'DAY'}
    symbols = [s for s in set(symbols) if s not in false_positives]
    return sorted(symbols)

def scan_for_catalysts(text: str) -> Dict[str, bool]:
    """(AC16) Scans text for predefined catalyst keywords."""
    found_catalysts = {}
    text_lower = text.lower()
    for flag, keywords in CATALYST_KEYWORDS.items():
        if any(keyword in text_lower for keyword in keywords):
            found_catalysts[flag] = True
    return found_catalysts

def run_nlp_pipeline():
    """Main function to run the entire NLP cascade pipeline."""
    print("Starting NLP batch job...")
    conn = get_db_connection()

    if not get_advisory_lock(conn, ADVISORY_LOCK_NAME):
        return  # Exit if another process has the lock

    try:
        # 1. Fetch unprocessed news from sa_silver (needs NLP)
        print("Fetching unprocessed news articles from sa_silver...")

        with conn.cursor() as cur:
            # Get articles that don't have sentiment_score yet
            cur.execute("""
                SELECT
                    url_canonical,
                    source_name,
                    publisher_time_utc,
                    bronze_ref_id
                FROM sa_silver
                WHERE sentiment_score IS NULL
                  AND validated_at > NOW() - INTERVAL '7 days'
                ORDER BY publisher_time_utc DESC
                LIMIT 200
            """)

            pending_articles = cur.fetchall()

            if not pending_articles:
                print("No articles pending NLP processing.")
                return

            print(f"Found {len(pending_articles)} articles pending NLP processing")

            # Fetch full text from raw_bronze via bronze_ref_id
            raw_docs = []
            for article in pending_articles:
                url_canonical, source_name, pub_time, bronze_id = article

                if not bronze_id:
                    print(f"  Skipping {url_canonical} - no bronze_ref_id")
                    continue

                # Get full text from raw_bronze
                cur.execute("""
                    SELECT payload_json->>'text', payload_json->>'title'
                    FROM raw_bronze
                    WHERE id = %s
                """, (bronze_id,))

                bronze_row = cur.fetchone()
                if bronze_row and (bronze_row[0] or bronze_row[1]):
                    text, title = bronze_row
                    # Combine title and text for NLP
                    full_text = f"{title}. {text}" if title else text
                    raw_docs.append((url_canonical, full_text))
                else:
                    print(f"  Warning: No text found in raw_bronze for {url_canonical}")

        if not raw_docs:
            print("No valid documents with text to process.")
            return

        print(f"Prepared {len(raw_docs)} documents for NLP processing")

        # 2. Process documents in batches
        total_processed = 0
        total_failed = 0

        for i in range(0, len(raw_docs), NLP_BATCH_SIZE):
            batch_docs = raw_docs[i:i + NLP_BATCH_SIZE]
            batch_num = i//NLP_BATCH_SIZE + 1
            print(f"\n--- Processing batch {batch_num} ({len(batch_docs)} docs) ---")

            results = process_nlp_batch(batch_docs)

            if not results:
                print(f"Batch {batch_num} failed. Skipping to next batch.")
                total_failed += len(batch_docs)
                continue

            # 3. Save results to sa_silver and catalyst_flags tables
            with conn.cursor() as cur:
                for result in results:
                    url_canonical = result['doc_id']
                    sentiment_score = result.get('sentiment_score')
                    catalysts = result.get('catalysts', [])

                    # Get full text for symbol extraction
                    cur.execute("""
                        SELECT rb.payload_json->>'text', rb.payload_json->>'title'
                        FROM sa_silver sa
                        JOIN raw_bronze rb ON sa.bronze_ref_id = rb.id
                        WHERE sa.url_canonical = %s
                    """, (url_canonical,))
                    
                    text_row = cur.fetchone()
                    full_text = ""
                    symbols = []
                    if text_row:
                        text, title = text_row
                        full_text = f"{title} {text}" if title and text else (title or text or "")
                        symbols = extract_symbols(full_text)

                    # Calculate hype_raw from sentiment_score (range -1 to +1)
                    # hype_raw is absolute intensity regardless of direction
                    hype_raw = abs(sentiment_score) if sentiment_score is not None else None

                    try:
                        # Update sa_silver with sentiment_score, symbols, and hype_raw
                        cur.execute("""
                            UPDATE sa_silver
                            SET sentiment_score = %s,
                                symbols = %s,
                                hype_raw = %s
                            WHERE url_canonical = %s
                        """, (sentiment_score, symbols, hype_raw, url_canonical))

                        # Insert catalysts into catalyst_flags
                        if catalysts:
                            for catalyst in catalysts:
                                catalyst_type = catalyst
                                catalyst_value = 1.0

                                cur.execute("""
                                    INSERT INTO catalyst_flags
                                        (sa_silver_ref_id, flag_name, value, as_of_time, validated_at)
                                    VALUES (%s, %s, %s, NOW(), NOW())
                                    ON CONFLICT (sa_silver_ref_id, flag_name)
                                    DO UPDATE SET
                                        value = EXCLUDED.value,
                                        validated_at = NOW()
                                """, (url_canonical, catalyst_type, catalyst_value))

                        total_processed += 1

                        print(f"  ✓ {url_canonical}: sentiment={sentiment_score:.2f}, catalysts={len(catalysts)}, symbols={symbols}")

                    except Exception as e:
                        print(f"  ✗ Failed to save {url_canonical}: {e}")
                        total_failed += 1

            # Commit after each batch
            conn.commit()

        print(f"\n=== NLP Pipeline Summary ===")
        print(f"Total processed: {total_processed}")
        print(f"Total failed: {total_failed}")
        print(f"Success rate: {total_processed/(total_processed+total_failed)*100:.1f}%" if (total_processed+total_failed) > 0 else "N/A")

        conn.commit()

    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"An error occurred in the NLP pipeline: {e}")
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()
            print("\nNLP batch job finished.")

if __name__ == "__main__":
    run_nlp_pipeline()
