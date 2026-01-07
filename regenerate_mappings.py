"""
Regenerate chart_column_mappings.json with enhanced aggregation semantics
"""
import sys
import os

# Suppress emoji print errors on Windows
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

from CHART_COLUMN_MAPPINGS_READER import TableauColumnMappingExtractor

# Configuration
METADATA_DIR = os.path.join(os.getcwd(), "tableau_descriptions")
OUTPUT_FILE = os.path.join(os.getcwd(), "chart_column_mappings.json")

print("="*80)
print("Regenerating chart_column_mappings.json with aggregation semantics...")
print("="*80)
print(f"Input directory: {METADATA_DIR}")
print(f"Output file: {OUTPUT_FILE}")
print("="*80 + "\n")

# Create extractor and process
extractor = TableauColumnMappingExtractor(METADATA_DIR, OUTPUT_FILE)
extractor.process_all_dashboards()

# Save results
extractor.save_results()

print("\n" + "="*80)
print("EXTRACTION COMPLETE")
print("="*80)

# Print summary statistics
summary = extractor.results.get('summary', {})
print(f"\nDashboards processed: {summary.get('total_dashboards', 0)}")
print(f"Charts processed: {summary.get('total_charts', 0)}")
print(f"Unique columns: {summary.get('total_unique_columns', 0)}")
print(f"Columns with aggregation semantics: {summary.get('columns_with_aggregation_semantics', 0)}")

# Show a sample of semantic data
column_metadata = extractor.results.get('column_metadata', {})
columns_with_semantics = {k: v for k, v in column_metadata.items() if v.get('aggregation_semantics')}

print(f"\nSample columns with aggregation semantics:")
for i, (col_name, col_info) in enumerate(list(columns_with_semantics.items())[:5]):
    agg_sem = col_info.get('aggregation_semantics', {})
    print(f"  {col_name}:")
    print(f"    Type: {col_info.get('type')}")
    print(f"    Typical aggregation: {agg_sem.get('typical_aggregation')}")
    print(f"    Confidence: {agg_sem.get('confidence', 0):.0%}")
    print(f"    Aggregations: {agg_sem.get('aggregations', {})}")

print("="*80)
