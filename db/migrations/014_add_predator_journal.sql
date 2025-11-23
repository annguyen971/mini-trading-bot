-- Migration: 014_add_predator_journal
-- Description: Creates table for Apex Predator Training Lab (Story 6.1, 6.3)
-- Stores user blind test predictions and AI Predator analysis

CREATE TABLE IF NOT EXISTS predator_journal (
    id BIGSERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    as_of_date DATE NOT NULL,
    
    -- Scenario Engine Outputs (Story 6.1)
    scenario_tag TEXT, -- e.g., 'DISTRIBUTION_CLIMAX', 'STEALTH_ACCUMULATION'
    trapped_price DOUBLE PRECISION, -- Calculated liquidity trap level
    
    -- User Interaction (Story 6.3)
    user_prediction TEXT, -- The "Blind Test" input from user
    user_id TEXT DEFAULT 'admin', -- For future multi-user support
    
    -- AI Analysis (Story 6.2)
    ai_memo JSONB, -- Structure: {predator_memo, quant_explanation, user_playbook}
    
    -- Metadata
    revealed_at TIMESTAMPTZ, -- When the user unlocked the analysis
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Performance Indexes
CREATE INDEX IF NOT EXISTS idx_predator_journal_symbol ON predator_journal(symbol);
CREATE INDEX IF NOT EXISTS idx_predator_journal_date ON predator_journal(as_of_date);
CREATE INDEX IF NOT EXISTS idx_predator_journal_user ON predator_journal(user_id);

-- Unique constraint to prevent duplicate active sessions per day if needed, 
-- but for now we allow multiple attempts or history. 
-- Let's add a composite index for quick lookup of today's entry.
CREATE INDEX IF NOT EXISTS idx_predator_journal_lookup ON predator_journal(symbol, as_of_date, user_id);
