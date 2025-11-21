-- Migration 008: Update Sector Taxonomy (4 Pillars + 1)

-- 1. Alter dim_sector table
ALTER TABLE dim_sector ADD COLUMN IF NOT EXISTS super_sector TEXT;
ALTER TABLE dim_sector ADD COLUMN IF NOT EXISTS correlation_asset TEXT;

-- 2. Clear existing data (re-population)
TRUNCATE TABLE dim_sector CASCADE;

-- 3. Populate new taxonomy
INSERT INTO dim_sector (sector, display_name, super_sector, correlation_asset) VALUES
-- Pillar 1: Financials & Capital (Tài chính & Vốn)
('banking', 'Ngân hàng', 'Financials', 'VN30'),
('securities', 'Chứng khoán', 'Financials', 'VNINDEX'),
('insurance', 'Bảo hiểm', 'Financials', 'VNINDEX'),

-- Pillar 2: Real Estate & Value Chain (BĐS & Chuỗi giá trị)
('real_estate', 'Bất động sản (TM/Nhà ở)', 'Real_Estate_Chain', 'Interest_Rate'),
('industrial_re', 'BĐS Khu công nghiệp', 'Real_Estate_Chain', 'FDI_Inflow'),
('construction', 'Xây dựng & Hạ tầng', 'Real_Estate_Chain', 'Public_Investment'),
('materials', 'Vật liệu xây dựng (Thép)', 'Real_Estate_Chain', 'HRC_Price'),

-- Pillar 3: Production & Export (Sản xuất & Xuất khẩu)
('energy', 'Dầu khí', 'Production_Export', 'Brent_Oil'),
('agriculture', 'Thủy sản & Nông nghiệp', 'Production_Export', 'Export_Value'),
('manufacturing', 'Dệt may & Gỗ', 'Production_Export', 'Global_PMI'),
('chemicals', 'Hóa chất & Phân bón', 'Production_Export', 'Urea_Price'),
('logistics', 'Vận tải & Cảng biển', 'Production_Export', 'BDI_Index'),

-- Pillar 4: Consumer & Tech (Tiêu dùng & Công nghệ)
('retail', 'Bán lẻ', 'Consumer_Tech', 'CPI'),
('technology', 'Công nghệ', 'Consumer_Tech', 'USD_VND'),
('consumer_goods', 'Thực phẩm & Đồ uống', 'Consumer_Tech', 'Retail_Sales'),

-- Pillar 5: Utilities (Tiện ích - Phòng thủ)
('utilities', 'Điện, Nước, Dược phẩm', 'Utilities', 'Defensive_Index');

-- 4. Update existing watchlist symbols to match new keys if needed
-- (Most keys like 'technology', 'banking', 'utilities' are same. 
-- 'industrial' might need mapping to 'materials' or 'construction' depending on the stock, 
-- but for now we keep 'industrial' as legacy or map it. 
-- User's request implies 'industrial' -> 'materials' (HPG) or 'construction'.
-- Let's add 'industrial' as an alias or map it to 'materials' for HPG case to avoid breaking FKs immediately if we didn't cascade)

-- Actually, we truncated dim_sector, so FKs from symbol_watchlist would fail if we didn't CASCADE.
-- But symbol_watchlist doesn't have a FK constraint to dim_sector in the schema provided in init.sql?
-- Let's check init.sql again.
-- "sector TEXT" in symbol_watchlist. No REFERENCES dim_sector(sector).
-- So we are safe from FK violation, but we should update the values in symbol_watchlist to match new keys.

UPDATE symbol_watchlist SET sector = 'materials' WHERE sector = 'industrial' AND symbol IN ('HPG', 'HSG', 'NKG');
UPDATE symbol_watchlist SET sector = 'real_estate' WHERE sector = 'real_estate'; -- same
UPDATE symbol_watchlist SET sector = 'consumer_goods' WHERE sector = 'consumer_goods'; -- same
UPDATE symbol_watchlist SET sector = 'banking' WHERE sector = 'banking'; -- same
UPDATE symbol_watchlist SET sector = 'technology' WHERE sector = 'technology'; -- same
UPDATE symbol_watchlist SET sector = 'utilities' WHERE sector = 'utilities'; -- same

-- Handle generic 'industrial' that might be construction or others
-- For safety, let's map remaining 'industrial' to 'manufacturing' or leave as is if we add 'industrial' back?
-- The new taxonomy doesn't have 'industrial'. It has 'manufacturing', 'construction', 'materials'.
-- Let's add 'industrial' as a deprecated key mapped to 'Production_Export' just in case, 
-- OR better, migrate data.
-- Assuming HPG was the main 'industrial' example.

-- 5. Add a view or helper for UI if needed (optional)
