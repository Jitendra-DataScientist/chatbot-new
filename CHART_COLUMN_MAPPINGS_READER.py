"""
Tableau Column Mappings Extractor - Production Version v3.0
Extracts column aliases and formulas with CSV mapping support
Prevents LLM hallucinations by providing complete, accurate context

FIXES:
- Handles columns with AND without brackets
- Correct column type inference (identifier, categorical, numeric)
- Filters out calculated fields from base_columns_used
- CSV column mapping support
- Always overwrites to stay current

Version: 3.0 - Production Ready
"""

import json
import os
import re
from pathlib import Path
from typing import Dict, List, Set, Optional


class TableauColumnMappingExtractor:
    """
    Extracts column aliases and formulas from Tableau dashboard metadata.
    Maps original column names to their calculated field aliases for better LLM understanding.
    Enhanced to prevent hallucinations with complete, accurate context.
    """
    
    def __init__(self, metadata_dir: str, output_file: str):
        self.metadata_dir = Path(metadata_dir)
        self.output_file = Path(output_file)
        self.results = {
            "dashboards": [],
            "csv_column_mapping": {},  # Tableau → CSV column mapping
            "column_metadata": {},      # Column type and description metadata
            "function_glossary": self._get_tableau_function_glossary()
        }
        
        # Tableau keywords to exclude when parsing formulas
        self.tableau_keywords = {
            'IF', 'THEN', 'ELSE', 'END', 'AND', 'OR', 'NOT', 'NULL', 
            'SUM', 'COUNTD', 'COUNT', 'AVG', 'MIN', 'MAX', 'COALESCE',
            'CASE', 'WHEN', 'IN', 'IS', 'AS', 'TRUE', 'FALSE'
        }
    
    def _get_tableau_function_glossary(self) -> Dict[str, str]:
        """Provide Tableau → Pandas function translation glossary"""
        return {
            "COUNTD": "nunique() - Count distinct values",
            "SUM": "sum() - Sum all values",
            "AVG": "mean() - Average of values",
            "MIN": "min() - Minimum value",
            "MAX": "max() - Maximum value",
            "COALESCE": "fillna() - Return first non-null value",
            "IF...THEN...ELSE...END": "np.where() or df.apply() - Conditional logic",
            "COUNT": "count() - Count non-null values",
            "CONTAINS": "str.contains() - Check if string contains substring",
            "DATEPART": "dt.year, dt.month, etc. - Extract date parts",
            "DATEDIFF": "(date2 - date1).dt.days - Calculate date difference"
        }
    
    def extract_column_references(self, formula: str) -> Set[str]:
        """
        Extract ALL column references from a formula (with or without brackets).
        FIXED: Now handles both [column_name] and standalone column_name
        """
        columns = set()
        
        if not formula:
            return columns
        
        # Method 1: Columns in brackets [column_name]
        bracketed = re.findall(r'\[([^\]]+)\]', formula)
        for col in bracketed:
            col_clean = col.strip().lower().replace(' ', '_')
            # Skip if it's a Tableau function
            if col_clean.upper() not in self.tableau_keywords:
                columns.add(col_clean)
        
        # Method 2: Standalone words (columns without brackets)
        # Remove bracketed content first to avoid double-counting
        formula_no_brackets = re.sub(r'\[[^\]]+\]', '', formula)
        
        # Split by operators and whitespace
        words = re.split(r'[\s\+\-\*/\(\),=<>!&|]+', formula_no_brackets)
        
        for word in words:
            word = word.strip()
            
            if not word:
                continue
            
            # Skip Tableau keywords and functions
            if word.upper() in self.tableau_keywords:
                continue
            
            # Skip numbers (integers and decimals)
            if re.match(r'^-?\d+\.?\d*$', word):
                continue
            
            # Skip quoted strings
            if word.startswith("'") or word.startswith('"'):
                continue
            
            # Skip if it contains special characters (likely not a column name)
            if re.search(r'["\']', word):
                continue
            
            # This looks like a column name
            col_clean = word.lower().replace(' ', '_')
            columns.add(col_clean)
        
        return columns
    
    def get_all_base_columns_recursive(self, field_name: str, dependencies: Dict, all_calculated_names: Set[str]) -> Set[str]:
        """
        Recursively collect ONLY true base columns (not calculated fields).
        FIXED: Now properly filters out calculated fields from base columns.
        
        Args:
            field_name: The calculated field name
            dependencies: Dictionary of all calculated field dependencies
            all_calculated_names: Set of ALL calculated field names (for filtering)
            
        Returns:
            Set of TRUE base column names (not calculated fields)
        """
        base_cols = set()
        
        # Normalize field name for comparison
        field_name_normalized = field_name.lower().replace(' ', '_')
        
        # If this field is not in dependencies, it's a base column
        if field_name not in dependencies:
            # Double-check it's not a calculated field
            if field_name_normalized not in all_calculated_names:
                return {field_name_normalized}
            else:
                return set()
        
        field_info = dependencies[field_name]
        
        # Extract all columns from formula
        formula = field_info.get('formula', '')
        formula_cols = self.extract_column_references(formula)
        
        for col in formula_cols:
            col_normalized = col.lower().replace(' ', '_')
            
            # Check if this column is a calculated field
            is_calculated = False
            
            # Search in dependencies (exact match)
            for dep_name in dependencies.keys():
                dep_normalized = dep_name.lower().replace(' ', '_')
                if dep_normalized == col_normalized:
                    is_calculated = True
                    # Recursively get its base columns
                    base_cols.update(self.get_all_base_columns_recursive(dep_name, dependencies, all_calculated_names))
                    break
            
            # Also check in the global calculated names set
            if not is_calculated and col_normalized in all_calculated_names:
                is_calculated = True
            
            if not is_calculated:
                # This is a TRUE base column
                base_cols.add(col_normalized)
        
        return base_cols
    
    def analyze_calculated_field_dependencies(self, chart_calculated_fields: List[Dict]) -> tuple:
        """
        Analyze calculated fields to build dependency chains.
        Returns (dependencies dict, set of all calculated field names)
        """
        dependencies = {}
        all_calc_names = set()
        
        # First pass: collect all calculated field names
        for calc_field in chart_calculated_fields:
            field_name = calc_field['name']
            all_calc_names.add(field_name.lower().replace(' ', '_'))
        
        # Second pass: build dependencies
        for calc_field in chart_calculated_fields:
            field_name = calc_field['name']
            formula = calc_field.get('formula', '')
            datatype = calc_field.get('datatype', 'unknown')
            
            # Find which other calculated fields this depends on
            depends_on = []
            for other_calc in chart_calculated_fields:
                other_name = other_calc['name']
                if other_name != field_name:
                    # Check if other_name appears in formula (with or without brackets)
                    if f"[{other_name}]" in formula or other_name in formula:
                        depends_on.append(other_name)
            
            # Extract all column references
            all_refs = self.extract_column_references(formula)
            
            # Filter out calculated field names to get only base columns
            base_cols = [col for col in all_refs if col not in all_calc_names]
            
            dependencies[field_name] = {
                'formula': formula,
                'datatype': datatype,
                'depends_on': depends_on,
                'base_columns_used': base_cols,
                'level': 0
            }
        
        # Calculate dependency levels
        max_iterations = 10
        for _ in range(max_iterations):
            changed = False
            for field_name, info in dependencies.items():
                if info['depends_on']:
                    max_dep_level = max(
                        dependencies[dep]['level'] 
                        for dep in info['depends_on'] 
                        if dep in dependencies
                    )
                    new_level = max_dep_level + 1
                    if new_level != info['level']:
                        info['level'] = new_level
                        changed = True
            if not changed:
                break
        
        return dependencies, all_calc_names
    
    def _infer_column_type(self, column_name: str, datatype: str) -> str:
        """
        Infer column type from name and datatype.
        FIXED: Priority to name-based inference over datatype.
        """
        col_lower = column_name.lower()
        
        # Priority 1: Name-based inference (most reliable)
        if any(keyword in col_lower for keyword in ['date', 'time', 'month', 'year', 'day', 'week', 'created', 'updated']):
            return 'datetime'
        elif any(keyword in col_lower for keyword in ['id', 'number', 'case', 'ticket']):
            return 'identifier'
        elif any(keyword in col_lower for keyword in ['queue', 'status', 'country', 'type', 'category', 'state', 'priority']):
            return 'categorical'
        elif any(keyword in col_lower for keyword in ['count', 'amount', 'total', 'sum', 'avg', 'rate', 'percent', 'aht', 'volume']):
            return 'numeric'
        
        # Priority 2: Datatype-based inference
        elif datatype in ['integer', 'real', 'number']:
            return 'numeric'
        elif datatype in ['string', 'text']:
            return 'categorical'
        
        # Default
        return 'categorical'
    
    def _generate_column_description(self, column_name: str) -> str:
        """Generate human-readable column description"""
        readable = column_name.replace('_', ' ').title()
        
        col_lower = column_name.lower()
        if 'queue' in col_lower:
            return f"{readable} (ticket routing queue)"
        elif 'case' in col_lower or 'ticket' in col_lower:
            return f"{readable} (ticket identifier)"
        elif 'status' in col_lower or 'state' in col_lower:
            return f"{readable} (status/state value)"
        elif 'country' in col_lower:
            return f"{readable} (geographic location)"
        elif 'time' in col_lower or 'mins' in col_lower or 'aht' in col_lower:
            return f"{readable} (time measurement)"
        elif 'date' in col_lower or 'month' in col_lower or 'day' in col_lower:
            return f"{readable} (date/time value)"
        elif 'volume' in col_lower or 'count' in col_lower:
            return f"{readable} (count/volume metric)"
        else:
            return readable
    
    def process_dashboard(self, file_path: Path) -> Dict:
        """Process a single dashboard metadata file"""
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        dashboard_name = data.get('workbook_name', file_path.stem)
        charts_metadata = data.get('charts_metadata_readable', {})
        
        dashboard_result = {
            "dashboard_name": dashboard_name,
            "charts": []
        }
        
        # Process each chart
        for chart_name, chart_data in charts_metadata.items():
            # Build calculated field dependencies
            calculated_fields = chart_data.get('calculated_fields_ordered', [])
            dependencies, all_calc_names = self.analyze_calculated_field_dependencies(calculated_fields)
            
            # Get Y-axis information with COMPLETE base columns
            y_axis_fields = []
            y_axis_calc_details = chart_data.get('y_axis_calculation_details', [])
            
            for y_detail in y_axis_calc_details:
                field_name = y_detail.get('field_name', '')
                formula = y_detail.get('formula', '')
                datatype = y_detail.get('datatype', '')
                
                # ✅ FIX: Recursively collect ALL TRUE base columns (no calculated fields)
                all_base_columns = list(self.get_all_base_columns_recursive(field_name, dependencies, all_calc_names))
                
                # Get dependent calculated fields
                dependent_fields = []
                if field_name in dependencies:
                    for dep in dependencies[field_name]['depends_on']:
                        if dep in dependencies:
                            dependent_fields.append({
                                "alias": dep,
                                "formula": dependencies[dep]['formula'],
                                "datatype": dependencies[dep]['datatype']
                            })
                
                y_axis_fields.append({
                    "alias": field_name,
                    "formula": formula,
                    "datatype": datatype,
                    "depends_on_calculated_fields": dependent_fields,
                    "base_columns_used": all_base_columns
                })
            
            # Process all calculated fields with complete base column info
            all_calculated_fields = []
            for calc_field in calculated_fields:
                field_name = calc_field.get('name', '')
                formula = calc_field.get('formula', '')
                datatype = calc_field.get('datatype', '')
                
                # ✅ FIX: Recursively collect ALL TRUE base columns
                all_base_columns = list(self.get_all_base_columns_recursive(field_name, dependencies, all_calc_names))
                
                # Get dependent calculated fields
                dependent_fields = []
                if field_name in dependencies:
                    for dep in dependencies[field_name]['depends_on']:
                        if dep in dependencies:
                            dependent_fields.append({
                                "alias": dep,
                                "formula": dependencies[dep]['formula'],
                                "datatype": dependencies[dep]['datatype']
                            })
                
                field_info = {
                    "alias": field_name,
                    "formula": formula,
                    "datatype": datatype,
                    "depends_on_calculated_fields": dependent_fields,
                    "base_columns_used": all_base_columns,
                    "execution_order": dependencies.get(field_name, {}).get('level', 0)
                }
                
                all_calculated_fields.append(field_info)
                
                # ✅ Collect column metadata for LLM context (with correct types)
                for base_col in all_base_columns:
                    if base_col not in self.results['column_metadata']:
                        self.results['column_metadata'][base_col] = {
                            "type": self._infer_column_type(base_col, datatype),
                            "used_in_charts": [chart_name],
                            "description": self._generate_column_description(base_col)
                        }
                    else:
                        if chart_name not in self.results['column_metadata'][base_col]['used_in_charts']:
                            self.results['column_metadata'][base_col]['used_in_charts'].append(chart_name)
            
            # Build chart result
            chart_result = {
                "chart_name": chart_name,
                "x_axis": chart_data.get('x_axis', []),
                "y_axis_metrics": y_axis_fields,
                "all_calculated_fields": all_calculated_fields,
                "filters": chart_data.get('filters', []),
                "detail_fields": chart_data.get('detail_fields', []),
                "mark_type": chart_data.get('mark_type', '')
            }
            
            dashboard_result["charts"].append(chart_result)
        
        return dashboard_result
    
    def add_csv_column_mapping(self, csv_data_loader=None):
        """
        Add CSV column mappings to prevent column name mismatches.
        
        Args:
            csv_data_loader: Optional CSVDataLoader instance with fuzzy matching
        """
        if not csv_data_loader:
            print("⚠️ No CSV data loader provided - skipping CSV mappings")
            return
        
        try:
            # Get all unique base columns from all charts
            all_base_columns = set()
            for dashboard in self.results['dashboards']:
                for chart in dashboard['charts']:
                    for field in chart['all_calculated_fields']:
                        all_base_columns.update(field['base_columns_used'])
            
            print(f"🔍 Mapping {len(all_base_columns)} Tableau columns to CSV...")
            
            # Map each Tableau column to CSV column
            if hasattr(csv_data_loader, 'map_tableau_to_csv_columns'):
                for tableau_col in all_base_columns:
                    matched_csv_cols = csv_data_loader.map_tableau_to_csv_columns([tableau_col])
                    if matched_csv_cols and len(matched_csv_cols) > 0:
                        self.results['csv_column_mapping'][tableau_col] = matched_csv_cols[0]
                        print(f"   ✅ {tableau_col} → {matched_csv_cols[0]}")
            
            print(f"✅ CSV column mapping completed: {len(self.results['csv_column_mapping'])} mappings")
        
        except Exception as e:
            print(f"⚠️ Could not add CSV mappings: {e}")
    
    def process_all_dashboards(self):
        """Process all JSON files in the metadata directory"""
        json_files = list(self.metadata_dir.glob('*_metadata.json'))
        
        if not json_files:
            print(f"❌ No metadata JSON files found in {self.metadata_dir}")
            return
        
        print(f"📁 Found {len(json_files)} dashboard metadata files")
        
        # ✅ CLEAR existing dashboards to ensure fresh data
        self.results["dashboards"] = []
        self.results["column_metadata"] = {}
        self.results["csv_column_mapping"] = {}
        
        for json_file in json_files:
            print(f"📊 Processing: {json_file.name}")
            dashboard_result = self.process_dashboard(json_file)
            self.results["dashboards"].append(dashboard_result)
        
        # Add summary statistics
        total_charts = sum(len(d["charts"]) for d in self.results["dashboards"])
        
        self.results["summary"] = {
            "total_dashboards": len(self.results["dashboards"]),
            "total_charts": total_charts,
            "total_unique_columns": len(self.results["column_metadata"]),
            "dashboards_processed": [d["dashboard_name"] for d in self.results["dashboards"]],
            "generation_timestamp": self._get_timestamp()
        }
    
    def _get_timestamp(self) -> str:
        """Get current timestamp for tracking"""
        from datetime import datetime
        return datetime.now().isoformat()
    
    def save_results(self):
        """Save results to JSON file - OVERWRITES to ensure relevance"""
        self.output_file.parent.mkdir(parents=True, exist_ok=True)
        
        # ✅ OVERWRITE mode (not append) to ensure file is always current
        with open(self.output_file, 'w', encoding='utf-8') as f:
            json.dump(self.results, f, indent=2, ensure_ascii=False)
        
        print(f"\n{'='*80}")
        print(f"✅ RESULTS SAVED SUCCESSFULLY")
        print(f"{'='*80}")
        print(f"📄 Output file: {self.output_file}")
        print(f"📊 Dashboards: {self.results['summary']['total_dashboards']}")
        print(f"📈 Charts: {self.results['summary']['total_charts']}")
        print(f"🗂️ Unique columns: {len(self.results['column_metadata'])}")
        print(f"🔗 CSV mappings: {len(self.results['csv_column_mapping'])}")
        print(f"💾 File mode: OVERWRITE (always relevant to current dashboard)")
        print(f"{'='*80}\n")
    
    def print_summary(self):
        """Print a summary of extracted mappings"""
        print("\n" + "="*80)
        print("COLUMN MAPPING SUMMARY")
        print("="*80)
        
        for dashboard in self.results["dashboards"]:
            print(f"\n📊 Dashboard: {dashboard['dashboard_name']}")
            print(f"   Charts: {len(dashboard['charts'])}")
            
            for chart in dashboard['charts'][:3]:
                print(f"\n   📈 Chart: {chart['chart_name']}")
                print(f"      Y-axis metrics: {len(chart['y_axis_metrics'])}")
                
                for metric in chart['y_axis_metrics'][:2]:
                    print(f"         • {metric['alias']}")
                    if metric['base_columns_used']:
                        base_cols_str = ', '.join(metric['base_columns_used'][:5])
                        if len(metric['base_columns_used']) > 5:
                            base_cols_str += f", ... ({len(metric['base_columns_used']) - 5} more)"
                        print(f"           Base columns: {base_cols_str}")
                    if metric['depends_on_calculated_fields']:
                        deps_str = ', '.join([f['alias'] for f in metric['depends_on_calculated_fields']])
                        print(f"           Depends on: {deps_str}")
            
            if len(dashboard['charts']) > 3:
                print(f"\n   ... and {len(dashboard['charts']) - 3} more charts")
        
        print("\n" + "="*80)
        print(f"📊 Total unique base columns: {len(self.results['column_metadata'])}")
        print(f"🔗 CSV column mappings: {len(self.results['csv_column_mapping'])}")
        
        # Show column type distribution
        type_counts = {}
        for col_info in self.results['column_metadata'].values():
            col_type = col_info['type']
            type_counts[col_type] = type_counts.get(col_type, 0) + 1
        
        print(f"\n📋 Column Type Distribution:")
        for col_type, count in sorted(type_counts.items()):
            print(f"   {col_type}: {count}")
        
        print("="*80)


def main():
    """Main execution function"""
    # Configuration
    METADATA_DIR = "/Users/aranja14/Desktop/New folder 2/tableau_metadata"
    OUTPUT_FILE = "/Users/aranja14/Desktop/New folder 2/chart_column_mappings.json"
    
    print("="*80)
    print("TABLEAU COLUMN MAPPING EXTRACTOR - PRODUCTION v3.0")
    print("="*80)
    print(f"📂 Input directory: {METADATA_DIR}")
    print(f"📄 Output file: {OUTPUT_FILE}")
    print(f"💾 Mode: OVERWRITE (ensures relevance to current dashboard)")
    print(f"🔧 Features:")
    print(f"   ✅ Handles columns with/without brackets")
    print(f"   ✅ Correct column type inference")
    print(f"   ✅ Filters calculated fields from base columns")
    print(f"   ✅ CSV column mapping support")
    print("="*80 + "\n")
    
    # Create extractor and process
    extractor = TableauColumnMappingExtractor(METADATA_DIR, OUTPUT_FILE)
    extractor.process_all_dashboards()
    
    # Optional: Add CSV mappings if CSVDataLoader is available
    try:
        from services.csv_data_loader import CSVDataLoader
        csv_loader = CSVDataLoader()
        if csv_loader.load_data():
            print("\n🔗 Adding CSV column mappings...")
            extractor.add_csv_column_mapping(csv_loader)
        else:
            print("\n⚠️ CSV data not loaded - skipping CSV mappings")
    except ImportError:
        print("\n⚠️ CSVDataLoader not available - skipping CSV mappings")
    except Exception as e:
        print(f"\n⚠️ Could not load CSV data: {e}")
    
    # Save and print summary
    extractor.save_results()
    extractor.print_summary()
    
    print("\n" + "="*80)
    print("✅ EXTRACTION COMPLETE - READY FOR LLM QUERIES")
    print("="*80)


if __name__ == "__main__":
    main()