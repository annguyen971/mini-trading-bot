#!/usr/bin/env python3
"""
Create Baseline Model - Stock Hunter AI
========================================
Creates a simple baseline model for API bootstrap.

This baseline model provides neutral predictions (50/50) until
the first trained model is available from the ML pipeline.

Run after database migration 003.
"""

import pickle
import hashlib
import os
from sklearn.dummy import DummyClassifier
import numpy as np
from core_lib.db import get_db_connection


def create_baseline_model():
    """
    Creates a baseline DummyClassifier model.

    Returns:
        Trained baseline model (always predicts class 1 with 50% probability)
    """
    print("Creating baseline model...")

    # Create a simple baseline: always predict neutral (50/50)
    model = DummyClassifier(strategy='uniform', random_state=42)

    # Train on dummy data
    X_dummy = np.random.rand(100, 10)  # 100 samples, 10 features
    y_dummy = np.random.randint(0, 2, 100)  # Binary labels

    model.fit(X_dummy, y_dummy)

    print("✓ Baseline model created (DummyClassifier - uniform strategy)")
    return model


def save_model_artifact(model, file_path: str) -> str:
    """
    Save model to disk and calculate SHA256 hash.

    Args:
        model: Trained model object
        file_path: Path to save model

    Returns:
        SHA256 hash of the model file
    """
    print(f"Saving model to {file_path}...")

    # Ensure directory exists
    os.makedirs(os.path.dirname(file_path), exist_ok=True)

    # Serialize model
    with open(file_path, 'wb') as f:
        pickle.dump(model, f)

    # Calculate hash
    with open(file_path, 'rb') as f:
        model_bytes = f.read()
        sha256_hash = hashlib.sha256(model_bytes).hexdigest()

    print(f"✓ Model saved")
    print(f"  Path: {file_path}")
    print(f"  Size: {len(model_bytes)} bytes")
    print(f"  SHA256: {sha256_hash}")

    return sha256_hash


def update_model_registry(sha256_hash: str):
    """
    Update model_registry with correct SHA256 hash.

    Args:
        sha256_hash: Calculated hash of the model file
    """
    print("Updating model_registry...")

    conn = None
    try:
        conn = get_db_connection()

        with conn.cursor() as cur:
            cur.execute("""
                UPDATE model_registry
                SET artifact_sha256 = %s
                WHERE model_version = 'baseline_v1'
            """, (sha256_hash,))

            if cur.rowcount > 0:
                print("✓ Model registry updated with correct SHA256")
            else:
                print("⚠ No rows updated - baseline_v1 not found in registry")
                print("  Run migration 003 first: db/migrations/003_seed_default_model.sql")

        conn.commit()

    except Exception as e:
        print(f"✗ Failed to update registry: {e}")
        if conn:
            conn.rollback()
        raise

    finally:
        if conn:
            conn.close()


def main():
    """Main execution."""
    print("=" * 60)
    print("Baseline Model Creation - Stock Hunter AI")
    print("=" * 60)

    # Paths
    artifacts_dir = "/opt/artifacts"
    model_path = os.path.join(artifacts_dir, "baseline_v1.pkl")

    # Create model
    model = create_baseline_model()

    # Save and hash
    sha256_hash = save_model_artifact(model, model_path)

    # Update registry
    update_model_registry(sha256_hash)

    print("\n" + "=" * 60)
    print("✓ Baseline model ready!")
    print("=" * 60)
    print("\nThe API can now start with this baseline model.")
    print("Run training pipeline to replace with trained model:")
    print("  docker compose exec worker python -m worker.tasks_train")
    print()

    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
