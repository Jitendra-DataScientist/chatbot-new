"""
CSV Data Loader for Analytics Assistant

This module handles loading and analyzing CSV files for statistical analysis.
Designed to replace Tableau workbook data with CSV data.
"""

import pandas as pd
import numpy as np
from typing import Dict, Any, Optional, List, Tuple
import logging
from pathlib import Path
import os
from datetime import datetime
from services.fuzzy_column_matcher import FuzzyColumnMatcher
from services.tableau_aggregation_hints import get_hints_manager
from master_logger import setup_module_logger

class CSVDataLoader:
    """Loads and analyzes CSV data for the analytics assistant"""
    
    def __init__(self, csv_file_path: str = None, workbook_name: str = None):
        """
        Initialize the CSV data loader
        
        Args:
            csv_file_path: Path to the CSV file. If None, looks for CSV in tableau_exports or project root.
            workbook_name: Name of the workbook to load exported CSV data from tableau_exports.
        """
        self.logger = setup_module_logger('services.csv_data_loader')
        self.csv_file_path = csv_file_path
        self.workbook_name = workbook_name
        self.data = None
        self.summary_stats = None
        self.fuzzy_matcher = FuzzyColumnMatcher(threshold=70)  # Lowered to 70% for better misspelling tolerance
        self.hints_manager = get_hints_manager(logger=self.logger)
        
        # Auto-detect CSV file if not provided
        if not self.csv_file_path:
            self.csv_file_path = self._find_csv_file()
    
    def _find_csv_file(self) -> Optional[str]:
        """Find CSV file in tableau_exports directory or project root"""
        project_root = Path(__file__).parent.parent
        
        # First priority: Look in tableau_exports/{workbook_name}/ if workbook_name is provided
        if self.workbook_name:
            tableau_exports_path = project_root / "tableau_exports" / self.workbook_name
            csv_file = self._find_csv_in_tableau_exports(tableau_exports_path)
            if csv_file:
                return csv_file
        
        # Second priority: Look in any tableau_exports subdirectory
        tableau_exports_base = project_root / "tableau_exports"
        if tableau_exports_base.exists():
            for workbook_dir in tableau_exports_base.iterdir():
                if workbook_dir.is_dir():
                    csv_file = self._find_csv_in_tableau_exports(workbook_dir)
                    if csv_file:
                        self.logger.info(f"Using CSV from workbook directory: {workbook_dir.name}")
                        return csv_file
        
        # Fallback: Look in project root (original behavior)
        csv_files = list(project_root.glob("*.csv"))
        if csv_files:
            csv_file = csv_files[0]  # Take the first CSV file found
            self.logger.info(f"Fallback: Auto-detected CSV file in root: {csv_file}")
            return str(csv_file)
        
        self.logger.warning("No CSV file found in tableau_exports or project root")
        return None
    
    def _find_csv_in_tableau_exports(self, workbook_path: Path) -> Optional[str]:
        """Find CSV files in tableau exports directory structure"""
        if not workbook_path.exists():
            return None
        
        # Look for CSV files in worksheets and datasources subdirectories
        for subdirectory in ["worksheets", "datasources"]:
            subdir_path = workbook_path / subdirectory
            if subdir_path.exists():
                csv_files = list(subdir_path.glob("*.csv"))
                if csv_files:
                    # Prefer worksheets over datasources, and take the first file found
                    csv_file = csv_files[0]
                    self.logger.info(f"Found CSV in tableau_exports: {csv_file}")
                    return str(csv_file)
        
        # Also check root of workbook directory
        csv_files = list(workbook_path.glob("*.csv"))
        if csv_files:
            csv_file = csv_files[0]
            self.logger.info(f"Found CSV in workbook root: {csv_file}")
            return str(csv_file)
        
        return None
    
    def set_workbook_name(self, workbook_name: str) -> None:
        """Set the workbook name and reload CSV data from tableau_exports"""
        self.workbook_name = workbook_name
        self.logger.info(f"Setting workbook name to: {workbook_name}")
        
        # Re-find CSV file with new workbook name
        self.csv_file_path = self._find_csv_file()
        
        # Clear existing data so it gets reloaded
        self.data = None
        self.summary_stats = None
    
    def load_data_from_workbook_exports(self, workbook_name: str) -> bool:
        """
        Load CSV data from tableau_exports directory for a specific workbook
        
        Args:
            workbook_name: Name of the workbook to load exported data from
            
        Returns:
            bool: True if successful, False otherwise
        """
        self.logger.info(f"Loading data from workbook exports: {workbook_name}")
        
        # Set workbook name and find CSV file
        self.set_workbook_name(workbook_name)
        
        # Load the data
        return self.load_data()
    
    def load_data(self) -> bool:
        """
        Load the CSV data into memory
        
        Returns:
            bool: True if successful, False otherwise
        """
        if not self.csv_file_path or not os.path.exists(self.csv_file_path):
            self.logger.error(f"CSV file not found: {self.csv_file_path}")
            return False
        
        try:
            # Load CSV data
            self.data = pd.read_csv(self.csv_file_path)
            self.logger.info(f"Successfully loaded CSV with {len(self.data)} rows and {len(self.data.columns)} columns")
            
            # Generate summary statistics
            self._generate_summary_stats()
            
            return True
            
        except Exception as e:
            self.logger.error(f"Error loading CSV file: {e}")
            return False
    
    def _generate_summary_stats(self) -> None:
        """Generate summary statistics for the loaded data"""
        if self.data is None:
            return
        
        try:
            # Basic stats
            total_rows = len(self.data)
            total_columns = len(self.data.columns)
            
            # Identify key columns
            numeric_columns = self.data.select_dtypes(include=[np.number]).columns.tolist()
            categorical_columns = self.data.select_dtypes(include=['object']).columns.tolist()
            datetime_columns = []
            
            # Try to identify datetime columns
            for col in categorical_columns:
                if any(word in col.lower() for word in ['date', 'time', 'created', 'modified', 'closed']):
                    # Try to parse as datetime
                    try:
                        pd.to_datetime(self.data[col].dropna().iloc[:100])
                        datetime_columns.append(col)
                    except:
                        continue
            
            # Find key business metrics
            key_metrics = []
            for col in self.data.columns:
                col_lower = col.lower()
                if any(word in col_lower for word in ['count', 'amount', 'total', 'revenue', 'sales', 'quantity', 'hours', 'time']):
                    key_metrics.append(col)
            
            # Detect data patterns
            data_patterns = self._detect_data_patterns()
            
            self.summary_stats = {
                'total_rows': total_rows,
                'total_columns': total_columns,
                'numeric_columns': numeric_columns,
                'categorical_columns': categorical_columns,
                'datetime_columns': datetime_columns,
                'key_metrics': key_metrics[:5],  # Top 5 key metrics
                'data_patterns': data_patterns,
                'file_name': os.path.basename(self.csv_file_path),
                'load_time': datetime.now().isoformat()
            }
            
        except Exception as e:
            self.logger.error(f"Error generating summary stats: {e}")
            self.summary_stats = {
                'total_rows': len(self.data) if self.data is not None else 0,
                'total_columns': len(self.data.columns) if self.data is not None else 0,
                'error': str(e)
            }
    
    def _detect_data_patterns(self) -> Dict[str, Any]:
        """Detect interesting patterns in the data"""
        if self.data is None:
            return {}
        
        patterns = {}
        
        try:
            # Check for ticket/case management patterns
            if any(str(col).lower() in ['case_id', 'ticket_id', 'casenumber'] for col in self.data.columns):
                patterns['data_type'] = 'Support Tickets'
                
                # Find status column
                status_cols = [col for col in self.data.columns if 'status' in str(col).lower()]
                if status_cols:
                    status_distribution = self.data[status_cols[0]].value_counts()
                    patterns['status_distribution'] = status_distribution.to_dict()
            
            # Check for time series data
            date_cols = [col for col in self.data.columns if any(word in str(col).lower() for word in ['date', 'time'])]
            if date_cols:
                patterns['has_time_series'] = True
                patterns['date_columns'] = date_cols[:3]  # Top 3 date columns
            
            # Check for geographic data
            geo_cols = [col for col in self.data.columns if any(word in str(col).lower() for word in ['country', 'region', 'city', 'location'])]
            if geo_cols:
                patterns['has_geographic'] = True
                patterns['geographic_columns'] = geo_cols
            
            # Check for customer/account data
            customer_cols = [col for col in self.data.columns if any(word in str(col).lower() for word in ['account', 'customer', 'client'])]
            if customer_cols:
                patterns['has_customer_data'] = True
                patterns['customer_columns'] = customer_cols
            
        except Exception as e:
            self.logger.warning(f"Error detecting data patterns: {e}")
        
        return patterns
    
    def generate_summary_lines(self) -> Tuple[str, str]:
        """
        Generate 2-line summary for the analytics assistant
        
        Returns:
            Tuple[str, str]: Two summary lines
        """
        if not self.summary_stats:
            return (
                "📊 Workbook data loaded successfully",
                "🔍 Ready for statistical analysis"
            )
        
        try:
            stats = self.summary_stats
            
            # Line 1: Data overview - COMMENTED OUT FOR SIMPLIFIED DISPLAY
            # data_type = stats.get('data_patterns', {}).get('data_type', 'Business Data')
            # if data_type == 'Support Tickets':
            #     line1 = f"📊 Loaded {stats['total_rows']:,} tickets across {stats['total_columns']} metrics"
            # else:
            #     line1 = f"📊 Loaded {stats['total_rows']:,} records across {stats['total_columns']} fields"
            line1 = ""  # Simplified - no data overview message
            
            # Line 2: Key capabilities - COMMENTED OUT FOR SIMPLIFIED DISPLAY
            # patterns = stats.get('data_patterns', {})
            # capabilities = []
            # 
            # if patterns.get('has_time_series'):
            #     capabilities.append("Time series analysis")
            # if patterns.get('has_geographic'):
            #     capabilities.append("Geographic insights")
            # if patterns.get('status_distribution'):
            #     capabilities.append("Status tracking")
            # if stats.get('key_metrics'):
            #     capabilities.append("Performance metrics")
            # 
            # if capabilities:
            #     line2 = f"🔍 Available analysis: {', '.join(capabilities[:3])}"
            # else:
            #     line2 = "🔍 Available analysis: Statistical analysis, Data exploration"
            line2 = ""  # Simplified - no capabilities message
            
            return (line1, line2)
            
        except Exception as e:
            self.logger.error(f"Error generating summary lines: {e}")
            return (
                f"📊 Loaded {self.summary_stats.get('total_rows', 0):,} records from workbook",
                "🔍 Available analysis: Data exploration, Statistical analysis"
            )
    
    def get_data(self) -> Optional[pd.DataFrame]:
        """Get the loaded DataFrame"""
        return self.data
    
    def get_summary_stats(self) -> Optional[Dict[str, Any]]:
        """Get the summary statistics"""
        return self.summary_stats
    
    def get_column_info(self) -> Dict[str, Any]:
        """Get detailed column information"""
        if self.data is None:
            return {}
        
        column_info = {}
        for col in self.data.columns:
            col_data = self.data[col]
            column_info[col] = {
                'dtype': str(col_data.dtype),
                'null_count': col_data.isnull().sum(),
                'unique_count': col_data.nunique(),
                'sample_values': col_data.dropna().head(3).tolist()
            }
        
        return column_info
    
    def map_tableau_to_csv_columns(self, tableau_columns: list, chart_name: str = None) -> list:
        """Map Tableau column names to CSV column names with prefix stripping and lowercase matching"""
        if self.data is None:
            return []
        
        csv_columns = self.data.columns.tolist()
        csv_columns_lower = [col.lower().strip() for col in csv_columns]
        mapped_columns = []
        
        self.logger.info(f"Mapping Tableau columns {tableau_columns} to CSV columns")
        
        for tableau_col in tableau_columns:
            # Extract aggregation hint from Tableau column name
            cleaned_tableau_col_with_hint, agg_hint = self.hints_manager.extract_aggregation_hint(tableau_col)
            
            # Strip common Tableau prefixes and convert to lowercase (for matching)
            cleaned_tableau_col = self._clean_tableau_column_name(tableau_col)
            
            self.logger.info(f"Cleaned Tableau column: '{tableau_col}' -> '{cleaned_tableau_col}'")
            if agg_hint:
                self.logger.info(f"  └─ Extracted aggregation hint: {agg_hint}")
            
            # Try exact match first with cleaned name (case-insensitive, underscore-insensitive)
            exact_match_found = False
            matched_csv_column = None
            
            # Normalize cleaned tableau column for comparison (remove underscores for matching)
            cleaned_tableau_for_match = cleaned_tableau_col.replace('_', ' ')
            
            for i, csv_col_lower in enumerate(csv_columns_lower):
                if cleaned_tableau_for_match == csv_col_lower:
                    matched_csv_column = csv_columns[i]
                    mapped_columns.append(matched_csv_column)
                    self.logger.info(f"Exact match: '{tableau_col}' -> '{csv_columns[i]}'")
                    exact_match_found = True
                    break
            
            # Store hint using the cleaned name from hint extraction OR matched CSV column
            # This ensures hints are stored even if CSV matching fails
            if chart_name and agg_hint:
                column_to_store = matched_csv_column if matched_csv_column else cleaned_tableau_col_with_hint
                self.hints_manager.store_hint_for_chart(chart_name, column_to_store, agg_hint)
                self.logger.info(f"  └─ Stored aggregation hint: '{column_to_store}' -> {agg_hint}")
            
            if exact_match_found:
                continue
            
            # Try fuzzy matching with cleaned names
            best_match = None
            best_score = 0
            
            # Special semantic mapping rules
            if self._is_closed_volume_field(cleaned_tableau_col):
                # Map closed volume to status or closed date fields
                for col in ['status', 'closeddate']:
                    if col.lower() in csv_columns_lower:
                        idx = csv_columns_lower.index(col.lower())
                        matched_csv_column = csv_columns[idx]
                        mapped_columns.append(matched_csv_column)
                        self.logger.info(f"Mapped closed volume '{tableau_col}' -> '{csv_columns[idx]}'")
                        exact_match_found = True
                        break
                if exact_match_found:
                    continue
            
            if self._is_open_volume_field(cleaned_tableau_col):
                # Map open volume to status field (for filtering open tickets)
                for col in ['status', 'closeddate']:
                    if col.lower() in csv_columns_lower:
                        idx = csv_columns_lower.index(col.lower())
                        matched_csv_column = csv_columns[idx]
                        mapped_columns.append(matched_csv_column)
                        self.logger.info(f"Mapped open volume '{tableau_col}' -> '{csv_columns[idx]}'")
                        exact_match_found = True
                        break
                if exact_match_found:
                    continue
            
            # if self._is_ticket_count_field(cleaned_tableau_col):
            #     # Map ticket counts to case_id or casenumber
            #     for col in ['case_id', 'casenumber']:
            #         if col.lower() in csv_columns_lower:
            #             idx = csv_columns_lower.index(col.lower())
            #             matched_csv_column = csv_columns[idx]
            #             mapped_columns.append(matched_csv_column)
            #             self.logger.info(f"Mapped ticket count '{tableau_col}' -> '{csv_columns[idx]}'")
            #             exact_match_found = True
            #             break
            #     if exact_match_found:
            #         if chart_name and agg_hint and matched_csv_column:
            #             self.hints_manager.store_hint_for_chart(chart_name, matched_csv_column, agg_hint)
            #         continue
            
            if self._is_eng_transfer_field(cleaned_tableau_col):
                # Map eng transfers to eng_transfers column
                for col in ['eng_transfers']:
                    if col.lower() in csv_columns_lower:
                        idx = csv_columns_lower.index(col.lower())
                        matched_csv_column = csv_columns[idx]
                        mapped_columns.append(matched_csv_column)
                        self.logger.info(f"Mapped eng transfers '{tableau_col}' -> '{csv_columns[idx]}'")
                        exact_match_found = True
                        break
                if exact_match_found:
                    continue
            
            # Use centralized fuzzy matching
            fuzzy_match = self.fuzzy_matcher.find_best_match(
                cleaned_tableau_col,
                csv_columns,
                context=f"tableau_to_csv_mapping"
            )
            
            if fuzzy_match:
                matched_csv_column = fuzzy_match
                mapped_columns.append(matched_csv_column)
                self.logger.info(f"Fuzzy matched Tableau column '{tableau_col}' -> CSV column '{fuzzy_match}'")
            else:
                self.logger.warning(f"No mapping found for Tableau column: '{tableau_col}'")
        
        self.logger.info(f"Final mapped columns: {mapped_columns}")
        
        # Log summary of stored hints
        if chart_name:
            stored_hints = self.hints_manager.get_all_hints_for_chart(chart_name)
            if stored_hints:
                self.logger.info(f"[TABLEAU_HINTS] Stored {len(stored_hints)} aggregation hint(s) for chart '{chart_name}':")
                for col, hint in stored_hints.items():
                    self.logger.info(f"  • {col}: {hint}")
        
        return mapped_columns
    
    def _clean_tableau_column_name(self, tableau_col: str) -> str:
        """Clean Tableau column name by removing prefixes and converting to lowercase"""
        cleaned = tableau_col.lower()
        
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
            'measure ',
            'number of '
        ]
        
        for prefix in prefixes_to_remove:
            if cleaned.startswith(prefix):
                cleaned = cleaned[len(prefix):]
                break
        
        # Replace spaces with underscores for better matching
        cleaned = cleaned.replace(' ', '_')
        
        return cleaned.strip()
    
    def _is_closed_volume_field(self, cleaned_col: str) -> bool:
        """Check if field represents closed volume/tickets"""
        return 'closed' in cleaned_col and ('volume' in cleaned_col or 'count' in cleaned_col)
    
    def _is_open_volume_field(self, cleaned_col: str) -> bool:
        """Check if field represents open volume/tickets"""
        return 'open' in cleaned_col and ('volume' in cleaned_col or 'count' in cleaned_col)
    
    # def _is_ticket_count_field(self, cleaned_col: str) -> bool:
    #     """Check if field represents ticket counts"""
    #     return ('ticket' in cleaned_col or 'case' in cleaned_col) and ('count' in cleaned_col or 'number' in cleaned_col)
    
    def _is_eng_transfer_field(self, cleaned_col: str) -> bool:
        """Check if field represents engineering transfers"""
        return 'eng' in cleaned_col and ('trans' in cleaned_col or 'transfer' in cleaned_col)
    
    def get_workbook_compatible_data(self) -> Dict[str, Any]:
        """
        Format data to be compatible with existing workbook processing
        
        Returns:
            Dict compatible with existing tableau workbook structure
        """
        if not self.data is not None or not self.summary_stats:
            return {
                'success': False,
                'error': 'No data loaded'
            }
        
        # Create a workbook-like structure with data as a single "worksheet"
        patterns = self.summary_stats.get('data_patterns', {})
        data_type = patterns.get('data_type', 'Business Data')
        worksheet_name = f"{data_type}_Analysis"
        
        # Generate summary lines
        line1, line2 = self.generate_summary_lines()
        
        return {
            'success': True,
            'worksheets_data': {
                worksheet_name: self.data
            },
            'workbook_summary': {
                'summary_line1': line1,
                'summary_line2': line2
            },
            'total_rows': self.summary_stats['total_rows'],
            'total_worksheets': 1,
            'metadata': self.summary_stats
        }