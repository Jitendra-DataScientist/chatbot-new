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
from datetime import datetime
from pathlib import Path


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
    
    def _get_tableau_function_glossary(self) -> dict[str, str]:
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
    
    def extract_column_references(self, formula: str) -> set[str]:
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
            if word.startswith(("'", '"')):
                continue
            
            # Skip if it contains special characters (likely not a column name)
            if re.search(r'["\']', word):
                continue
            
            # This looks like a column name
            col_clean = word.lower().replace(' ', '_')
            columns.add(col_clean)
        
        return columns
    
    def get_all_base_columns_iterative(self, field_name: str, dependencies: dict, all_calculated_names: set[str]) -> set[str]:
        """
        Iteratively collect ONLY true base columns using stack-based graph traversal.
        Handles circular dependencies gracefully without recursion limits.

        This treats the problem as directed graph traversal:
        - Nodes: field names
        - Edges: dependencies (A uses B means A→B edge)
        - Goal: find all leaf nodes (base columns)
        - Challenge: cycles must be detected and handled

        Args:
            field_name: The calculated field name
            dependencies: Dictionary of all calculated field dependencies
            all_calculated_names: Set of ALL calculated field names (for filtering)

        Returns:
            Set of TRUE base column names (not calculated fields)
        """
        base_cols = set()
        stack = [field_name]
        visited = set()  # Track all visited nodes globally

        while stack:
            current_field = stack.pop()

            # Skip if already processed (cycle detection)
            if current_field in visited:
                continue

            visited.add(current_field)

            # Normalize for comparison
            current_normalized = current_field.lower().replace(' ', '_')

            # Check if this field is in dependencies dict
            if current_field not in dependencies:
                # Not a calculated field in our dependency graph
                if current_normalized not in all_calculated_names:
                    # This is a TRUE base column
                    base_cols.add(current_normalized)
                continue

            # It's a calculated field - process its formula
            field_info = dependencies[current_field]
            formula = field_info.get('formula', '')
            formula_cols = self.extract_column_references(formula)

            for col in formula_cols:
                col_normalized = col.lower().replace(' ', '_')

                # Find if this is a calculated field
                is_calculated = False
                matching_calc_field = None

                for dep_name in dependencies.keys():
                    dep_normalized = dep_name.lower().replace(' ', '_')
                    if dep_normalized == col_normalized:
                        is_calculated = True
                        matching_calc_field = dep_name
                        break

                if is_calculated and matching_calc_field not in visited:
                    # Add to stack for processing (will be skipped if creates cycle)
                    stack.append(matching_calc_field)
                elif col_normalized not in all_calculated_names:
                    # It's a base column
                    base_cols.add(col_normalized)

        return base_cols

    def calculate_dependency_levels(self, dependencies: dict) -> None:
        """
        Calculate dependency levels using Kahn's algorithm (topological sort).
        Handles cycles gracefully by assigning them max level + 1.
        """
        # Initialize all levels to 0
        for field_name in dependencies:
            dependencies[field_name]['level'] = 0

        # Count incoming edges for each node
        in_degree = {name: 0 for name in dependencies}
        for field_name, info in dependencies.items():
            for dep in info['depends_on']:
                if dep in in_degree:
                    in_degree[dep] += 1

        # Queue of nodes with no incoming edges
        queue = [name for name, degree in in_degree.items() if degree == 0]
        processed = []

        # Process nodes level by level
        while queue:
            # Sort for deterministic ordering
            queue.sort()
            current = queue.pop(0)
            processed.append(current)

            # Check dependencies and update levels
            for field_name, info in dependencies.items():
                if current in info['depends_on']:
                    # Update level: max of all dependencies + 1
                    current_level = dependencies[current]['level']
                    dependencies[field_name]['level'] = max(
                        dependencies[field_name]['level'],
                        current_level + 1
                    )

                    in_degree[field_name] -= 1
                    if in_degree[field_name] == 0:
                        queue.append(field_name)

        # Handle cycles: any unprocessed nodes are in cycles
        if len(processed) < len(dependencies):
            max_level = max(info['level'] for info in dependencies.values())
            cycle_nodes = set(dependencies.keys()) - set(processed)

            print(f"⚠️  Detected circular dependencies in fields: {cycle_nodes}")

            # Assign cycle nodes to max_level + 1
            for node in cycle_nodes:
                dependencies[node]['level'] = max_level + 1
                dependencies[node]['has_cycle'] = True

    def analyze_calculated_field_dependencies(self, chart_calculated_fields: list[dict]) -> tuple:
        """
        Analyze calculated fields to build dependency chains.
        Uses cycle-safe algorithms throughout.
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
                    # Check if other_name appears in formula
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
                'level': 0,
                'has_cycle': False
            }

        # Calculate dependency levels using topological sort
        self.calculate_dependency_levels(dependencies)

        return dependencies, all_calc_names

    def _extract_aggregations_from_formula(self, formula: str) -> dict[str, list[str]]:
        """
        Extract aggregation functions and their target columns from a formula.

        Returns:
            Dict mapping column names to list of aggregations used on them
            Example: {"twc": ["SUM", "SUM"], "case_id": ["COUNTD"]}
        """
        if not formula:
            return {}

        aggregations = {}

        # Tableau aggregation functions to detect
        agg_functions = ['SUM', 'COUNT', 'COUNTD', 'AVG', 'MIN', 'MAX', 'MEDIAN', 'STDEV', 'VAR']

        for agg_func in agg_functions:
            # Pattern: AGG_FUNC([column_name])
            # Use regex to find all instances
            pattern = rf'{agg_func}\s*\(\s*\[([^\]]+)\]\s*\)'
            matches = re.findall(pattern, formula, re.IGNORECASE)

            for col_name in matches:
                col_normalized = col_name.strip().lower().replace(' ', '_')

                if col_normalized not in aggregations:
                    aggregations[col_normalized] = []
                aggregations[col_normalized].append(agg_func.upper())

        return aggregations

    def _analyze_aggregation_semantics(self) -> dict[str, dict]:
        """
        Analyze all formulas to determine typical aggregation for each base column.

        Returns:
            Dict mapping column names to aggregation statistics
            Example: {
                "twc": {
                    "aggregations": {"SUM": 47, "AVG": 3},
                    "most_common": "SUM",
                    "usage_frequency": 50,
                    "typical_aggregation": "SUM"
                }
            }
        """
        aggregation_stats = {}

        # Analyze all calculated fields across all dashboards
        for dashboard in self.results.get('dashboards', []):
            for chart in dashboard.get('charts', []):
                for calc_field in chart.get('all_calculated_fields', []):
                    formula = calc_field.get('formula', '')

                    # Extract aggregations from this formula
                    formula_aggs = self._extract_aggregations_from_formula(formula)

                    # Accumulate statistics
                    for col_name, agg_list in formula_aggs.items():
                        if col_name not in aggregation_stats:
                            aggregation_stats[col_name] = {}

                        for agg in agg_list:
                            if agg not in aggregation_stats[col_name]:
                                aggregation_stats[col_name][agg] = 0
                            aggregation_stats[col_name][agg] += 1

        # Process statistics to determine typical aggregation
        semantic_data = {}
        for col_name, agg_counts in aggregation_stats.items():
            if not agg_counts:
                continue

            # Find most common aggregation
            most_common = max(agg_counts.items(), key=lambda x: x[1])
            total_uses = sum(agg_counts.values())

            semantic_data[col_name] = {
                "aggregations": agg_counts,
                "most_common_aggregation": most_common[0],
                "most_common_count": most_common[1],
                "total_aggregation_uses": total_uses,
                "typical_aggregation": most_common[0],  # For easy access
                "confidence": round(most_common[1] / total_uses, 2) if total_uses > 0 else 0.0
            }

        return semantic_data

    def _infer_column_type_from_usage(self, column_name: str, aggregation_semantic: dict) -> str:
        """
        Infer column type from how it's used in formulas.
        More reliable than keyword-based inference.

        Args:
            column_name: The column name
            aggregation_semantic: Aggregation semantic data for this column

        Returns:
            Column type: 'numeric', 'identifier', 'categorical', 'datetime'
        """
        col_lower = column_name.lower()

        # Priority 1: Usage-based inference (most reliable)
        if aggregation_semantic:
            typical_agg = aggregation_semantic.get('typical_aggregation', '')

            # Columns used with SUM, AVG, MIN, MAX are numeric
            if typical_agg in ['SUM', 'AVG', 'MIN', 'MAX', 'MEDIAN', 'STDEV', 'VAR']:
                return 'numeric'

            # Columns used with COUNTD are likely identifiers
            elif typical_agg == 'COUNTD':
                return 'identifier'

            # Columns used with COUNT might be categorical
            elif typical_agg == 'COUNT':
                return 'categorical'

        # Priority 2: Name-based inference
        if any(keyword in col_lower for keyword in ['date', 'time', 'month', 'year', 'day', 'week', 'created', 'updated']):
            return 'datetime'
        elif any(keyword in col_lower for keyword in ['id', 'number', 'case', 'ticket']):
            return 'identifier'
        elif any(keyword in col_lower for keyword in ['queue', 'status', 'country', 'type', 'category', 'state', 'priority']):
            return 'categorical'
        elif any(keyword in col_lower for keyword in ['count', 'amount', 'total', 'sum', 'avg', 'rate', 'percent', 'aht', 'volume', 'twc', 'wwc', 'cost', 'spend', 'price']):
            return 'numeric'

        # Default
        return 'categorical'

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

    def _is_valid_metadata_file(self, file_path: Path) -> bool:
        """
        Validate that a JSON file is actually a Tableau metadata file.
        Checks for expected structure rather than filename pattern.
        """
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            # Check for required Tableau metadata keys
            required_keys = ['charts_metadata_readable']
            optional_keys = ['workbook_name', 'dashboards', 'worksheets']

            # File must have at least one required key or multiple optional keys
            has_required = any(key in data for key in required_keys)
            has_optional = sum(1 for key in optional_keys if key in data) >= 2

            return has_required or has_optional

        except (json.JSONDecodeError, IOError, Exception) as e:
            print(f"⚠️  Cannot validate {file_path.name}: {e}")
            return False

    def process_dashboard(self, file_path: Path) -> dict:
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
                
                # ✅ FIX: Iteratively collect ALL TRUE base columns (no calculated fields)
                all_base_columns = list(self.get_all_base_columns_iterative(field_name, dependencies, all_calc_names))
                
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
                
                # ✅ FIX: Iteratively collect ALL TRUE base columns
                all_base_columns = list(self.get_all_base_columns_iterative(field_name, dependencies, all_calc_names))
                
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
        # Initialize summary immediately to prevent KeyError
        self.results["summary"] = {
            "total_dashboards": 0,
            "total_charts": 0,
            "total_unique_columns": 0,
            "dashboards_processed": [],
            "generation_timestamp": self._get_timestamp(),
            "errors": []
        }

        # Clear existing data
        self.results["dashboards"] = []
        self.results["column_metadata"] = {}
        self.results["csv_column_mapping"] = {}

        # Recursive search for ALL JSON files
        json_files = list(self.metadata_dir.rglob('*.json'))

        if not json_files:
            error_msg = f"No JSON files found in {self.metadata_dir} or its subdirectories"
            print(f"❌ {error_msg}")
            self.results["summary"]["errors"].append(error_msg)
            return

        print(f"📁 Found {len(json_files)} JSON files, validating...")

        # Validate and filter for actual metadata files
        valid_metadata_files = []
        for json_file in json_files:
            if self._is_valid_metadata_file(json_file):
                valid_metadata_files.append(json_file)
            else:
                print(f"⏭️  Skipping non-metadata file: {json_file.name}")

        if not valid_metadata_files:
            error_msg = f"No valid Tableau metadata files found in {len(json_files)} JSON files"
            print(f"❌ {error_msg}")
            self.results["summary"]["errors"].append(error_msg)
            return

        print(f"✅ Found {len(valid_metadata_files)} valid metadata files")

        # Process each valid metadata file
        for json_file in valid_metadata_files:
            try:
                print(f"📊 Processing: {json_file.name}")
                dashboard_result = self.process_dashboard(json_file)
                self.results["dashboards"].append(dashboard_result)
            except Exception as e:
                error_msg = f"Failed to process {json_file.name}: {str(e)}"
                print(f"❌ {error_msg}")
                self.results["summary"]["errors"].append(error_msg)
                # Continue processing other files instead of failing completely

        # ✅ NEW: Analyze aggregation semantics across all formulas
        print(f"\n🔍 Analyzing aggregation semantics from formulas...")
        aggregation_semantics = self._analyze_aggregation_semantics()
        print(f"   Found aggregation patterns for {len(aggregation_semantics)} columns")

        # ✅ NEW: Enhance column_metadata with aggregation semantics and fix types
        for col_name, col_meta in self.results["column_metadata"].items():
            agg_semantic = aggregation_semantics.get(col_name, {})

            # Add aggregation semantic information
            if agg_semantic:
                col_meta["aggregation_semantics"] = agg_semantic

                # Update column type based on usage (more reliable than keywords)
                updated_type = self._infer_column_type_from_usage(col_name, agg_semantic)
                if updated_type != col_meta["type"]:
                    print(f"   ✓ Updated '{col_name}' type: {col_meta['type']} → {updated_type} (based on {agg_semantic['typical_aggregation']} usage)")
                    col_meta["type"] = updated_type
            else:
                col_meta["aggregation_semantics"] = None

        # Update summary statistics
        total_charts = sum(len(d["charts"]) for d in self.results["dashboards"])
        columns_with_semantics = sum(1 for col in self.results["column_metadata"].values() if col.get("aggregation_semantics"))

        self.results["summary"].update({
            "total_dashboards": len(self.results["dashboards"]),
            "total_charts": total_charts,
            "total_unique_columns": len(self.results["column_metadata"]),
            "columns_with_aggregation_semantics": columns_with_semantics,
            "dashboards_processed": [d["dashboard_name"] for d in self.results["dashboards"]]
        })
    
    def _get_timestamp(self) -> str:
        """Get current timestamp for tracking"""
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