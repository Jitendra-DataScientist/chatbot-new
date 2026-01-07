import polars as pl
import json

# Load the data
df = pl.read_csv('tableau_exports/CentralizedCommOpsL10NMetrics/datasources/federated_053h21l19h2gzs11uyzf71.csv',
                 infer_schema_length=50000)

print(f"Total rows: {len(df):,}")
print(f"\nColumns: {df.columns}")

# Convert month to datetime
df = df.with_columns(pl.col('month').str.to_datetime())

# Check March data
print("\n" + "="*80)
print("MARCH TWC ANALYSIS")
print("="*80)

# Get all March data (all years)
march_df = df.filter(pl.col('month').dt.month() == 3)
print(f"\nTotal March rows (all years): {len(march_df):,}")
print(f"Total March TWC (all years): {march_df['twc'].sum():,.0f}")

# Breakdown by year
march_by_year = march_df.group_by(pl.col('month').dt.year().alias('year')).agg([
    pl.count().alias('rows'),
    pl.sum('twc').alias('twc_sum')
]).sort('year')

print("\nMarch TWC by Year:")
print(march_by_year)

# March 2025 specific
march_2025 = df.filter((pl.col('month').dt.month() == 3) & (pl.col('month').dt.year() == 2025))
print(f"\nMarch 2025:")
print(f"  Rows: {len(march_2025):,}")
print(f"  TWC Sum: {march_2025['twc'].sum():,.0f}")

# Check what filters could reduce 52M to 678k
print("\n" + "="*80)
print("POTENTIAL FILTERS TO GET FROM 52.7M TO 678K")
print("="*80)

target_twc = 678_070
actual_twc = march_2025['twc'].sum()
ratio = actual_twc / target_twc

print(f"\nTarget TWC: {target_twc:,.0f}")
print(f"Actual TWC: {actual_twc:,.0f}")
print(f"Ratio: {ratio:.1f}x")

# Check unique values for common filter fields
filter_fields = ['vendor', 'client_category', 'client', 'domain', 'target_locale', 'vertical']

for field in filter_fields:
    if field in df.columns:
        breakdown = march_2025.group_by(field).agg([
            pl.count().alias('rows'),
            pl.sum('twc').alias('twc')
        ]).sort('twc', descending=True).head(10)

        print(f"\nMarch 2025 TWC by {field}:")
        print(breakdown)

        # Check if any single value gives us ~678k
        for row in breakdown.iter_rows(named=True):
            if 600_000 < row['twc'] < 750_000:
                print(f"  ⭐ MATCH: {field}='{row[field]}' has TWC={row['twc']:,.0f}")

print("\n" + "="*80)
print("CHECKING DATA CACHE")
print("="*80)

# Also check the default.parquet that was being used
cache_df = pl.read_parquet('data_cache/default.parquet')
print(f"\nCache columns: {cache_df.columns}")
print(f"Cache rows: {len(cache_df):,}")

if 'twc' in cache_df.columns:
    cache_df = cache_df.with_columns(pl.col('month').str.to_datetime())
    cache_march_2025 = cache_df.filter((pl.col('month').dt.month() == 3) & (pl.col('month').dt.year() == 2025))
    print(f"\nCache March 2025 TWC: {cache_march_2025['twc'].sum():,.0f}")
else:
    print("\n'twc' column not found in cache")
