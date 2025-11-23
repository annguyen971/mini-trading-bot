from vnstock import Company
import pandas as pd

pd.set_option('display.max_columns', None)
pd.set_option('display.width', 1000)

symbol = 'TCB'
print(f"Fetching news for {symbol}...")
company = Company(symbol=symbol, source='TCBS')
print("Company methods:", dir(company))
news_df = company.news(page_size=5)

if news_df is not None and not news_df.empty:
    print("Columns:", news_df.columns.tolist())
    print("First row:", news_df.iloc[0].to_dict())
else:
    print("No news found.")
