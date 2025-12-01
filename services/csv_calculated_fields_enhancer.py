"""
CSV Calculated Fields Enhancer for Tableau Analytics Agent

This service identifies non-aggregated calculated fields from Tableau metadata
and adds them as new columns to exported CSV files.
"""

import pandas as pd
import json
import re
import numpy as np
from typing import Dict, List, Any, Optional, Tuple
import logging
from pathlib import Path

# Setup basic logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

class TableauFormulaTranslator:
    """Translates Tableau formulas to pandas operations"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        
        # Map Tableau field names to CSV column names (case-insensitive)
        self.field_mapping = {}
        
        # Tableau function mappings to pandas
        self.function_mappings = {
            'COALESCE': 'fillna',
            'ISNULL': 'isna',
            'IF': self._translate_if_statement,
            'CASE': self._translate_case_statement
        }
    
    def build_field_mapping(self, csv_columns: List[str]) -> Dict[str, str]:
        """Build mapping between Tableau field names and CSV columns"""
        mapping = {}
        csv_lower = [col.lower() for col in csv_columns]
        
        for col in csv_columns:
            # Direct mapping
            mapping[col.lower()] = col
            
            # Common variations
            col_variations = [
                col.replace('_', ''),
                col.replace(' ', '_'),
                col.replace(' ', ''),
                col.replace('-', '_')
            ]
            
            for variation in col_variations:
                mapping[variation.lower()] = col
        
        self.field_mapping = mapping
        return mapping
    
    def _normalize_field_name(self, field_name: str) -> Optional[str]:
        """Convert Tableau field reference to CSV column name"""
        # Remove brackets and clean field name
        cleaned = re.sub(r'[\[\]]', '', field_name).lower()
        
        return self.field_mapping.get(cleaned)
    
    def _translate_if_statement(self, formula: str, df: pd.DataFrame) -> pd.Series:
        """Translate Tableau IF statement to pandas conditional logic"""
        # Pattern: IF condition THEN value [ELSE else_value] END
        if_pattern = r'IF\s+(.+?)\s+THEN\s+(.+?)(?:\s+ELSE\s+(.+?))?\s+END'
        
        # FIXED: Don't convert to uppercase - preserve original case for field names and values
        match = re.search(if_pattern, formula, re.DOTALL | re.IGNORECASE)
        if not match:
            raise ValueError(f"Could not parse IF statement: {formula}")
        
        condition_str = match.group(1)
        then_value = match.group(2)
        else_value = match.group(3) if match.group(3) else 'None'
        
        # Parse condition
        condition = self._parse_condition(condition_str, df)
        
        # Parse values
        then_val = self._parse_value(then_value, df)
        else_val = self._parse_value(else_value, df) if else_value != 'None' else None
        
        # Apply conditional logic
        return np.where(condition, then_val, else_val)
    
    def _parse_condition(self, condition_str: str, df: pd.DataFrame) -> pd.Series:
        """Parse condition string into pandas boolean Series"""
        # Handle common operators
        condition_str = condition_str.strip()
        
        # Pattern: [field] = "value" or [field] != "value"
        comparison_pattern = r'\[?([^[\]]+)\]?\s*(=|!=|<|>|<=|>=)\s*["\']?([^"\']+)["\']?'
        match = re.search(comparison_pattern, condition_str)
        
        if match:
            field_name = match.group(1)
            operator = match.group(2)
            value = match.group(3)
            
            csv_col = self._normalize_field_name(field_name)
            if csv_col is None:
                raise ValueError(f"Field '{field_name}' not found in CSV columns")
            
            if operator == '=':
                return df[csv_col] == value
            elif operator == '!=':
                return df[csv_col] != value
            elif operator == '<':
                return df[csv_col] < float(value)
            elif operator == '>':
                return df[csv_col] > float(value)
            elif operator == '<=':
                return df[csv_col] <= float(value)
            elif operator == '>=':
                return df[csv_col] >= float(value)
        
        # Handle OR conditions
        if re.search(r'\s+OR\s+', condition_str, re.IGNORECASE):
            conditions = re.split(r'\s+OR\s+', condition_str, flags=re.IGNORECASE)
            result = self._parse_condition(conditions[0], df)
            for cond in conditions[1:]:
                result = result | self._parse_condition(cond, df)
            return result
        
        # Handle AND conditions  
        if re.search(r'\s+AND\s+', condition_str, re.IGNORECASE):
            conditions = re.split(r'\s+AND\s+', condition_str, flags=re.IGNORECASE)
            result = self._parse_condition(conditions[0], df)
            for cond in conditions[1:]:
                result = result & self._parse_condition(cond, df)
            return result
        
        raise ValueError(f"Could not parse condition: {condition_str}")
    
    def _parse_value(self, value_str: str, df: pd.DataFrame) -> Any:
        """Parse value string to actual value or pandas Series"""
        value_str = value_str.strip()
        
        # Check if it's a field reference
        if value_str.startswith('[') and value_str.endswith(']'):
            field_name = value_str[1:-1]
            csv_col = self._normalize_field_name(field_name)
            if csv_col:
                return df[csv_col]
        
        # Check if it's a quoted string
        if (value_str.startswith('"') and value_str.endswith('"')) or \
           (value_str.startswith("'") and value_str.endswith("'")):
            return value_str[1:-1]
        
        # Check if it's a number
        try:
            if '.' in value_str:
                return float(value_str)
            return int(value_str)
        except ValueError:
            pass
        
        # Check if it's NULL/None
        if value_str.upper() in ['NULL', 'NONE', '\\N']:
            return None
        
        # Default to field reference without brackets
        csv_col = self._normalize_field_name(value_str)
        if csv_col:
            return df[csv_col]
        
        # Return as string literal
        return value_str
    
    def _translate_case_statement(self, formula: str, df: pd.DataFrame) -> pd.Series:
        """Translate CASE statement (not implemented in current data)"""
        raise NotImplementedError("CASE statements not yet implemented")
    
    def translate_formula(self, formula: str, df: pd.DataFrame) -> pd.Series:
        """Main method to translate Tableau formula to pandas operation"""
        try:
            formula = formula.strip()
            
            # Handle IF statements
            if formula.upper().startswith('IF '):
                return self._translate_if_statement(formula, df)
            
            # Handle mathematical expressions
            if any(op in formula for op in ['+', '-', '*', '/', '(', ')']):
                return self._translate_mathematical_expression(formula, df)
            
            # Handle simple field references
            if formula.startswith('[') and formula.endswith(']'):
                field_name = formula[1:-1]
                csv_col = self._normalize_field_name(field_name)
                if csv_col:
                    return df[csv_col]
            
            raise ValueError(f"Unsupported formula type: {formula}")
            
        except Exception as e:
            self.logger.error(f"Error translating formula '{formula}': {e}")
            raise
    
    def _translate_mathematical_expression(self, formula: str, df: pd.DataFrame) -> pd.Series:
        """Translate mathematical expressions with COALESCE, etc."""
        # Replace COALESCE functions
        coalesce_pattern = r'COALESCE\(([^,]+),\s*([^)]+)\)'
        
        def replace_coalesce(match):
            field_ref = match.group(1).strip()
            default_val = match.group(2).strip()
            
            csv_col = self._normalize_field_name(field_ref)
            if csv_col:
                return f"df['{csv_col}'].fillna({default_val})"
            return f"{field_ref}.fillna({default_val})"
        
        formula = re.sub(coalesce_pattern, replace_coalesce, formula)
        
        # Replace field references
        field_pattern = r'\[([^\]]+)\]'
        
        def replace_field(match):
            field_name = match.group(1)
            csv_col = self._normalize_field_name(field_name)
            if csv_col:
                return f"df['{csv_col}']"
            return f"[{field_name}]"  # Keep original if not found
        
        formula = re.sub(field_pattern, replace_field, formula)
        
        try:
            # Evaluate the expression
            result = eval(formula)
            return result
        except Exception as e:
            self.logger.error(f"Error evaluating mathematical expression '{formula}': {e}")
            raise


class CSVCalculatedFieldsEnhancer:
    """Main service to enhance CSV files with calculated fields"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.translator = TableauFormulaTranslator()
    
    def identify_non_aggregated_fields(self, metadata: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Extract all non-aggregated calculated fields from metadata"""
        non_aggregated_fields = []
        
        # Aggregation functions to check for
        aggregation_functions = [
            'SUM', 'AVG', 'COUNT', 'COUNTD', 'MIN', 'MAX', 
            'MEDIAN', 'STDEV', 'VAR', 'PERCENTILE', 'ATTR'
        ]
        
        charts_metadata = metadata.get('charts_metadata_readable', {})
        
        # Track unique calculated fields (avoid duplicates)
        seen_fields = set()
        
        for chart_name, chart_data in charts_metadata.items():
            calculated_fields = chart_data.get('calculated_fields_ordered', [])
            
            for calc_field in calculated_fields:
                field_name = calc_field.get('name', '')
                formula = calc_field.get('formula', '')
                
                # Skip if already processed
                if field_name in seen_fields:
                    continue
                
                # Check if formula contains aggregation functions
                is_aggregated = any(
                    re.search(rf'\b{func}\s*\(', formula.upper()) 
                    for func in aggregation_functions
                )
                
                if not is_aggregated:
                    non_aggregated_fields.append({
                        'name': field_name,
                        'formula': formula,
                        'datatype': calc_field.get('datatype', 'string'),
                        'source_chart': chart_name
                    })
                    seen_fields.add(field_name)
                    self.logger.info(f"Found non-aggregated field: {field_name}")
        
        return non_aggregated_fields
    
    def enhance_csv_with_calculated_fields(
        self, 
        csv_path: str, 
        metadata_path: str, 
        output_path: Optional[str] = None
    ) -> str:
        """Main method to enhance CSV with calculated fields"""
        
        # Load metadata
        with open(metadata_path, 'r') as f:
            metadata = json.load(f)
        
        # Load CSV
        df = pd.read_csv(csv_path)
        original_columns = df.columns.tolist()
        
        self.logger.info(f"Loaded CSV with {len(df)} rows and {len(df.columns)} columns")
        
        # Build field mapping
        self.translator.build_field_mapping(df.columns.tolist())
        
        # Identify non-aggregated calculated fields
        calc_fields = self.identify_non_aggregated_fields(metadata)
        
        if not calc_fields:
            self.logger.warning("No non-aggregated calculated fields found")
            return csv_path
        
        # Add calculated fields to DataFrame
        added_columns = []
        
        for field in calc_fields:
            field_name = field['name']
            formula = field['formula']
            
            try:
                self.logger.info(f"Processing field: {field_name}")
                self.logger.info(f"Formula: {formula}")
                
                # Translate and apply formula
                calculated_series = self.translator.translate_formula(formula, df)
                
                # Add to DataFrame
                df[field_name] = calculated_series
                added_columns.append(field_name)
                
                self.logger.info(f"Successfully added calculated field: {field_name}")
                
            except Exception as e:
                self.logger.error(f"Failed to process field '{field_name}': {e}")
                continue
        
        # Save enhanced CSV
        if output_path is None:
            # Create new filename with suffix
            path_obj = Path(csv_path)
            output_path = str(path_obj.parent / f"{path_obj.stem}_enhanced{path_obj.suffix}")
        
        df.to_csv(output_path, index=False)
        
        self.logger.info(f"Enhanced CSV saved to: {output_path}")
        self.logger.info(f"Added {len(added_columns)} calculated columns: {added_columns}")
        self.logger.info(f"Total columns: {len(original_columns)} -> {len(df.columns)}")
        
        return output_path
    
    def get_calculated_fields_summary(self, metadata_path: str) -> Dict[str, Any]:
        """Get summary of all calculated fields (aggregated and non-aggregated)"""
        
        with open(metadata_path, 'r') as f:
            metadata = json.load(f)
        
        all_fields = []
        aggregated_fields = []
        non_aggregated_fields = self.identify_non_aggregated_fields(metadata)
        
        # Get aggregated fields for comparison
        aggregation_functions = ['SUM', 'AVG', 'COUNT', 'COUNTD', 'MIN', 'MAX', 'MEDIAN', 'STDEV', 'VAR']
        charts_metadata = metadata.get('charts_metadata_readable', {})
        seen_fields = set()
        
        for chart_name, chart_data in charts_metadata.items():
            calculated_fields = chart_data.get('calculated_fields_ordered', [])
            
            for calc_field in calculated_fields:
                field_name = calc_field.get('name', '')
                formula = calc_field.get('formula', '')
                
                if field_name in seen_fields:
                    continue
                
                is_aggregated = any(
                    re.search(rf'\b{func}\s*\(', formula.upper()) 
                    for func in aggregation_functions
                )
                
                field_info = {
                    'name': field_name,
                    'formula': formula,
                    'datatype': calc_field.get('datatype', 'string'),
                    'source_chart': chart_name,
                    'is_aggregated': is_aggregated
                }
                
                all_fields.append(field_info)
                
                if is_aggregated:
                    aggregated_fields.append(field_info)
                
                seen_fields.add(field_name)
        
        return {
            'total_calculated_fields': len(all_fields),
            'non_aggregated_fields': len(non_aggregated_fields),
            'aggregated_fields': len(aggregated_fields),
            'non_aggregated_list': non_aggregated_fields,
            'aggregated_list': aggregated_fields,
            'all_fields': all_fields
        }


# Example usage and testing
if __name__ == "__main__":
    enhancer = CSVCalculatedFieldsEnhancer()
    
    # Test paths
    csv_path = "tableau_exports/FRO Dashboard_new_updated/datasources/GFi7H5K2D (1).csv"
    metadata_path = "tableau_metadata/FRO_Dashboard_new_updated_metadata.json"
    
    # Get summary first
    summary = enhancer.get_calculated_fields_summary(metadata_path)
    print("\n=== CALCULATED FIELDS SUMMARY ===")
    print(f"Total calculated fields: {summary['total_calculated_fields']}")
    print(f"Non-aggregated fields: {summary['non_aggregated_fields']}")
    print(f"Aggregated fields: {summary['aggregated_fields']}")
    
    print("\n=== NON-AGGREGATED FIELDS ===")
    for field in summary['non_aggregated_list']:
        print(f"- {field['name']}: {field['formula']}")
    
    print("\n=== AGGREGATED FIELDS ===")
    for field in summary['aggregated_list']:
        print(f"- {field['name']}: {field['formula']}")
    
    # Enhance CSV
    try:
        output_path = enhancer.enhance_csv_with_calculated_fields(csv_path, metadata_path)
        print(f"\nEnhanced CSV created: {output_path}")
    except Exception as e:
        print(f"Error enhancing CSV: {e}")
