import random
import time
from typing import Dict, List, Optional

from core_lib.db import get_db_connection
from core_lib.locks import get_advisory_lock

# --- Constants ---
ADVISORY_LOCK_NAME = "nlp_batch"
MAX_SEQ_LEN = 256
TIER1_CONFIDENCE_THRESHOLD = 0.7
CATALYST_KEYWORDS = {
    "M&A": ["M&A", "sáp nhập", "thâu tóm"],
    "EARNINGS_SURPRISE": ["KQKD vượt kỳ vọng", "lợi nhuận đột biến"],
    "CAPITAL_INCREASE": ["tăng vốn", "phát hành thêm"],
}

# --- Placeholder Models ---

class Tier1_Model:
    """Placeholder for a scikit-learn TF-IDF + Logistic Regression model."""
    def predict_proba(self, texts: List[str]) -> List[Dict[str, float]]:
        # Simulate predictions
        return [{"sentiment": random.uniform(-1, 1)} for _ in texts]

class Tier2_Model:
    """Placeholder for an onnxruntime PhoBERT INT8 model."""
    def predict(self, texts: List[str]) -> List[Dict[str, float]]:
        # Simulate predictions, including potential failures
        results = []
        for text in texts:
            if "fail" in text.lower(): # Simulate an OOM error
                raise MemoryError("Simulated OOM Error")
            results.append({"sentiment": random.uniform(-1, 1) * 1.2}) # Simulate slightly better model
        return results

# --- Core Logic ---

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
        tier1_model = Tier1_Model()
        tier2_model = Tier2_Model()

        # 1. Fetch data from raw_bronze (placeholder)
        raw_texts_to_process = [
            ("id_1", "Tin tốt, KQKD vượt kỳ vọng, lợi nhuận đột biến!"),
            ("id_2", "Cổ phiếu có vẻ hơi đắt ở thời điểm này."), # Uncertain
            ("id_3", "Một tin tức rất xấu, công ty sắp phá sản."),
            ("id_4", "Văn bản này chứa từ 'fail' để mô phỏng lỗi OOM."),
        ]

        for doc_id, text in raw_texts_to_process:
            print(f"\n--- Processing doc_id: {doc_id} ---")

            # (Tier 0) Prefilter
            text_clipped = text[:MAX_SEQ_LEN]

            # (Tier 1) Run Tiny Gate
            tier1_result = tier1_model.predict_proba([text_clipped])[0]
            sentiment_t1 = tier1_result.get("sentiment", 0)

            final_sentiment = sentiment_t1
            escalated_to_t2 = False

            # (Logic) Decide whether to escalate
            if abs(sentiment_t1) < TIER1_CONFIDENCE_THRESHOLD:
                print(f"Tier 1 uncertain (score: {sentiment_t1:.2f}). Escalating to Tier 2.")
                escalated_to_t2 = True
                try:
                    # (Tier 2) Run PhoBERT
                    tier2_result = tier2_model.predict([text_clipped])[0]
                    final_sentiment = tier2_result.get("sentiment", sentiment_t1)
                except Exception as e:
                    # (AC7) Fallback logic
                    print(f"ALERT: Tier 2 failed with error: {e}. Falling back to Tier 1 result.")
                    final_sentiment = sentiment_t1
            else:
                print(f"Tier 1 confident (score: {sentiment_t1:.2f}). Accepting result.")

            # (AC16) Scan for catalysts
            catalysts = scan_for_catalysts(text_clipped)

            # 4. Save results to Silver tables (placeholder)
            print(f"Final Sentiment: {final_sentiment:.2f}")
            if catalysts:
                print(f"Found Catalysts: {list(catalysts.keys())}")

            # In a real implementation, you would use the 'conn' object to
            # write the sentiment to 'sa_silver' and any found catalysts
            # to the 'catalyst_flags' table within a transaction.

        conn.commit()

    except Exception as e:
        print(f"An error occurred in the NLP pipeline: {e}")
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()
            print("\nNLP batch job finished.")

if __name__ == "__main__":
    run_nlp_pipeline()
