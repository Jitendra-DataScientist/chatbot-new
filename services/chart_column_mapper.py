"""
Chart Column Mapper Service
Stores the complete transformation chain for chart columns:
Tableau API → Cleaned → CSV Matched

Populates chart_column_mappings.json when workbook data is fetched.
"""

import json
import os
from typing import Dict, List, Optional, Tuple, Any
from datetime import datetime
import pandas as pd
from master_logger import setup_module_logger

# Setup logger
logger = setup_module_logger('chart_column_mapper')


class ChartColumnMapper:
    """
    Maps chart columns through three transformation stages:
    1. Original Tableau columns (from API)
    2. Cleaned columns (Tableau prefixes removed)
    3. CSV matched columns (fuzzy matched to actual CSV)
    """
    
    CACHE_FILE = "chart_column_mappings.json"
    FUZZY_MATCH_THRESHOLD = 60  # Minimum score for accepting a match
    
    def __init__(self, csv_data: Optional[pd.DataFrame] = None, fuzzy_matcher=None):
        """
        Initialize the chart column mapper.
        
        Args:
            csv_data: Full CSV dataset for fuzzy matching (optional)
            fuzzy_matcher: FuzzyColumnMatcher instance (optional)
        """
        self.csv_data = csv_data
        self.fuzzy_matcher = fuzzy_matcher
        self.current_workbook_charts = {}  # Accumulate charts for current workbook
        self.current_workbook_name = None
        logger.info("ChartColumnMapper initialized")
        
        if csv_data is not None:
            logger.info(f"CSV data provided with {len(csv_data.columns)} columns")
        else:
            logger.info("No CSV data provided - CSV matching will return null")
    
    def process_and_store_chart(
        self, 
        chart_name: str, 
        chart_data: pd.DataFrame,
        workbook_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Process a chart's columns and store the complete mapping chain.
        
        Args:
            chart_name: Name of the chart/worksheet
            chart_data: DataFrame with original Tableau columns
            workbook_name: Name of the workbook (for logging)
        
        Returns:
            Dictionary with the complete mapping structure
        """
        logger.info(f"Processing chart: {chart_name}")
        logger.info(f"Original Tableau columns: {list(chart_data.columns)}")
        
        # Get original Tableau columns from the chart
        original_columns = list(chart_data.columns)
        
        # Step 1: Clean Tableau columns (remove prefixes)
        cleaned_columns = {}
        for col in original_columns:
            cleaned = self._clean_tableau_column_name(col)
            cleaned_columns[col] = cleaned
            logger.debug(f"Cleaned: '{col}' → '{cleaned}'")
        
        # Step 2: CSV fuzzy matching (if CSV data available)
        csv_matched_columns = {}
        mapping_confidence = {}
        
        for original_col, cleaned_col in cleaned_columns.items():
            matched_col, confidence = self._fuzzy_match_to_csv(cleaned_col, original_col)
            csv_matched_columns[original_col] = matched_col
            mapping_confidence[original_col] = confidence
            
            if matched_col:
                logger.info(f"CSV matched: '{original_col}' -> '{matched_col}' (confidence: {confidence})")
            else:
                logger.info(f"CSV match failed: '{original_col}' -> null (confidence: {confidence})")
        
        # Step 3: Detect X, Y, Hue axes from original Tableau data
        x_axis = self._detect_x_axis(chart_data)
        y_axis = self._detect_y_axis(chart_data, x_axis)
        hue_axes = self._detect_hue_axes(chart_data, x_axis, y_axis)
        
        logger.info(f"Detected axes - X: {x_axis}, Y: {y_axis}, Hue: {hue_axes}")
        
        # Build the complete mapping entry
        mapping_entry = {
            "original_columns": original_columns,
            "cleaned_columns": cleaned_columns,
            "csv_matched_columns": csv_matched_columns,
            "x_axis_detected": x_axis,
            "x_axis_cleaned": cleaned_columns.get(x_axis) if x_axis else None,
            "x_axis_csv_matched": csv_matched_columns.get(x_axis) if x_axis else None,
            "y_axis_detected": y_axis,
            "y_axis_cleaned": cleaned_columns.get(y_axis) if y_axis else None,
            "y_axis_csv_matched": csv_matched_columns.get(y_axis) if y_axis else None,
            "hue_detected": hue_axes,
            "hue_cleaned": [cleaned_columns.get(h) for h in hue_axes] if hue_axes else [],
            "hue_csv_matched": [csv_matched_columns.get(h) for h in hue_axes] if hue_axes else [],
            "mapping_confidence": {
                "x_axis": mapping_confidence.get(x_axis, 0.0) if x_axis else 0.0,
                "y_axis": mapping_confidence.get(y_axis, 0.0) if y_axis else 0.0,
                "csv_matching_success_rate": self._calculate_success_rate(csv_matched_columns)
            },
            "chart_timestamp": datetime.utcnow().isoformat() + 'Z'
        }
        
        # Accumulate chart for current workbook (don't save yet)
        if workbook_name:
            self.current_workbook_name = workbook_name
            self.current_workbook_charts[chart_name] = mapping_entry
            logger.info(f"Accumulated mapping for chart: {chart_name} (workbook: {workbook_name})")
        else:
            # Fallback: save immediately if no workbook name
            logger.warning(f"No workbook name provided, saving chart individually: {chart_name}")
            self._save_chart_individually(chart_name, mapping_entry)
        
        logger.info(f"Successfully processed mapping for chart: {chart_name}")
        return mapping_entry
    
    def _clean_tableau_column_name(self, tableau_col: str) -> Optional[str]:
        """
        Clean Tableau column name by removing prefixes and converting to lowercase.
        Uses the same logic as csv_data_loader.py
        
        Returns None if cleaning fails.
        """
        try:
            if not tableau_col or not isinstance(tableau_col, str):
                logger.warning(f"Invalid column name: {tableau_col}")
                return None
            
            cleaned = tableau_col.lower().strip()
            
            # Remove common Tableau prefixes
            prefixes_to_remove = [
                'distinct count of ',
                'count of ',
                'sum of ',
                'average of ',
                'avg of ',
                'month of ',
                'year of ',
                'day of ',
                'week of ',
                'quarter of ',
                'measure ',
                'number of '
            ]
            
            for prefix in prefixes_to_remove:
                if cleaned.startswith(prefix):
                    cleaned = cleaned[len(prefix):]
                    break
            
            # Replace spaces with underscores for better matching
            cleaned = cleaned.replace(' ', '_')
            
            # Remove special characters except underscore
            cleaned = ''.join(c for c in cleaned if c.isalnum() or c == '_')
            
            result = cleaned.strip('_')  # Remove leading/trailing underscores
            
            # Return None if result is empty
            return result if result else None
            
        except Exception as e:
            logger.error(f"Error cleaning column name '{tableau_col}': {e}")
            return None
    
    def _fuzzy_match_to_csv(
        self, 
        cleaned_col: Optional[str], 
        original_col: str
    ) -> Tuple[Optional[str], float]:
        """
        Fuzzy match cleaned column to actual CSV columns.
        
        Returns:
            Tuple of (matched_column, confidence_score)
            Returns (None, 0.0) if matching fails
        """
        # If no cleaned column or no CSV data, return null
        if not cleaned_col or self.csv_data is None:
            return (None, 0.0)
        
        # If fuzzy matcher is available, use it
        if self.fuzzy_matcher:
            try:
                csv_columns = list(self.csv_data.columns)
                matched_col, score = self.fuzzy_matcher.get_match_with_score(
                    cleaned_col,
                    csv_columns,
                    context=f"chart_mapping|{original_col}"
                )
                
                # Only accept match if score is above threshold
                if matched_col and score >= self.FUZZY_MATCH_THRESHOLD:
                    return (matched_col, float(score))
                else:
                    logger.debug(f"Match rejected for '{cleaned_col}': score {score} < threshold {self.FUZZY_MATCH_THRESHOLD}")
                    return (None, float(score) if score else 0.0)
                    
            except Exception as e:
                logger.error(f"Error in fuzzy matching for '{cleaned_col}': {e}")
                return (None, 0.0)
        
        # Fallback: Direct exact match
        if cleaned_col in self.csv_data.columns:
            return (cleaned_col, 100.0)
        
        # No match found
        return (None, 0.0)
    
    def _detect_x_axis(self, chart_data: pd.DataFrame) -> Optional[str]:
        """
        Detect X-axis column (usually time/date column).
        Returns None if detection fails or is ambiguous.
        """
        time_keywords = ['month', 'date', 'time', 'year', 'day', 'week', 'period', 'quarter']
        
        candidates = []
        for col in chart_data.columns:
            col_lower = str(col).lower()
            if any(keyword in col_lower for keyword in time_keywords):
                candidates.append(col)
        
        # Return None if no candidates or multiple ambiguous candidates
        if len(candidates) == 0:
            logger.debug("No X-axis (time) column detected")
            return None
        elif len(candidates) == 1:
            logger.debug(f"X-axis detected: {candidates[0]}")
            return candidates[0]
        else:
            # Multiple candidates - try to pick the most likely one
            # Prefer columns with "month" or "date" first
            for col in candidates:
                col_lower = str(col).lower()
                if 'month' in col_lower or 'date' in col_lower:
                    logger.debug(f"X-axis detected (multiple candidates, picked by priority): {col}")
                    return col
            
            # If still ambiguous, return first candidate with warning
            logger.warning(f"Ambiguous X-axis detection, multiple candidates: {candidates}. Using first one.")
            return candidates[0]
    
    def _detect_y_axis(self, chart_data: pd.DataFrame, x_axis: Optional[str]) -> Optional[str]:
        """
        Detect Y-axis column (usually numeric/count column).
        Returns None if detection fails or is ambiguous.
        """
        numeric_keywords = ['count', 'sum', 'total', 'amount', 'volume', 'revenue', 'avg', 'average']
        
        candidates = []
        for col in chart_data.columns:
            # Skip if this is the x-axis
            if col == x_axis:
                continue
            
            col_lower = str(col).lower()
            
            # Check if numeric or has numeric keywords
            is_numeric = pd.api.types.is_numeric_dtype(chart_data[col])
            has_numeric_keyword = any(keyword in col_lower for keyword in numeric_keywords)
            
            if is_numeric or has_numeric_keyword:
                candidates.append(col)
        
        # Return None if no candidates
        if len(candidates) == 0:
            logger.debug("No Y-axis (numeric) column detected")
            return None
        elif len(candidates) == 1:
            logger.debug(f"Y-axis detected: {candidates[0]}")
            return candidates[0]
        else:
            # Multiple candidates - pick first non-time column with numeric data
            for col in candidates:
                if pd.api.types.is_numeric_dtype(chart_data[col]):
                    logger.debug(f"Y-axis detected (multiple candidates, picked numeric): {col}")
                    return col
            
            # If still ambiguous, return first candidate with warning
            logger.warning(f"Ambiguous Y-axis detection, multiple candidates: {candidates}. Using first one.")
            return candidates[0]
    
    def _detect_hue_axes(
        self, 
        chart_data: pd.DataFrame, 
        x_axis: Optional[str], 
        y_axis: Optional[str]
    ) -> List[str]:
        """
        Detect hue/legend columns (categorical columns not used for x or y).
        Returns empty list if no hue columns found.
        """
        hue_candidates = []
        
        for col in chart_data.columns:
            # Skip x and y axes
            if col == x_axis or col == y_axis:
                continue
            
            col_lower = str(col).lower()
            
            # Check for "Measure Names" (Tableau special column)
            if col_lower == 'measure names':
                hue_candidates.append(col)
                logger.debug(f"Hue detected: {col} (Tableau Measure Names)")
                continue
            
            # Check if categorical (object dtype or low cardinality)
            if chart_data[col].dtype == 'object':
                hue_candidates.append(col)
                logger.debug(f"Hue detected: {col} (categorical/object type)")
            elif pd.api.types.is_numeric_dtype(chart_data[col]):
                # Check if low cardinality numeric (could be categorical)
                unique_ratio = chart_data[col].nunique() / len(chart_data)
                if unique_ratio < 0.1 and chart_data[col].nunique() <= 20:
                    hue_candidates.append(col)
                    logger.debug(f"Hue detected: {col} (low cardinality numeric)")
        
        logger.debug(f"Total hue columns detected: {len(hue_candidates)}")
        return hue_candidates
    
    def _calculate_success_rate(self, csv_matched_columns: Dict[str, Optional[str]]) -> float:
        """Calculate percentage of columns successfully matched to CSV."""
        if not csv_matched_columns:
            return 0.0
        
        successful = sum(1 for v in csv_matched_columns.values() if v is not None)
        total = len(csv_matched_columns)
        
        return round((successful / total) * 100, 2) if total > 0 else 0.0
    
    def save_workbook_to_cache(self, workbook_name: Optional[str] = None):
        """
        Save all accumulated charts for the current workbook to cache.
        Replaces entire workbook entry (not merge).
        
        Args:
            workbook_name: Name of workbook to save (uses current if not provided)
        """
        if workbook_name:
            self.current_workbook_name = workbook_name
        
        if not self.current_workbook_name:
            logger.warning("No workbook name set, cannot save to cache")
            return
        
        if not self.current_workbook_charts:
            logger.warning(f"No charts accumulated for workbook '{self.current_workbook_name}'")
            return
        
        try:
            # Load existing cache
            cache_data = {}
            if os.path.exists(self.CACHE_FILE):
                try:
                    with open(self.CACHE_FILE, 'r', encoding='utf-8') as f:
                        cache_data = json.load(f)
                    logger.debug(f"Loaded existing cache with {len(cache_data)} workbooks")
                except Exception as e:
                    logger.error(f"Error reading cache file: {e}")
                    cache_data = {}
            
            # Create workbook entry (replaces existing completely)
            workbook_entry = {
                "last_updated": datetime.utcnow().isoformat() + 'Z',
                "chart_count": len(self.current_workbook_charts),
                "charts": self.current_workbook_charts
            }
            
            # Replace entire workbook entry
            cache_data[self.current_workbook_name] = workbook_entry
            
            # Write back to file
            with open(self.CACHE_FILE, 'w', encoding='utf-8') as f:
                json.dump(cache_data, f, indent=2, ensure_ascii=False)
            
            logger.info(f"Saved {len(self.current_workbook_charts)} charts for workbook '{self.current_workbook_name}' to {self.CACHE_FILE}")
            logger.info(f"Workbook entry completely replaced (handles adds/removes/changes)")
            
            # Clear accumulated data
            self.current_workbook_charts = {}
            self.current_workbook_name = None
            
        except Exception as e:
            logger.error(f"Error saving workbook to cache: {e}")
            import traceback
            logger.error(traceback.format_exc())
    
    def _save_chart_individually(self, chart_name: str, mapping_entry: Dict[str, Any]):
        """Fallback: Save individual chart when no workbook name provided."""
        try:
            # Load existing cache
            cache_data = {}
            if os.path.exists(self.CACHE_FILE):
                try:
                    with open(self.CACHE_FILE, 'r', encoding='utf-8') as f:
                        cache_data = json.load(f)
                except Exception as e:
                    logger.error(f"Error reading cache file: {e}")
                    cache_data = {}
            
            # Create or update "Unknown Workbook" entry
            workbook_name = "Unknown Workbook"
            if workbook_name not in cache_data:
                cache_data[workbook_name] = {
                    "last_updated": datetime.utcnow().isoformat() + 'Z',
                    "chart_count": 0,
                    "charts": {}
                }
            
            # Add chart to unknown workbook
            cache_data[workbook_name]["charts"][chart_name] = mapping_entry
            cache_data[workbook_name]["chart_count"] = len(cache_data[workbook_name]["charts"])
            cache_data[workbook_name]["last_updated"] = datetime.utcnow().isoformat() + 'Z'
            
            # Write back to file
            with open(self.CACHE_FILE, 'w', encoding='utf-8') as f:
                json.dump(cache_data, f, indent=2, ensure_ascii=False)
            
            logger.info(f"Saved chart '{chart_name}' to '{workbook_name}' in {self.CACHE_FILE}")
            
        except Exception as e:
            logger.error(f"Error saving chart individually: {e}")
            import traceback
            logger.error(traceback.format_exc())
    
    @classmethod
    def load_mappings(cls, workbook_name: Optional[str] = None, chart_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Load mappings from cache file.
        
        Args:
            workbook_name: Specific workbook to load, or None to load all workbooks
            chart_name: Specific chart within workbook to load (requires workbook_name)
        
        Returns:
            - If both workbook_name and chart_name: Return specific chart mapping
            - If only workbook_name: Return all charts for that workbook
            - If neither: Return all workbooks
            - None if not found
        """
        try:
            if not os.path.exists(cls.CACHE_FILE):
                logger.warning(f"Cache file not found: {cls.CACHE_FILE}")
                return None
            
            with open(cls.CACHE_FILE, 'r', encoding='utf-8') as f:
                cache_data = json.load(f)
            
            if workbook_name and chart_name:
                # Return specific chart from specific workbook
                workbook_data = cache_data.get(workbook_name)
                if workbook_data and 'charts' in workbook_data:
                    return workbook_data['charts'].get(chart_name)
                return None
            elif workbook_name:
                # Return all charts for specific workbook
                return cache_data.get(workbook_name)
            else:
                # Return all workbooks
                return cache_data
                
        except Exception as e:
            logger.error(f"Error loading cache: {e}")
            return None

