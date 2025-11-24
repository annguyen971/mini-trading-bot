import os
import polars as pl
from sqlalchemy import create_engine
import pandas as pd
from worker.tasks_feature_gold import calculate_sentiment_features, calculate_technical_features, apply_hmm, calculate_scores, read_sql_pl

# Setup DB connection
DB_URL = os.getenv("DB_URL")
if not DB_URL:
    # Fallback for manual run
    DB_URL = "postgresql+psycopg://hunter:hunter@mini-trading-bot-db-1:5432/hunter"
    os.environ["DB_URL"] = DB_URL

engine = create_engine(DB_URL)

def debug_pipeline():
    print("--- Debugging Feature Pipeline ---")
    
    # 1. Read v_features_asof
    print("Reading v_features_asof...")
    df = read_sql_pl("SELECT * FROM v_features_asof LIMIT 100", params=None)
    print("v_features_asof head:")
    print(df.head())
    
    # 2. Calculate Real Features
    print("Calculating Real Features...")
    conn = engine.connect()
    sent_df = calculate_sentiment_features(conn)
    tech_df = calculate_technical_features(conn)
    
    print("Sentiment DF head:")
    print(sent_df.head())
    print("Technical DF head:")
    print(tech_df.head())
    
    # 3. Join
    print("Joining...")
    df = df.with_columns(pl.col("effective_date").cast(pl.Date))
    sent_df = sent_df.with_columns(pl.col("effective_date").cast(pl.Date))
    tech_df = tech_df.with_columns(pl.col("effective_date").cast(pl.Date))
    
    cols_to_drop = ['hype_crowd_z', 'news_count_crowd_z', 's_tech_z', 'S_tech_z']
    df = df.drop([c for c in cols_to_drop if c in df.columns])
    
    df = df.join(sent_df, on=["symbol", "effective_date"], how="left")
    df = df.join(tech_df, on=["symbol", "effective_date"], how="left")
    
    df = df.with_columns([
        pl.col("hype_crowd_z").fill_null(0.0),
        pl.col("news_count_crowd_z").fill_null(0.0),
        pl.col("S_tech_z").fill_null(0.0)
    ])
    
    print("Joined DF head:")
    print(df.select(['symbol', 'effective_date', 'hype_crowd_z', 'S_tech_z', 'breadth_contra_z']).head())
    
    # 4. Scores
    print("Calculating Scores...")
    # Mock macro blend
    ENABLE_MACRO_BLEND = False
    df = apply_hmm(df)
    df = calculate_scores(df, conn, ENABLE_MACRO_BLEND)
    
    print("Final Scores head:")
    print(df.head())

if __name__ == "__main__":
    debug_pipeline()
