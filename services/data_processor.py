"""
Enhanced Data Processor for Tableau Analytics Agent
Processes Tableau DataFrame data for analysis and aggregation
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Any, Optional, Tuple
import logging
from datetime import datetime
import warnings
from scipy import stats

from models.schemas import AutoAnalysisResult, ImpactColumn, StatisticalResult
from services.NL_to_python import NLToPythonGenerator

warnings.filterwarnings('ignore')

class TableauDataProcessor:
    """
    Enhanced data processor for Tableau DataFrames
    Handles workbook-level data processing and analysis
    """
    
    def __init__(self, openai_client=None):
        self.logger = logging.getLogger(__name__)
        self.nl_to_python = NLToPythonGenerator(openai_client=openai_client)
        self.smart_aggregation_decider = None  # Will be set via set_smart_aggregation()
        
        # Column type categories for better analysis
        self.metric_keywords = [
            'revenue', 'sales', 'amount', 'value', 'total', 'count', 'score', 'rate',
            'price', 'cost', 'profit', 'margin', 'volume', 'quantity', 'performance'
        ]
        
        self.dimension_keywords = [
            'category', 'type', 'region', 'segment', 'status', 'grade', 'level',
            'channel', 'source', 'method', 'team', 'department', 'group'
        ]
        
        self.temporal_keywords = [
            'date', 'time', 'month', 'year', 'quarter', 'week', 'day', 'period'
        ]
    
    def set_smart_aggregation(self, smart_decider):
        """Set smart aggregation decider instance and propagate to NL generator"""
        self.smart_aggregation_decider = smart_decider
        self.nl_to_python.set_smart_aggregation(smart_decider)
        self.logger.info("[SMART_AGGREGATION] Smart aggregation decider set for data processor")

    def process_workbook_data(self, workbook_data: Dict[str, pd.DataFrame]) -> Dict[str, Any]:
        """
        Process entire workbook data and return comprehensive analysis
        
        Args:
            workbook_data: Dictionary of worksheet_name -> DataFrame
            
        Returns:
            Dictionary with processed data and metadata
        """
        try:
            processed_data = {
                'worksheets': {},
                'summary': {},
                'relationships': [],
                'key_metrics': [],
                'total_rows': 0,
                'total_columns': 0
            }
            
            total_rows = 0
            all_columns = set()
            
            # Process each worksheet
            for ws_name, df in workbook_data.items():
                if df is not None and not df.empty:
                    ws_processed = self._process_single_worksheet(ws_name, df)
                    processed_data['worksheets'][ws_name] = ws_processed
                    
                    total_rows += len(df)
                    all_columns.update(df.columns)
            
            processed_data['total_rows'] = total_rows
            processed_data['total_columns'] = len(all_columns)
            
            # Generate cross-worksheet analysis
            processed_data['relationships'] = self._detect_worksheet_relationships(workbook_data)
            processed_data['key_metrics'] = self._identify_key_metrics(workbook_data)
            processed_data['summary'] = self._generate_workbook_summary(processed_data)
            
            self.logger.info(f"Processed workbook with {len(workbook_data)} worksheets, {total_rows:,} total rows")
            
            return processed_data
            
        except Exception as e:
            self.logger.error(f"Error processing workbook data: {e}")
            return self._create_empty_processed_data()

    def _process_single_worksheet(self, worksheet_name: str, df: pd.DataFrame) -> Dict[str, Any]:
        """Process a single worksheet DataFrame"""
        
        try:
            processed = {
                'name': worksheet_name,
                'shape': df.shape,
                'columns': list(df.columns),
                'data_types': self._analyze_column_types(df),
                'numeric_columns': [],
                'categorical_columns': [],
                'datetime_columns': [],
                'missing_data': {},
                'sample_data': [],
                'column_stats': {}
            }
            
            # Categorize columns
            for col in df.columns:
                if pd.api.types.is_numeric_dtype(df[col]):
                    processed['numeric_columns'].append(col)
                elif pd.api.types.is_datetime64_any_dtype(df[col]):
                    processed['datetime_columns'].append(col)
                else:
                    processed['categorical_columns'].append(col)
            
            # Missing data analysis
            processed['missing_data'] = {
                'total_missing': df.isnull().sum().sum(),
                'missing_by_column': df.isnull().sum().to_dict(),
                'missing_percentage': (df.isnull().sum() / len(df) * 100).to_dict()
            }
            
            # Sample data for analysis
            processed['sample_data'] = df.head(5).fillna('').to_dict('records')
            
            # Column statistics
            processed['column_stats'] = self._generate_column_statistics(df)
            
            return processed
            
        except Exception as e:
            self.logger.error(f"Error processing worksheet {worksheet_name}: {e}")
            return {
                'name': worksheet_name,
                'shape': (0, 0),
                'columns': [],
                'error': str(e)
            }

    def _analyze_column_types(self, df: pd.DataFrame) -> Dict[str, str]:
        """Analyze and categorize column types semantically"""
        
        column_types = {}
        
        for col in df.columns:
            col_lower = col.lower()
            
            # Check pandas data type first
            if pd.api.types.is_numeric_dtype(df[col]):
                if any(keyword in col_lower for keyword in self.metric_keywords):
                    column_types[col] = 'metric'
                else:
                    column_types[col] = 'numeric'
                    
            elif pd.api.types.is_datetime64_any_dtype(df[col]):
                column_types[col] = 'datetime'
                
            else:
                # Analyze content for better categorization
                if any(keyword in col_lower for keyword in self.dimension_keywords):
                    column_types[col] = 'dimension'
                elif any(keyword in col_lower for keyword in self.temporal_keywords):
                    column_types[col] = 'temporal_text'
                elif df[col].nunique() / len(df) < 0.5:  # Low cardinality
                    column_types[col] = 'categorical'
                else:
                    column_types[col] = 'text'
        
        return column_types

    def _generate_column_statistics(self, df: pd.DataFrame) -> Dict[str, Any]:
        """Generate comprehensive statistics for each column"""
        
        stats = {}
        
        for col in df.columns:
            col_stats = {
                'dtype': str(df[col].dtype),
                'non_null_count': df[col].count(),
                'null_count': df[col].isnull().sum(),
                'unique_count': df[col].nunique()
            }
            
            if pd.api.types.is_numeric_dtype(df[col]):
                col_stats.update({
                    'mean': float(df[col].mean()) if not df[col].empty else 0,
                    'median': float(df[col].median()) if not df[col].empty else 0,
                    'std': float(df[col].std()) if not df[col].empty else 0,
                    'min': float(df[col].min()) if not df[col].empty else 0,
                    'max': float(df[col].max()) if not df[col].empty else 0,
                    'skewness': float(df[col].skew()) if not df[col].empty else 0
                })
            else:
                # For non-numeric columns
                value_counts = df[col].value_counts().head(5)
                col_stats['top_values'] = value_counts.to_dict()
                col_stats['cardinality_ratio'] = col_stats['unique_count'] / len(df) if len(df) > 0 else 0
            
            stats[col] = col_stats
        
        return stats

    def analyze_column_impact(self, df: pd.DataFrame, target_column: Optional[str] = None) -> List[ImpactColumn]:
        """
        Analyze which columns have the most impact/variance in the data
        
        Args:
            df: DataFrame to analyze
            target_column: Optional target column for impact analysis
            
        Returns:
            List of ImpactColumn objects sorted by impact
        """
        try:
            impact_columns = []
            
            numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
            categorical_cols = df.select_dtypes(include=['object']).columns.tolist()
            
            # If target column specified, calculate correlations
            if target_column and target_column in numeric_cols:
                correlations = df[numeric_cols].corrwith(df[target_column]).abs().sort_values(ascending=False)
                
                for col in correlations.index:
                    if col != target_column:
                        impact_score = float(correlations[col]) if not pd.isna(correlations[col]) else 0.0
                        
                        impact_columns.append(ImpactColumn(
                            column_name=col,
                            impact_score=impact_score,
                            variance_explained=impact_score * 100,
                            data_type=self._get_column_semantic_type(col, df),
                            sample_values=self._get_sample_values(df[col])
                        ))
            
            else:
                # General impact analysis based on variance and business relevance
                for col in df.columns:
                    impact_score = self._calculate_general_impact_score(col, df)
                    
                    impact_columns.append(ImpactColumn(
                        column_name=col,
                        impact_score=impact_score,
                        variance_explained=impact_score * 100,
                        data_type=self._get_column_semantic_type(col, df),
                        sample_values=self._get_sample_values(df[col])
                    ))
            
            # Sort by impact score and return top 5
            impact_columns.sort(key=lambda x: x.impact_score, reverse=True)
            return impact_columns[:5]
            
        except Exception as e:
            self.logger.error(f"Error analyzing column impact: {e}")
            return []

    def _calculate_general_impact_score(self, column: str, df: pd.DataFrame) -> float:
        """Calculate general impact score for a column"""
        
        score = 0.5  # Base score
        col_lower = column.lower()
        
        # Business relevance boost
        if any(keyword in col_lower for keyword in self.metric_keywords):
            score += 0.3
        elif any(keyword in col_lower for keyword in self.dimension_keywords):
            score += 0.2
        
        # Data variance boost
        try:
            if pd.api.types.is_numeric_dtype(df[column]):
                # Coefficient of variation for numeric columns
                if df[column].std() > 0 and df[column].mean() != 0:
                    cv = df[column].std() / abs(df[column].mean())
                    score += min(cv / 10, 0.2)  # Cap at 0.2
            else:
                # Cardinality ratio for categorical columns
                cardinality_ratio = df[column].nunique() / len(df)
                if 0.1 < cardinality_ratio < 0.8:  # Sweet spot for meaningful categories
                    score += 0.15
        except:
            pass
        
        return min(score, 1.0)  # Cap at 1.0

    def _get_column_semantic_type(self, column: str, df: pd.DataFrame) -> str:
        """Get semantic type of column"""
        col_lower = column.lower()
        
        if any(keyword in col_lower for keyword in self.metric_keywords):
            return 'metric'
        elif any(keyword in col_lower for keyword in self.dimension_keywords):
            return 'dimension'
        elif any(keyword in col_lower for keyword in self.temporal_keywords):
            return 'temporal'
        elif pd.api.types.is_numeric_dtype(df[column]):
            return 'numeric'
        else:
            return 'categorical'

    def _get_sample_values(self, series: pd.Series) -> List[str]:
        """Get sample values from a series"""
        try:
            non_null_values = series.dropna()
            if len(non_null_values) == 0:
                return ['No data']
            
            # Get up to 3 sample values
            sample_values = non_null_values.head(3).astype(str).tolist()
            return [val[:50] for val in sample_values]  # Truncate long values
        except:
            return ['Error reading values']

    def execute_pandas_aggregation(self, query: str, df: pd.DataFrame) -> Dict[str, Any]:
        """
        Execute pandas aggregation based on natural language query
        
        Args:
            query: Natural language query
            df: DataFrame to operate on
            
        Returns:
            Dictionary with aggregation results
        """
        try:
            # Generate pandas code
            nl_result = self.nl_to_python.generate_python_code(query, df.columns.tolist(), df)
            
            # Execute the code
            result, status = self.nl_to_python.execute_code(nl_result.generated_code, df)
            
            # Format the result
            aggregation_result = {
                'query': query,
                'generated_code': nl_result.generated_code,
                'operation_type': nl_result.operation_type,
                'confidence': nl_result.confidence,
                'explanation': nl_result.explanation,
                'execution_status': status,
                'result': self._format_aggregation_result(result),
                'result_type': type(result).__name__
            }
            
            self.logger.info(f"Executed aggregation for query: '{query}' -> {nl_result.operation_type}")
            
            return aggregation_result
            
        except Exception as e:
            self.logger.error(f"Error executing pandas aggregation: {e}")
            
            # Try to preserve partial result if execution started
            partial_result = None
            partial_preserved = False
            
            # Only preserve if we successfully got some result before exception
            if 'result' in locals() and result is not None:
                try:
                    partial_result = self._format_aggregation_result(result)
                    partial_preserved = True
                    self.logger.info(f"[PARTIAL] Preserved partial result despite error (type: {type(result).__name__})")
                except Exception as format_error:
                    self.logger.warning(f"[PARTIAL] Could not format partial result: {format_error}")
            else:
                self.logger.info(f"[PARTIAL] No partial result available to preserve")
            
            return {
                'query': query,
                'execution_status': 'error',
                'error': str(e),
                'result': partial_result,  # May be None
                'partial': partial_preserved  # Flag for debugging
            }

    def _format_aggregation_result(self, result: Any) -> Any:
        """Format aggregation result for JSON serialization"""
        
        if result is None:
            return None
        
        if isinstance(result, pd.DataFrame):
            return {
                'type': 'dataframe',
                'data': result.head(20).fillna('').to_dict('records'),
                'index': result.head(20).index.tolist(),
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

    def calculate_statistical_summary(self, df: pd.DataFrame) -> Dict[str, Any]:
        """Calculate comprehensive statistical summary"""
        
        try:
            summary = {
                'dataset_overview': {
                    'total_rows': len(df),
                    'total_columns': len(df.columns),
                    'missing_data_percentage': (df.isnull().sum().sum() / df.size) * 100,
                    'memory_usage_mb': df.memory_usage(deep=True).sum() / 1024 / 1024
                },
                'column_analysis': {},
                'correlations': {},
                'statistical_tests': []
            }
            
            # Numeric column analysis
            numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
            if len(numeric_cols) > 0:
                summary['column_analysis']['numeric'] = df[numeric_cols].describe().to_dict()
                
                # Correlation analysis
                if len(numeric_cols) > 1:
                    corr_matrix = df[numeric_cols].corr()
                    summary['correlations'] = {
                        'matrix': corr_matrix.to_dict(),
                        'strong_correlations': self._find_strong_correlations(corr_matrix)
                    }
            
            # Categorical column analysis
            categorical_cols = df.select_dtypes(include=['object']).columns.tolist()
            if len(categorical_cols) > 0:
                summary['column_analysis']['categorical'] = {}
                for col in categorical_cols[:5]:  # Limit to first 5
                    summary['column_analysis']['categorical'][col] = {
                        'unique_count': df[col].nunique(),
                        'top_values': df[col].value_counts().head(5).to_dict()
                    }
            
            # Statistical tests
            summary['statistical_tests'] = self._perform_statistical_tests(df)
            
            return summary
            
        except Exception as e:
            self.logger.error(f"Error calculating statistical summary: {e}")
            return {'error': str(e)}

    def _find_strong_correlations(self, corr_matrix: pd.DataFrame) -> List[Dict[str, Any]]:
        """Find strong correlations in the correlation matrix"""
        
        strong_corrs = []
        
        for i in range(len(corr_matrix.columns)):
            for j in range(i + 1, len(corr_matrix.columns)):
                corr_value = corr_matrix.iloc[i, j]
                if abs(corr_value) > 0.7:  # Strong correlation threshold
                    strong_corrs.append({
                        'column1': corr_matrix.columns[i],
                        'column2': corr_matrix.columns[j],
                        'correlation': float(corr_value),
                        'strength': 'strong' if abs(corr_value) > 0.8 else 'moderate'
                    })
        
        return sorted(strong_corrs, key=lambda x: abs(x['correlation']), reverse=True)

    def _perform_statistical_tests(self, df: pd.DataFrame) -> List[Dict[str, Any]]:
        """Perform basic statistical tests"""
        
        tests = []
        numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
        
        # Normality tests for numeric columns
        for col in numeric_cols[:3]:  # Limit to first 3
            try:
                data = df[col].dropna()
                if len(data) > 3:
                    statistic, p_value = stats.normaltest(data)
                    tests.append({
                        'test_type': 'normality_test',
                        'column': col,
                        'statistic': float(statistic),
                        'p_value': float(p_value),
                        'is_normal': p_value > 0.05,
                        'interpretation': f"Data {'appears normal' if p_value > 0.05 else 'does not appear normal'} (p={p_value:.3f})"
                    })
            except:
                continue
        
        return tests

    def _detect_worksheet_relationships(self, workbook_data: Dict[str, pd.DataFrame]) -> List[Dict[str, Any]]:
        """Detect relationships between worksheets"""
        
        relationships = []
        worksheet_names = list(workbook_data.keys())
        
        for i, ws1_name in enumerate(worksheet_names):
            for j, ws2_name in enumerate(worksheet_names):
                if i >= j:
                    continue
                
                df1 = workbook_data[ws1_name]
                df2 = workbook_data[ws2_name]
                
                if df1 is None or df2 is None:
                    continue
                
                # Find common columns
                common_cols = set(df1.columns) & set(df2.columns)
                if common_cols:
                    relationships.append({
                        'worksheet1': ws1_name,
                        'worksheet2': ws2_name,
                        'relationship_type': 'common_columns',
                        'common_columns': list(common_cols),
                        'strength': len(common_cols) / max(len(df1.columns), len(df2.columns))
                    })
        
        return relationships

    def _identify_key_metrics(self, workbook_data: Dict[str, pd.DataFrame]) -> List[str]:
        """Identify key metrics across all worksheets"""
        
        key_metrics = set()
        
        for ws_name, df in workbook_data.items():
            if df is None:
                continue
            
            for col in df.columns:
                col_lower = col.lower()
                if any(keyword in col_lower for keyword in self.metric_keywords):
                    key_metrics.add(col)
        
        return sorted(list(key_metrics))

    def _generate_workbook_summary(self, processed_data: Dict[str, Any]) -> Dict[str, Any]:
        """Generate high-level workbook summary"""
        
        return {
            'total_worksheets': len(processed_data['worksheets']),
            'total_rows': processed_data['total_rows'],
            'total_columns': processed_data['total_columns'],
            'key_metrics_count': len(processed_data['key_metrics']),
            'relationships_count': len(processed_data['relationships']),
            'has_temporal_data': any(
                'datetime_columns' in ws and len(ws['datetime_columns']) > 0
                for ws in processed_data['worksheets'].values()
            ),
            'has_numeric_data': any(
                'numeric_columns' in ws and len(ws['numeric_columns']) > 0
                for ws in processed_data['worksheets'].values()
            )
        }

    def _create_empty_processed_data(self) -> Dict[str, Any]:
        """Create empty processed data structure for error cases"""
        return {
            'worksheets': {},
            'summary': {'total_worksheets': 0, 'total_rows': 0, 'total_columns': 0},
            'relationships': [],
            'key_metrics': [],
            'total_rows': 0,
            'total_columns': 0,
            'error': True
        }
