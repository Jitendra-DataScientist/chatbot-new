"""
Multi-Table Service for Tableau Analytics Agent
Handles cross-worksheet analysis and relationships using DataFrames
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Any, Optional, Tuple
import logging
from datetime import datetime
import re

from models.schemas import QueryIntent, NLToPythonResult
from services.NL_to_python import NLToPythonGenerator

class TableauMultiTableService:
    """
    Handles multi-worksheet analysis for Tableau workbooks
    Replaces SQL operations with DataFrame operations for cross-worksheet analysis
    """
    
    def __init__(self, openai_client=None):
        self.logger = logging.getLogger(__name__)
        self.nl_to_python = NLToPythonGenerator(openai_client=openai_client)
        self.worksheets: Dict[str, pd.DataFrame] = {}
        self.worksheet_metadata: Dict[str, Dict[str, Any]] = {}
        
        # Common join patterns for worksheet relationships
        self.join_candidates = [
            'id', 'customer_id', 'product_id', 'order_id', 'user_id',
            'date', 'month', 'year', 'period', 'region', 'category'
        ]

    def add_worksheet_data(self, worksheet_name: str, dataframe: pd.DataFrame, metadata: Optional[Dict[str, Any]] = None):
        """Add worksheet data to the multi-table service"""
        try:
            if dataframe is not None and not dataframe.empty:
                self.worksheets[worksheet_name] = dataframe.copy()
                self.worksheet_metadata[worksheet_name] = metadata or {}
                
                self.logger.info(f"Added worksheet '{worksheet_name}' with shape {dataframe.shape}")
            else:
                self.logger.warning(f"Empty or None dataframe provided for worksheet '{worksheet_name}'")
                
        except Exception as e:
            self.logger.error(f"Error adding worksheet data for '{worksheet_name}': {e}")

    def get_workbook_schema(self) -> Dict[str, Any]:
        """Get comprehensive schema information for all worksheets"""
        try:
            schema = {
                'worksheets': {},
                'total_worksheets': len(self.worksheets),
                'total_rows': 0,
                'relationships': [],
                'common_columns': [],
                'key_metrics': []
            }
            
            total_rows = 0
            all_columns = set()
            
            # Analyze each worksheet
            for ws_name, df in self.worksheets.items():
                if df is not None:
                    ws_info = {
                        'name': ws_name,
                        'shape': df.shape,
                        'columns': list(df.columns),
                        'numeric_columns': df.select_dtypes(include=[np.number]).columns.tolist(),
                        'categorical_columns': df.select_dtypes(include=['object']).columns.tolist(),
                        'dtypes': {col: str(dtype) for col, dtype in df.dtypes.items()},
                        'sample_data': df.head(3).fillna('').to_dict('records')
                    }
                    
                    schema['worksheets'][ws_name] = ws_info
                    total_rows += len(df)
                    all_columns.update(df.columns)
            
            schema['total_rows'] = total_rows
            schema['relationships'] = self._detect_cross_worksheet_relationships()
            schema['common_columns'] = self._find_common_columns()
            schema['key_metrics'] = self._identify_key_metrics()
            
            return schema
            
        except Exception as e:
            self.logger.error(f"Error getting workbook schema: {e}")
            return {'error': str(e)}

    def execute_cross_worksheet_query(self, query: str, intent: Optional[QueryIntent] = None) -> Dict[str, Any]:
        """
        Execute a query that spans multiple worksheets
        
        Args:
            query: Natural language query
            intent: Parsed query intent
            
        Returns:
            Dictionary with query results
        """
        try:
            self.logger.info(f"Executing cross-worksheet query: '{query}'")
            
            # Analyze query to identify required worksheets
            required_worksheets = self._identify_required_worksheets(query)
            
            if not required_worksheets:
                # Default to all worksheets
                required_worksheets = list(self.worksheets.keys())
            
            # Determine analysis type based on query
            analysis_type = self._determine_analysis_type(query, intent)
            
            # Execute the appropriate analysis
            if analysis_type == 'join_analysis':
                result = self._execute_join_analysis(query, required_worksheets)
            elif analysis_type == 'comparison_analysis':
                result = self._execute_comparison_analysis(query, required_worksheets)
            elif analysis_type == 'aggregation_analysis':
                result = self._execute_aggregation_analysis(query, required_worksheets)
            elif analysis_type == 'correlation_analysis':
                result = self._execute_correlation_analysis(query, required_worksheets)
            else:
                result = self._execute_general_analysis(query, required_worksheets)
            
            return {
                'success': True,
                'query': query,
                'analysis_type': analysis_type,
                'worksheets_used': required_worksheets,
                'result': result,
                'execution_time': datetime.now().isoformat()
            }
            
        except Exception as e:
            self.logger.error(f"Error executing cross-worksheet query: {e}")
            return {
                'success': False,
                'query': query,
                'error': str(e),
                'worksheets_used': []
            }

    def _identify_required_worksheets(self, query: str) -> List[str]:
        """Identify which worksheets are needed for the query"""
        
        required = []
        query_lower = query.lower()
        
        # Look for explicit worksheet mentions
        for ws_name in self.worksheets.keys():
            if ws_name.lower() in query_lower:
                required.append(ws_name)
        
        # Look for column mentions that might indicate specific worksheets
        if not required:
            for ws_name, df in self.worksheets.items():
                for col in df.columns:
                    if col.lower() in query_lower:
                        if ws_name not in required:
                            required.append(ws_name)
        
        # If still no specific worksheets identified, use heuristics
        if not required:
            # For comparison queries, try to use all worksheets
            if any(word in query_lower for word in ['compare', 'vs', 'versus', 'between']):
                required = list(self.worksheets.keys())
            # For aggregation queries, look for worksheets with numeric data
            elif any(word in query_lower for word in ['sum', 'total', 'average', 'count']):
                for ws_name, df in self.worksheets.items():
                    if len(df.select_dtypes(include=[np.number]).columns) > 0:
                        required.append(ws_name)
        
        return required[:5]  # Limit to 5 worksheets to avoid complexity

    def _determine_analysis_type(self, query: str, intent: Optional[QueryIntent] = None) -> str:
        """Determine what type of cross-worksheet analysis to perform"""
        
        query_lower = query.lower()
        
        # Use intent if available
        if intent:
            intent_mapping = {
                'comparison': 'comparison_analysis',
                'statistical_significance': 'correlation_analysis',
                'shap_analysis': 'correlation_analysis',
                'trend_analysis': 'aggregation_analysis',
                'data_exploration': 'general_analysis'
            }
            return intent_mapping.get(intent.primary_intent, 'general_analysis')
        
        # Fallback to keyword analysis
        if any(word in query_lower for word in ['join', 'merge', 'combine', 'relate']):
            return 'join_analysis'
        elif any(word in query_lower for word in ['compare', 'vs', 'versus', 'difference']):
            return 'comparison_analysis'
        elif any(word in query_lower for word in ['correlation', 'relationship', 'related']):
            return 'correlation_analysis'
        elif any(word in query_lower for word in ['sum', 'total', 'aggregate', 'group']):
            return 'aggregation_analysis'
        else:
            return 'general_analysis'

    def _execute_join_analysis(self, query: str, worksheets: List[str]) -> Dict[str, Any]:
        """Execute analysis that requires joining worksheets"""
        
        try:
            if len(worksheets) < 2:
                return {'error': 'Join analysis requires at least 2 worksheets'}
            
            # Find potential join keys
            join_key = self._find_best_join_key(worksheets[:2])
            
            if not join_key:
                return {'error': 'No suitable join key found between worksheets'}
            
            # Perform the join
            df1 = self.worksheets[worksheets[0]]
            df2 = self.worksheets[worksheets[1]]
            
            # Add suffixes to avoid column name conflicts
            merged_df = pd.merge(
                df1, df2, 
                on=join_key, 
                how='inner',
                suffixes=('_ws1', '_ws2')
            )
            
            if merged_df.empty:
                return {'error': f'No matching records found when joining on {join_key}'}
            
            # Generate aggregation based on query
            aggregation_result = self.nl_to_python.generate_pandas_code(
                query, merged_df.columns.tolist(), merged_df
            )
            
            # Execute the aggregation
            result, status = self.nl_to_python.execute_code(aggregation_result.generated_code, merged_df)
            
            return {
                'analysis_type': 'join_analysis',
                'join_key': join_key,
                'joined_worksheets': worksheets[:2],
                'merged_shape': merged_df.shape,
                'aggregation_code': aggregation_result.generated_code,
                'result': self._format_result(result),
                'sample_merged_data': merged_df.head(5).fillna('').to_dict('records')
            }
            
        except Exception as e:
            self.logger.error(f"Error in join analysis: {e}")
            return {'error': str(e)}

    def _execute_comparison_analysis(self, query: str, worksheets: List[str]) -> Dict[str, Any]:
        """Execute comparison analysis across worksheets"""
        
        try:
            comparison_results = {}
            
            # Compare similar metrics across worksheets
            for ws_name in worksheets:
                df = self.worksheets[ws_name]
                
                # Generate basic statistics for numeric columns
                numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
                if numeric_cols:
                    stats = {}
                    for col in numeric_cols[:3]:  # Limit to first 3 numeric columns
                        stats[col] = {
                            'mean': float(df[col].mean()),
                            'median': float(df[col].median()),
                            'std': float(df[col].std()),
                            'count': int(df[col].count())
                        }
                    comparison_results[ws_name] = {
                        'row_count': len(df),
                        'column_count': len(df.columns),
                        'numeric_stats': stats
                    }
            
            # Find common metrics for direct comparison
            common_metrics = self._find_common_numeric_columns(worksheets)
            
            if common_metrics:
                metric_comparison = {}
                for metric in common_metrics[:3]:  # Limit to 3 metrics
                    metric_comparison[metric] = {}
                    for ws_name in worksheets:
                        df = self.worksheets[ws_name]
                        if metric in df.columns:
                            metric_comparison[metric][ws_name] = {
                                'total': float(df[metric].sum()),
                                'average': float(df[metric].mean()),
                                'max': float(df[metric].max()),
                                'min': float(df[metric].min())
                            }
                
                comparison_results['metric_comparison'] = metric_comparison
            
            return {
                'analysis_type': 'comparison_analysis',
                'worksheets_compared': worksheets,
                'comparison_results': comparison_results,
                'common_metrics': common_metrics
            }
            
        except Exception as e:
            self.logger.error(f"Error in comparison analysis: {e}")
            return {'error': str(e)}

    def _execute_aggregation_analysis(self, query: str, worksheets: List[str]) -> Dict[str, Any]:
        """Execute aggregation analysis across worksheets"""
        
        try:
            aggregation_results = {}
            
            for ws_name in worksheets:
                df = self.worksheets[ws_name]
                
                # Generate pandas code for the query
                aggregation_result = self.nl_to_python.generate_pandas_code(
                    query, df.columns.tolist(), df
                )
                
                # Execute the aggregation
                result, status = self.nl_to_python.execute_code(aggregation_result.generated_code, df)
                
                aggregation_results[ws_name] = {
                    'code': aggregation_result.generated_code,
                    'operation_type': aggregation_result.operation_type,
                    'result': self._format_result(result),
                    'status': status
                }
            
            return {
                'analysis_type': 'aggregation_analysis',
                'worksheets_analyzed': worksheets,
                'aggregation_results': aggregation_results
            }
            
        except Exception as e:
            self.logger.error(f"Error in aggregation analysis: {e}")
            return {'error': str(e)}

    def _execute_correlation_analysis(self, query: str, worksheets: List[str]) -> Dict[str, Any]:
        """Execute correlation analysis across worksheets"""
        
        try:
            correlation_results = {}
            
            # Analyze correlations within each worksheet
            for ws_name in worksheets:
                df = self.worksheets[ws_name]
                numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
                
                if len(numeric_cols) > 1:
                    corr_matrix = df[numeric_cols].corr()
                    
                    # Find strong correlations
                    strong_correlations = []
                    for i in range(len(numeric_cols)):
                        for j in range(i + 1, len(numeric_cols)):
                            corr_val = corr_matrix.iloc[i, j]
                            if abs(corr_val) > 0.5:  # Threshold for "strong"
                                strong_correlations.append({
                                    'column1': numeric_cols[i],
                                    'column2': numeric_cols[j],
                                    'correlation': float(corr_val)
                                })
                    
                    correlation_results[ws_name] = {
                        'correlation_matrix': corr_matrix.to_dict(),
                        'strong_correlations': sorted(strong_correlations, 
                                                     key=lambda x: abs(x['correlation']), 
                                                     reverse=True)
                    }
            
            # Cross-worksheet correlation if possible
            cross_correlations = self._calculate_cross_worksheet_correlations(worksheets)
            if cross_correlations:
                correlation_results['cross_worksheet'] = cross_correlations
            
            return {
                'analysis_type': 'correlation_analysis',
                'worksheets_analyzed': worksheets,
                'correlation_results': correlation_results
            }
            
        except Exception as e:
            self.logger.error(f"Error in correlation analysis: {e}")
            return {'error': str(e)}

    def _execute_general_analysis(self, query: str, worksheets: List[str]) -> Dict[str, Any]:
        """Execute general analysis across worksheets"""
        
        try:
            analysis_results = {}
            
            for ws_name in worksheets:
                df = self.worksheets[ws_name]
                
                # Basic analysis
                numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
                categorical_cols = df.select_dtypes(include=['object']).columns.tolist()
                
                worksheet_analysis = {
                    'shape': df.shape,
                    'numeric_summary': {},
                    'categorical_summary': {},
                    'missing_data': {
                        'total_missing': int(df.isnull().sum().sum()),
                        'missing_percentage': float((df.isnull().sum().sum() / df.size) * 100)
                    }
                }
                
                # Numeric analysis
                if numeric_cols:
                    for col in numeric_cols[:3]:
                        worksheet_analysis['numeric_summary'][col] = {
                            'mean': float(df[col].mean()),
                            'std': float(df[col].std()),
                            'min': float(df[col].min()),
                            'max': float(df[col].max())
                        }
                
                # Categorical analysis
                if categorical_cols:
                    for col in categorical_cols[:3]:
                        top_values = df[col].value_counts().head(3)
                        worksheet_analysis['categorical_summary'][col] = {
                            'unique_count': int(df[col].nunique()),
                            'top_values': top_values.to_dict()
                        }
                
                analysis_results[ws_name] = worksheet_analysis
            
            return {
                'analysis_type': 'general_analysis',
                'worksheets_analyzed': worksheets,
                'analysis_results': analysis_results
            }
            
        except Exception as e:
            self.logger.error(f"Error in general analysis: {e}")
            return {'error': str(e)}

    def _find_best_join_key(self, worksheets: List[str]) -> Optional[str]:
        """Find the best column to join two worksheets on"""
        
        if len(worksheets) < 2:
            return None
        
        df1 = self.worksheets[worksheets[0]]
        df2 = self.worksheets[worksheets[1]]
        
        # Find common columns
        common_cols = set(df1.columns) & set(df2.columns)
        
        # Prefer known join candidates
        for candidate in self.join_candidates:
            for col in common_cols:
                if candidate in col.lower():
                    # Check if there are matching values
                    if not (set(df1[col].dropna()) & set(df2[col].dropna())):
                        continue
                    return col
        
        # Fallback to any common column with matching values
        for col in common_cols:
            if len(set(df1[col].dropna()) & set(df2[col].dropna())) > 0:
                return col
        
        return None

    def _find_common_columns(self) -> List[str]:
        """Find columns that appear in multiple worksheets"""
        
        if len(self.worksheets) < 2:
            return []
        
        column_counts = {}
        for df in self.worksheets.values():
            for col in df.columns:
                column_counts[col] = column_counts.get(col, 0) + 1
        
        # Return columns that appear in at least 2 worksheets
        return [col for col, count in column_counts.items() if count >= 2]

    def _find_common_numeric_columns(self, worksheets: List[str]) -> List[str]:
        """Find numeric columns that appear in multiple specified worksheets"""
        
        if len(worksheets) < 2:
            return []
        
        numeric_column_sets = []
        for ws_name in worksheets:
            if ws_name in self.worksheets:
                df = self.worksheets[ws_name]
                numeric_cols = set(df.select_dtypes(include=[np.number]).columns)
                numeric_column_sets.append(numeric_cols)
        
        if not numeric_column_sets:
            return []
        
        # Find intersection of all sets
        common_numeric_cols = numeric_column_sets[0]
        for col_set in numeric_column_sets[1:]:
            common_numeric_cols = common_numeric_cols.intersection(col_set)
        
        return list(common_numeric_cols)

    def _calculate_cross_worksheet_correlations(self, worksheets: List[str]) -> Optional[Dict[str, Any]]:
        """Calculate correlations between common columns across worksheets"""
        
        try:
            common_numeric_cols = self._find_common_numeric_columns(worksheets)
            
            if len(common_numeric_cols) == 0 or len(worksheets) < 2:
                return None
            
            cross_correlations = {}
            
            # Compare each pair of worksheets
            for i, ws1_name in enumerate(worksheets):
                for j, ws2_name in enumerate(worksheets):
                    if i >= j:
                        continue
                    
                    df1 = self.worksheets[ws1_name]
                    df2 = self.worksheets[ws2_name]
                    
                    pair_correlations = {}
                    for col in common_numeric_cols:
                        if col in df1.columns and col in df2.columns:
                            # Calculate correlation between the same column in different worksheets
                            try:
                                corr = df1[col].corr(df2[col])
                                if not pd.isna(corr):
                                    pair_correlations[col] = float(corr)
                            except:
                                continue
                    
                    if pair_correlations:
                        cross_correlations[f"{ws1_name}_vs_{ws2_name}"] = pair_correlations
            
            return cross_correlations if cross_correlations else None
            
        except Exception as e:
            self.logger.error(f"Error calculating cross-worksheet correlations: {e}")
            return None

    def _detect_cross_worksheet_relationships(self) -> List[Dict[str, Any]]:
        """Detect potential relationships between worksheets"""
        
        relationships = []
        worksheet_names = list(self.worksheets.keys())
        
        for i, ws1_name in enumerate(worksheet_names):
            for j, ws2_name in enumerate(worksheet_names):
                if i >= j:
                    continue
                
                df1 = self.worksheets[ws1_name]
                df2 = self.worksheets[ws2_name]
                
                # Find common columns
                common_cols = set(df1.columns) & set(df2.columns)
                
                if common_cols:
                    # Analyze the strength of the relationship
                    strong_relationships = []
                    for col in common_cols:
                        # Check if there are actually overlapping values
                        overlap = len(set(df1[col].dropna()) & set(df2[col].dropna()))
                        if overlap > 0:
                            strong_relationships.append({
                                'column': col,
                                'overlapping_values': overlap,
                                'df1_unique': df1[col].nunique(),
                                'df2_unique': df2[col].nunique()
                            })
                    
                    if strong_relationships:
                        relationships.append({
                            'worksheet1': ws1_name,
                            'worksheet2': ws2_name,
                            'relationship_type': 'common_columns',
                            'common_columns': list(common_cols),
                            'strong_relationships': strong_relationships,
                            'relationship_strength': len(strong_relationships) / len(common_cols)
                        })
        
        return relationships

    def _identify_key_metrics(self) -> List[str]:
        """Identify key metrics across all worksheets"""
        
        metric_keywords = [
            'revenue', 'sales', 'amount', 'value', 'total', 'count', 'score',
            'rate', 'price', 'cost', 'profit', 'volume', 'quantity'
        ]
        
        key_metrics = set()
        
        for df in self.worksheets.values():
            for col in df.columns:
                col_lower = col.lower()
                if any(keyword in col_lower for keyword in metric_keywords):
                    key_metrics.add(col)
        
        return sorted(list(key_metrics))

    def _format_result(self, result: Any) -> Any:
        """Format result for JSON serialization"""
        
        if result is None:
            return None
        
        if isinstance(result, pd.DataFrame):
            return {
                'type': 'dataframe',
                'data': result.head(20).fillna('').to_dict('records'),
                'shape': result.shape,
                'columns': result.columns.tolist()
            }
        
        elif isinstance(result, pd.Series):
            return {
                'type': 'series',
                'data': result.head(20).fillna('').to_dict(),
                'name': result.name,
                'length': len(result)
            }
        
        elif isinstance(result, (int, float, np.integer, np.floating)):
            return {
                'type': 'scalar',
                'value': float(result) if not pd.isna(result) else None
            }
        
        else:
            return {
                'type': 'other',
                'value': str(result)
            }

    def get_worksheet_list(self) -> List[Dict[str, Any]]:
        """Get list of available worksheets with metadata"""
        
        worksheets = []
        
        for ws_name, df in self.worksheets.items():
            metadata = self.worksheet_metadata.get(ws_name, {})
            
            worksheets.append({
                'name': ws_name,
                'shape': df.shape,
                'columns': len(df.columns),
                'numeric_columns': len(df.select_dtypes(include=[np.number]).columns),
                'categorical_columns': len(df.select_dtypes(include=['object']).columns),
                'metadata': metadata
            })
        
        return worksheets

    def clear_all_data(self):
        """Clear all worksheet data"""
        self.worksheets.clear()
        self.worksheet_metadata.clear()
        self.logger.info("Cleared all worksheet data from multi-table service")
