"""
Tableau Aggregation Hints Manager

Extracts and stores aggregation hints from Tableau column names.
Stores hints in JSON format: {chart_name: {csv_column: aggregation_hint}}

Example:
    {
        "Closed_Volume & Open_volume Brief": {
            "Closed_Volume": "COUNT_DISTINCT",
            "Open_Volume": "COUNT_DISTINCT"
        }
    }
"""

import json
import logging
from typing import Dict, Optional
from pathlib import Path


class TableauAggregationHintsManager:
    """Manages Tableau aggregation hints extracted from column names"""
    
    # Mapping of Tableau prefixes to aggregation methods
    TABLEAU_AGGREGATION_PREFIXES = {
        'distinct count of': 'COUNT_DISTINCT',
        'count of': 'COUNT',
        'sum of': 'SUM',
        'avg of': 'AVG',
        'average of': 'AVG',
        'min of': 'MIN',
        'max of': 'MAX',
        'median of': 'MEDIAN',
    }
    
    def __init__(self, hints_file_path: str = "tableau_aggregation_hints.json", logger=None):
        """
        Initialize the hints manager
        
        Args:
            hints_file_path: Path to JSON file for storing hints
            logger: Optional logger instance
        """
        self.hints_file_path = Path(hints_file_path)
        self.logger = logger or logging.getLogger(__name__)
        self.hints_cache: Dict[str, Dict[str, str]] = {}
        
        # Load existing hints from file
        self._load_hints_from_file()
    
    def _normalize_column_key(self, column_name: str) -> str:
        """
        Canonical normalization for column names used as keys.
        Ensures consistent matching regardless of case, spaces, or underscores.
        
        Args:
            column_name: Original column name
            
        Returns:
            Normalized column name for use as dictionary key
        """
        if not column_name:
            return ""
        
        # Lowercase and strip whitespace
        normalized = column_name.lower().strip()
        
        # Replace spaces with underscores for consistency
        normalized = normalized.replace(' ', '_')
        
        # Remove any duplicate underscores
        while '__' in normalized:
            normalized = normalized.replace('__', '_')
        
        # Strip leading/trailing underscores
        normalized = normalized.strip('_')
        
        return normalized
    
    def _load_hints_from_file(self):
        """Load hints from JSON file if it exists"""
        if self.hints_file_path.exists():
            try:
                with open(self.hints_file_path, 'r') as f:
                    self.hints_cache = json.load(f)
                self.logger.info(f"[TABLEAU_HINTS] Loaded {len(self.hints_cache)} chart hint(s) from {self.hints_file_path}")
            except Exception as e:
                self.logger.warning(f"[TABLEAU_HINTS] Failed to load hints file: {e}")
                self.hints_cache = {}
        else:
            self.logger.debug(f"[TABLEAU_HINTS] No existing hints file found at {self.hints_file_path}")
    
    def _save_hints_to_file(self):
        """Save hints to JSON file"""
        try:
            with open(self.hints_file_path, 'w') as f:
                json.dump(self.hints_cache, f, indent=2)
            self.logger.debug(f"[TABLEAU_HINTS] Saved hints to {self.hints_file_path}")
        except Exception as e:
            self.logger.error(f"[TABLEAU_HINTS] Failed to save hints: {e}")
    
    def extract_aggregation_hint(self, tableau_column_name: str) -> tuple[str, Optional[str]]:
        """
        Extract aggregation hint from Tableau column name.
        
        Args:
            tableau_column_name: e.g., "Distinct count of Closed_volume"
        
        Returns:
            tuple: (cleaned_column_name, aggregation_hint)
            e.g., ("Closed_volume", "COUNT_DISTINCT")
        """
        column_lower = tableau_column_name.lower().strip()
        
        for prefix, aggregation in self.TABLEAU_AGGREGATION_PREFIXES.items():
            if column_lower.startswith(prefix):
                # Remove the prefix to get the actual column name
                cleaned_name = tableau_column_name[len(prefix):].strip()
                self.logger.debug(f"[TABLEAU_HINTS] Extracted: '{tableau_column_name}' -> column='{cleaned_name}', hint='{aggregation}'")
                return cleaned_name, aggregation
        
        # No prefix found
        return tableau_column_name, None
    
    def store_hint_for_chart(self, chart_name: str, csv_column: str, aggregation_hint: str):
        """
        Store an aggregation hint for a specific chart and column.
        Uses normalized column names as keys for consistent lookup.
        
        Args:
            chart_name: Name of the Tableau chart/worksheet
            csv_column: The CSV column name (after mapping)
            aggregation_hint: The aggregation method (e.g., "COUNT_DISTINCT")
        """
        if chart_name not in self.hints_cache:
            self.hints_cache[chart_name] = {}
        
        # Normalize the column name for consistent storage
        normalized_col = self._normalize_column_key(csv_column)
        
        self.hints_cache[chart_name][normalized_col] = aggregation_hint
        self.logger.info(f"[TABLEAU_HINTS] Stored hint for chart '{chart_name}', column '{csv_column}' (normalized: '{normalized_col}'): {aggregation_hint}")
        
        # Save to file immediately
        self._save_hints_to_file()
    
    def get_hint(self, chart_name: str, csv_column: str) -> Optional[str]:
        """
        Get aggregation hint for a specific chart and column.
        Uses normalized column names for consistent lookup.
        
        Args:
            chart_name: Name of the Tableau chart/worksheet
            csv_column: The CSV column name
        
        Returns:
            Aggregation hint string (e.g., "COUNT_DISTINCT") or None
        """
        chart_hints = self.hints_cache.get(chart_name, {})
        
        # Normalize the column name for consistent lookup
        normalized_col = self._normalize_column_key(csv_column)
        hint = chart_hints.get(normalized_col)
        
        if hint:
            self.logger.debug(f"[TABLEAU_HINTS] Retrieved hint for '{chart_name}' / '{csv_column}' (normalized: '{normalized_col}'): {hint}")
        else:
            self.logger.debug(f"[TABLEAU_HINTS] No hint found for '{chart_name}' / '{csv_column}' (normalized: '{normalized_col}')")
        
        return hint
    
    def get_all_hints_for_chart(self, chart_name: str) -> Dict[str, str]:
        """
        Get all hints for a specific chart.
        
        Args:
            chart_name: Name of the Tableau chart/worksheet
        
        Returns:
            Dictionary of {csv_column: aggregation_hint}
        """
        return self.hints_cache.get(chart_name, {})
    
    def clear_hints_for_chart(self, chart_name: str):
        """Clear all hints for a specific chart"""
        if chart_name in self.hints_cache:
            del self.hints_cache[chart_name]
            self._save_hints_to_file()
            self.logger.info(f"[TABLEAU_HINTS] Cleared hints for chart '{chart_name}'")
    
    def clear_all_hints(self):
        """Clear all stored hints"""
        self.hints_cache = {}
        self._save_hints_to_file()
        self.logger.info("[TABLEAU_HINTS] Cleared all hints")


# Global instance (singleton pattern)
_global_hints_manager: Optional[TableauAggregationHintsManager] = None


def get_hints_manager(hints_file_path: str = "tableau_aggregation_hints.json", 
                      logger=None) -> TableauAggregationHintsManager:
    """
    Get or create the global hints manager instance.
    
    Args:
        hints_file_path: Path to JSON file for storing hints
        logger: Optional logger instance
    
    Returns:
        TableauAggregationHintsManager instance
    """
    global _global_hints_manager
    
    if _global_hints_manager is None:
        _global_hints_manager = TableauAggregationHintsManager(
            hints_file_path=hints_file_path,
            logger=logger
        )
    
    return _global_hints_manager
