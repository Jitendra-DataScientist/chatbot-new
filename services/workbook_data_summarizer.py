"""
Workbook Data Summarizer Service

Generates and caches workbook-level data summaries including:
- Executive summary (1-2 lines from LLM)
- Numerical column statistics (df.describe())
- Categorical column statistics (df.describe())
- Calculated field formulas (from metadata)
"""

import pandas as pd
import numpy as np
import json
import os
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime
import logging
from master_logger import setup_module_logger

# Adaptive thresholds for column categorization (from causal_feature_importance files)
MIN_SAMPLES_PER_CATEGORY = 10   # Statistical minimum for stable estimates
MAX_SPARSITY_RATIO = 0.5        # >50% unique values = identifier column
MAX_DOMINANCE_RATIO = 0.95      # One value can't dominate >95% of rows
STRING_NUMERIC_THRESHOLD = 0.8  # >80% conversion success = numeric column


class WorkbookDataSummarizer:
    """Generates and caches workbook data summaries"""
    
    def __init__(self, cache_file: str = "workbook_data_summary.json"):
        """
        Initialize the summarizer
        
        Args:
            cache_file: Path to JSON cache file
        """
        self.logger = setup_module_logger('services.workbook_data_summarizer')
        self.cache_file = cache_file
        self.cache = self._load_cache()
    
    def _load_cache(self) -> Dict[str, Any]:
        """Load cache from JSON file"""
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, 'r', encoding='utf-8') as f:
                    cache = json.load(f)
                    self.logger.info(f"Loaded cache with {len(cache)} workbook(s)")
                    return cache
            except Exception as e:
                self.logger.error(f"Error loading cache: {e}")
                return {}
        else:
            self.logger.info("No cache file found, starting fresh")
            return {}
    
    def _save_cache(self) -> None:
        """Save cache to JSON file"""
        try:
            with open(self.cache_file, 'w', encoding='utf-8') as f:
                json.dump(self.cache, f, indent=2, ensure_ascii=False)
            self.logger.info(f"Saved cache with {len(self.cache)} workbook(s)")
        except Exception as e:
            self.logger.error(f"Error saving cache: {e}")
    
    def get_summary(self, workbook_name: str, df: pd.DataFrame, metadata_path: str, llm_client: Any) -> Dict[str, Any]:
        """
        Get or generate workbook data summary
        
        Args:
            workbook_name: Name of the workbook
            df: Already-loaded DataFrame from csv_data_loader
            metadata_path: Path to metadata JSON file
            llm_client: OpenAI client for generating summary
            
        Returns:
            Dictionary with data_description, numerical_columns, categorical_columns, calculated_columns
        """
        # Check cache first
        if workbook_name in self.cache:
            self.logger.info(f"Cache hit for workbook: {workbook_name}")
            return self.cache[workbook_name]
        
        self.logger.info(f"Cache miss for workbook: {workbook_name}, generating summary...")
        
        # Generate new summary
        summary = self._generate_summary(workbook_name, df, metadata_path, llm_client)
        
        # Save to cache
        self.cache[workbook_name] = summary
        self._save_cache()
        
        return summary
    
    def _generate_summary(self, workbook_name: str, df: pd.DataFrame, metadata_path: str, llm_client: Any) -> Dict[str, Any]:
        """Generate complete workbook summary"""
        self.logger.info(f"Generating summary for workbook: {workbook_name}")
        
        # 1. Smart categorization of columns
        numerical_col_list, categorical_col_list, string_as_numeric_dict = self._categorize_columns_smartly(df)
        
        # 2. Get numerical column stats
        numerical_columns = self._get_numerical_stats(df, numerical_col_list, string_as_numeric_dict)
        self.logger.info(f"Processed {len(numerical_columns)} numerical column(s)")
        
        # 3. Get categorical column stats
        categorical_columns = self._get_categorical_stats(df, categorical_col_list)
        self.logger.info(f"Processed {len(categorical_columns)} categorical column(s)")
        
        # 4. Extract calculated fields from metadata
        calculated_columns = self._extract_calculated_fields(metadata_path)
        self.logger.info(f"Extracted {len(calculated_columns)} calculated field(s)")
        
        # 5. Generate LLM summary
        data_description = self._generate_llm_summary(df, numerical_columns, categorical_columns, calculated_columns, llm_client)
        self.logger.info(f"Generated LLM summary: {data_description}")
        
        return {
            "data_description": data_description,
            "numerical_columns": numerical_columns,
            "categorical_columns": categorical_columns,
            "calculated_columns": calculated_columns,
            "generated_at": datetime.now().isoformat(),
            "total_rows": len(df),
            "total_columns": len(df.columns)
        }
    
    def _categorize_columns_smartly(self, df: pd.DataFrame) -> Tuple[List[str], List[str], Dict[str, pd.Series]]:
        """
        Intelligently categorize columns using adaptive thresholds and pattern matching.
        
        Returns:
            Tuple of (numerical_columns, categorical_columns, string_as_numeric_dict)
            - numerical_columns: True measures suitable for mean/std/percentiles
            - categorical_columns: Categories and identifiers
            - string_as_numeric_dict: {col_name: coerced_series} for string columns with numbers
        """
        total_rows = len(df)
        numerical_columns = []
        categorical_columns = []
        string_as_numeric = {}
        
        for col in df.columns:
            col_data = df[col]
            dtype = col_data.dtype
            non_null_count = col_data.count()
            
            # Skip columns with no data
            if non_null_count == 0:
                self.logger.warning(f"Column '{col}' has no data, skipping")
                continue
            
            unique_count = col_data.nunique()
            unique_ratio = unique_count / non_null_count if non_null_count > 0 else 0
            
            # Check 1: No variation (constant column)
            if unique_count < 2:
                self.logger.debug(f"Column '{col}' has < 2 unique values, treating as categorical")
                categorical_columns.append(col)
                continue
            
            # Check 2: Column name pattern matching (from smart_aggregation_service.py)
            col_lower = col.lower()
            is_identifier_pattern = any([
                '_id' in col_lower,
                'id_' in col_lower,
                col_lower.endswith('_number'),
                col_lower.endswith('number'),
                col_lower == 'id'
            ])
            
            is_preaggregated_pattern = any([
                '_count' in col_lower,
                'count_' in col_lower,
                col_lower.startswith('total_'),
                col_lower.startswith('num_'),
                col_lower.startswith('number_')
            ])
            
            # NUMERIC DTYPE columns
            if pd.api.types.is_numeric_dtype(dtype):
                # Check 3: Sparsity ratio (>50% unique = identifier)
                if unique_ratio > MAX_SPARSITY_RATIO:
                    self.logger.debug(f"Column '{col}' has high sparsity ({unique_ratio:.2%}), treating as identifier")
                    categorical_columns.append(col)
                    continue
                
                # Check 4: ID pattern in name
                if is_identifier_pattern:
                    self.logger.debug(f"Column '{col}' matches ID pattern, treating as identifier")
                    categorical_columns.append(col)
                    continue
                
                # Check 5: Adaptive practical cardinality
                max_practical_categories = total_rows // MIN_SAMPLES_PER_CATEGORY
                if unique_count > max_practical_categories:
                    self.logger.debug(f"Column '{col}' has too many categories ({unique_count} > {max_practical_categories})")
                    categorical_columns.append(col)
                    continue
                
                # Check 6: Low cardinality (likely categorical codes like status 0,1,2)
                if unique_count <= 10:  # Arbitrary but reasonable threshold for numeric codes
                    self.logger.debug(f"Column '{col}' has low cardinality ({unique_count}), treating as categorical")
                    categorical_columns.append(col)
                    continue
                
                # Check 7: Dominance check
                if non_null_count > 0:
                    top_freq = col_data.value_counts().iloc[0]
                    dominance_ratio = top_freq / non_null_count
                    if dominance_ratio > MAX_DOMINANCE_RATIO:
                        self.logger.debug(f"Column '{col}' has high dominance ({dominance_ratio:.2%}), treating as categorical")
                        categorical_columns.append(col)
                        continue
                
                # Passed all checks - it's a true numerical measure
                self.logger.debug(f"Column '{col}' classified as numerical measure")
                numerical_columns.append(col)
            
            # OBJECT DTYPE columns (strings)
            else:
                # Try to coerce to numeric (detect numbers stored as strings)
                coerced = pd.to_numeric(col_data, errors='coerce')
                coerced_count = coerced.count()
                conversion_rate = coerced_count / non_null_count if non_null_count > 0 else 0
                
                # If >80% of values can be converted to numbers, treat as numeric
                if conversion_rate >= STRING_NUMERIC_THRESHOLD:
                    self.logger.info(f"Column '{col}' is string but {conversion_rate:.1%} are numbers, converting to numerical")
                    string_as_numeric[col] = coerced
                    numerical_columns.append(col)
                else:
                    # True categorical/string column
                    self.logger.debug(f"Column '{col}' classified as categorical")
                    categorical_columns.append(col)
        
        self.logger.info(f"Smart categorization: {len(numerical_columns)} numerical, {len(categorical_columns)} categorical, {len(string_as_numeric)} string-as-numeric")
        return numerical_columns, categorical_columns, string_as_numeric
    
    def _get_numerical_stats(self, df: pd.DataFrame, numerical_cols: List[str], 
                            string_as_numeric: Dict[str, pd.Series]) -> Dict[str, Dict[str, Any]]:
        """
        Get statistics for numerical columns using df.describe()
        
        Args:
            df: Original DataFrame
            numerical_cols: List of column names identified as numerical
            string_as_numeric: Dict of {col_name: coerced_series} for string columns with numbers
        """
        if not numerical_cols:
            return {}
        
        # Helper function to convert values, replacing NaN with None
        def safe_float(value):
            """Convert to float, replacing NaN with None for JSON serialization"""
            if pd.isna(value):
                return None
            return float(value)
        
        # Build a temporary dataframe for describe()
        # Use original numeric columns, but replace string-as-numeric columns with coerced versions
        temp_data = {}
        for col in numerical_cols:
            if col in string_as_numeric:
                # Use the coerced numeric version
                temp_data[col] = string_as_numeric[col]
            else:
                # Use original column from dataframe
                temp_data[col] = df[col]
        
        temp_df = pd.DataFrame(temp_data)
        
        # Get describe() output
        # Note: describe() may drop columns with all NaN values or edge case types
        stats_df = temp_df.describe()
        
        # Convert to nested dictionary with metadata
        result = {}
        for col in numerical_cols:
            # Check if this column exists in stats_df (it may be dropped in edge cases)
            if col in stats_df.columns:
                col_info = {
                    "count": safe_float(stats_df.loc['count', col]) if 'count' in stats_df.index else 0,
                    "mean": safe_float(stats_df.loc['mean', col]) if 'mean' in stats_df.index else None,
                    "std": safe_float(stats_df.loc['std', col]) if 'std' in stats_df.index else None,
                    "min": safe_float(stats_df.loc['min', col]) if 'min' in stats_df.index else None,
                    "25%": safe_float(stats_df.loc['25%', col]) if '25%' in stats_df.index else None,
                    "50%": safe_float(stats_df.loc['50%', col]) if '50%' in stats_df.index else None,
                    "75%": safe_float(stats_df.loc['75%', col]) if '75%' in stats_df.index else None,
                    "max": safe_float(stats_df.loc['max', col]) if 'max' in stats_df.index else None
                }
            else:
                # Column was dropped by describe() - compute manually
                self.logger.warning(f"Numerical column '{col}' has no valid data for describe(), using manual stats")
                col_data = temp_data[col]
                col_info = {
                    "count": safe_float(col_data.count()),
                    "mean": safe_float(col_data.mean()) if col_data.count() > 0 else None,
                    "std": safe_float(col_data.std()) if col_data.count() > 0 else None,
                    "min": safe_float(col_data.min()) if col_data.count() > 0 else None,
                    "25%": safe_float(col_data.quantile(0.25)) if col_data.count() > 0 else None,
                    "50%": safe_float(col_data.quantile(0.50)) if col_data.count() > 0 else None,
                    "75%": safe_float(col_data.quantile(0.75)) if col_data.count() > 0 else None,
                    "max": safe_float(col_data.max()) if col_data.count() > 0 else None
                }
            
            # Add metadata about the column type
            if col in string_as_numeric:
                col_info["column_type"] = "string_as_numeric"
                col_info["original_dtype"] = str(df[col].dtype)
            else:
                col_info["column_type"] = "measure"
            
            result[col] = col_info
        
        return result
    
    def _get_categorical_stats(self, df: pd.DataFrame, categorical_cols: List[str]) -> Dict[str, Dict[str, Any]]:
        """
        Get statistics for categorical columns using df.describe()
        
        Args:
            df: Original DataFrame
            categorical_cols: List of column names identified as categorical (includes identifiers)
        """
        if not categorical_cols:
            return {}
        
        # Convert numeric columns to string so describe() treats them as categorical
        # (otherwise describe() gives mean/std instead of unique/top/freq)
        temp_df = df[categorical_cols].copy()
        for col in categorical_cols:
            if pd.api.types.is_numeric_dtype(temp_df[col]):
                temp_df[col] = temp_df[col].astype(str)
        
        # Get describe() output for categorical
        # Note: describe() may drop columns with all NaN values
        stats_df = temp_df.describe()
        
        # Convert to nested dictionary with metadata
        result = {}
        for col in categorical_cols:
            col_data = df[col]
            non_null_count = col_data.count()
            unique_count = col_data.nunique()
            unique_ratio = unique_count / non_null_count if non_null_count > 0 else 0
            
            # Check if this column exists in stats_df (it may be dropped if all NaN)
            if col in stats_df.columns:
                col_info = {
                    "count": int(stats_df.loc['count', col]) if 'count' in stats_df.index else 0,
                    "unique": int(stats_df.loc['unique', col]) if 'unique' in stats_df.index else 0,
                    "top": str(stats_df.loc['top', col]) if 'top' in stats_df.index else None,
                    "freq": int(stats_df.loc['freq', col]) if 'freq' in stats_df.index else 0
                }
            else:
                # Column was dropped by describe() (likely all NaN)
                self.logger.warning(f"Column '{col}' has no valid data for describe(), using manual stats")
                col_info = {
                    "count": int(non_null_count),
                    "unique": int(unique_count),
                    "top": str(col_data.mode()[0]) if len(col_data.mode()) > 0 else None,
                    "freq": int(col_data.value_counts().iloc[0]) if len(col_data.value_counts()) > 0 else 0
                }
            
            # Add metadata about whether it's an identifier or true categorical
            if unique_ratio > MAX_SPARSITY_RATIO:
                col_info["column_type"] = "identifier"
            elif pd.api.types.is_numeric_dtype(col_data.dtype):
                col_info["column_type"] = "categorical_numeric"  # Numeric dtype but treated as categorical
                col_info["original_dtype"] = str(col_data.dtype)
            else:
                col_info["column_type"] = "categorical"
            
            result[col] = col_info
        
        return result
    
    def _extract_calculated_fields(self, metadata_path: str) -> Dict[str, Dict[str, Any]]:
        """Extract calculated fields from metadata JSON"""
        if not metadata_path or not os.path.exists(metadata_path):
            self.logger.warning(f"Metadata file not found: {metadata_path}")
            return {}
        
        try:
            with open(metadata_path, 'r', encoding='utf-8') as f:
                metadata = json.load(f)
            
            # Extract all calculated fields from all charts
            calculated_fields = {}
            charts_metadata = metadata.get('charts_metadata_readable', {})
            
            for chart_name, chart_data in charts_metadata.items():
                calc_fields = chart_data.get('calculated_fields_ordered', [])
                
                for field in calc_fields:
                    field_name = field.get('name')
                    if field_name and field_name not in calculated_fields:
                        calculated_fields[field_name] = {
                            "formula": field.get('formula', ''),
                            "datatype": field.get('datatype', 'unknown')
                        }
            
            return calculated_fields
            
        except Exception as e:
            self.logger.error(f"Error extracting calculated fields: {e}")
            return {}
    
    def _generate_llm_summary(self, df: pd.DataFrame, numerical_cols: Dict, categorical_cols: Dict, 
                             calculated_cols: Dict, llm_client: Any) -> str:
        """Generate 1-2 line data description using LLM"""
        if not llm_client:
            self.logger.warning("No LLM client provided, using default summary")
            return f"Dataset with {len(df)} records across {len(df.columns)} columns."
        
        try:
            # Prepare dataset info for LLM
            total_rows = len(df)
            numerical_col_names = list(numerical_cols.keys())[:10]  # First 10
            categorical_col_names = list(categorical_cols.keys())[:10]
            calculated_col_names = list(calculated_cols.keys())
            
            # Get sample values from key categorical columns
            sample_values = {}
            for col in list(categorical_cols.keys())[:3]:
                if col in df.columns:
                    unique_vals = df[col].dropna().unique()[:5]
                    sample_values[col] = [str(v) for v in unique_vals]
            
            # Build prompt
            prompt = f"""Dataset Info:
- Total records: {total_rows:,}
- Numerical columns: {', '.join(numerical_col_names)}
- Categorical columns: {', '.join(categorical_col_names)}"""
            
            if sample_values:
                prompt += f"\n- Sample categorical values:"
                for col, vals in sample_values.items():
                    prompt += f"\n  • {col}: {', '.join(vals)}"
            
            if calculated_col_names:
                prompt += f"\n- Calculated metrics: {', '.join(calculated_col_names)}"
            
            prompt += "\n\nGenerate a concise 1-2 line description of what this data represents."
            
            # Call LLM
            response = llm_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "You are a data analyst. Provide concise, clear descriptions."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3,
                max_tokens=100
            )
            
            summary = response.choices[0].message.content.strip()
            self.logger.info(f"LLM generated summary: {summary}")
            return summary
            
        except Exception as e:
            self.logger.error(f"Error generating LLM summary: {e}")
            return f"Dataset with {len(df):,} records across {len(df.columns)} columns."
    
    def clear_cache(self, workbook_name: Optional[str] = None) -> None:
        """
        Clear cache for specific workbook or all workbooks
        
        Args:
            workbook_name: If provided, clear only this workbook. Otherwise clear all.
        """
        if workbook_name:
            if workbook_name in self.cache:
                del self.cache[workbook_name]
                self._save_cache()
                self.logger.info(f"Cleared cache for workbook: {workbook_name}")
        else:
            self.cache = {}
            self._save_cache()
            self.logger.info("Cleared entire cache")


