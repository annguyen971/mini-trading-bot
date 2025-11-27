-- P0 HOTFIX: Fix Incorrect Sector Mapping in symbol_watchlist
-- Root Cause: Most stocks were incorrectly mapped to "technology" sector
-- Impact: Market X-Ray and RRG charts showing completely wrong sector analysis
-- 
-- This migration corrects all 51 stock mappings according to Vietnam taxonomy
-- Based on: SSI Research, FTSE Vietnam, MSCI Vietnam classifications

-- First, add missing sectors to dim_sector (if they don't exist)
INSERT INTO dim_sector (sector, display_name, super_sector, correlation_asset)
VALUES 
    ('healthcare', 'Dược phẩm & Y tế', 'Consumer_Tech', 'CPI'),
    ('renewable_energy', 'Năng lượng tái tạo', 'Production_Export', 'Carbon_Credit'),
    ('conglomerate', 'Tập đoàn đa ngành', 'Financials', 'VNINDEX')
ON CONFLICT (sector) DO NOTHING;

-- Update sector mapping for all 51 stocks
UPDATE symbol_watchlist
SET sector = CASE symbol
    -- Banking (12 stocks)
    WHEN 'ACB' THEN 'banking'
    WHEN 'BID' THEN 'banking'
    WHEN 'CTG' THEN 'banking'
    WHEN 'HDB' THEN 'banking'
    WHEN 'LPB' THEN 'banking'
    WHEN 'MBB' THEN 'banking'
    WHEN 'STB' THEN 'banking'
    WHEN 'TCB' THEN 'banking'
    WHEN 'TPB' THEN 'banking'
    WHEN 'VCB' THEN 'banking'
    WHEN 'VIB' THEN 'banking'
    WHEN 'VPB' THEN 'banking'
    
    -- Securities (2 stocks)
    WHEN 'SSI' THEN 'securities'
    WHEN 'VCI' THEN 'securities'
    
    -- Insurance (1 stock)
    WHEN 'BVH' THEN 'insurance'
    
    -- Consumer Goods (7 stocks)
    WHEN 'DBC' THEN 'consumer_goods'
    WHEN 'MSN' THEN 'consumer_goods'
    WHEN 'PNJ' THEN 'consumer_goods'
    WHEN 'SAB' THEN 'consumer_goods'
    WHEN 'SBT' THEN 'consumer_goods'
    WHEN 'VHC' THEN 'consumer_goods'
    WHEN 'VNM' THEN 'consumer_goods'
    
    -- Retail (1 stock)
    WHEN 'MWG' THEN 'retail'
    
    -- Technology (1 stock)
    WHEN 'FPT' THEN 'technology'
    
    -- Real Estate (7 stocks)
    WHEN 'BCM' THEN 'real_estate'
    WHEN 'KDH' THEN 'real_estate'
    WHEN 'NVL' THEN 'real_estate'
    WHEN 'PDR' THEN 'real_estate'
    WHEN 'VHM' THEN 'real_estate'
    WHEN 'VIC' THEN 'real_estate'  -- Conglomerate but mapped to real_estate for RRG consistency
    WHEN 'VRE' THEN 'real_estate'
    
    -- Materials (6 stocks)
    WHEN 'DPM' THEN 'materials'
    WHEN 'GVR' THEN 'materials'
    WHEN 'HPG' THEN 'materials'
    WHEN 'HSG' THEN 'materials'
    WHEN 'VGC' THEN 'materials'
    
    -- Energy (4 stocks)
    WHEN 'BCG' THEN 'renewable_energy'
    WHEN 'GAS' THEN 'energy'
    WHEN 'PLX' THEN 'energy'
    WHEN 'PVD' THEN 'energy'
    
    -- Utilities (3 stocks)
    WHEN 'NT2' THEN 'utilities'
    WHEN 'POW' THEN 'utilities'
    WHEN 'REE' THEN 'utilities'
    
    -- Construction (2 stocks)
    WHEN 'CDH' THEN 'construction'
    WHEN 'PC1' THEN 'construction'
    
    -- Logistics (2 stocks)
    WHEN 'GMD' THEN 'logistics'
    WHEN 'SCS' THEN 'logistics'
    
    -- Chemicals (1 stock)
    WHEN 'DGC' THEN 'chemicals'
    
    -- Healthcare (1 stock)
    WHEN 'DHG' THEN 'healthcare'
    
    -- Agriculture (1 stock)
    WHEN 'HNG' THEN 'agriculture'
    
    -- Industrial (1 stock - Aviation)
    WHEN 'VJC' THEN 'industrial'
    
    ELSE sector  -- Keep existing if not in list (safety)
END
WHERE is_active = true;

-- Verify the fix
SELECT 
    sector,
    COUNT(*) as stock_count,
    STRING_AGG(symbol, ', ' ORDER BY symbol) as symbols
FROM symbol_watchlist
WHERE is_active = true
GROUP BY sector
ORDER BY stock_count DESC, sector;
