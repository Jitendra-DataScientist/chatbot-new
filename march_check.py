import polars as pl

# Read the cached data
df = pl.read_parquet('data_cache/default.parquet')
print(f"Total rows: {len(df)}")

# Convert month to datetime
df = df.with_columns(pl.col('month').str.to_datetime())

# Check March data by year
march_data = df.filter(pl.col('month').dt.month() == 3)
print(f"\nTotal March rows: {len(march_data)}")

result = march_data.group_by(
    pl.col('month').dt.year().alias('year')
).agg([
    pl.len().alias('row_count'),
    pl.col('twc').sum().alias('twc_sum')
]).sort('year')

print("\nMarch data by year:")
for row in result.to_dicts():
    print(f"  Year {row['year']}: {row['row_count']} rows, TWC sum: {row['twc_sum']:,.0f}")

# Check March 2025 specifically
march_2025 = df.filter((pl.col('month').dt.month() == 3) & (pl.col('month').dt.year() == 2025))
print(f"\nMarch 2025 rows: {len(march_2025)}")
print(f"March 2025 TWC sum: {march_2025['twc'].sum()}")

# Check what the chatbot returned
chatbot_result = 5.2726e7
print(f"\nChatbot returned: {chatbot_result:,.0f}")
print(f"Expected (chart shows): 678,070")
