"""
Enhanced Query Understanding Agent for Tableau Analytics Agent
Integrated with services for comprehensive query processing and intent classification
"""

import asyncio
import json
from typing import Dict, List, Any, Optional
import logging
from datetime import datetime
import sys
import os

# Add parent directory to path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Import services
from models.schemas import QueryIntent, IntentType, EnhancedChatRequest, AutoAnalysisResult
from services.llm_service import LLMService
from services.data_processor import TableauDataProcessor
from services.visualization_service import IntelligentVisualizationService
from services.multi_table_service import TableauMultiTableService
from services.insight_generator import TableauInsightGenerator
from services.NL_to_python import NLToPythonGenerator
from services.fuzzy_column_matcher import FuzzyColumnMatcher

# Configure logging with master logger integration
from master_logger import setup_module_logger
logger = setup_module_logger('meta_agents.enhanced_query_agent')

class EnhancedQueryAgent:
    """
    Enhanced Query Understanding Agent integrated with all services
    Orchestrates the entire analytics pipeline based on user intent
    """
    
    def __init__(self, openai_client=None):
        logger.info("Initializing Enhanced Query Agent with all services")
        
        # Initialize all services
        self.llm_service = LLMService(openai_client=openai_client)
        self.data_processor = TableauDataProcessor(openai_client=openai_client)
        self.viz_service = IntelligentVisualizationService()
        self.multi_table_service = TableauMultiTableService(openai_client=openai_client)
        self.insight_generator = TableauInsightGenerator()
        self.nl_to_python = NLToPythonGenerator(openai_client=openai_client)
        self.fuzzy_matcher = FuzzyColumnMatcher(threshold=70)  # Lowered to 70% for better misspelling tolerance
        self.smart_aggregation_decider = None  # Will be set via set_smart_aggregation()
    
    def set_smart_aggregation(self, smart_decider):
        """Set smart aggregation decider and propagate to services that need it"""
        self.smart_aggregation_decider = smart_decider
        
        # Set on data processor (which will propagate to its NL_to_python instance)
        if hasattr(self.data_processor, 'set_smart_aggregation'):
            self.data_processor.set_smart_aggregation(smart_decider)
        
        # Set directly on our NL_to_python instance
        if hasattr(self.nl_to_python, 'set_smart_aggregation'):
            self.nl_to_python.set_smart_aggregation(smart_decider)
        
        logger.info("[SMART_AGGREGATION] Smart aggregation set on EnhancedQueryAgent and propagated to services")
        
        # Enhanced intent classification with 9 primary categories
        self.intent_categories = {
            "anomaly_detection": {
                "keywords": ["anomaly", "outlier", "unusual", "abnormal", "detect", "exception", "irregular"],
                "description": "Identify anomalies and outliers in data",
                "services": ["data_processor", "visualization", "insight_generator"]
            },
            "trend_analysis": {
                "keywords": ["trend", "over time", "temporal", "timeline", "progression", "pattern", "change"],
                "description": "Analyze trends and temporal patterns",
                "services": ["data_processor", "visualization", "multi_table"]
            },
            "statistical_significance": {
                "keywords": ["significant", "p-value", "hypothesis", "test", "correlation", "statistical"],
                "description": "Perform statistical significance testing",
                "services": ["data_processor", "insight_generator"]
            },
            "comparison": {
                "keywords": ["compare", "vs", "versus", "difference", "between", "against", "contrast"],
                "description": "Compare different groups or segments",
                "services": ["data_processor", "visualization", "multi_table"]
            },
            "top_bottom_analysis": {
                "keywords": ["top", "bottom", "highest", "lowest", "best", "worst", "ranking", "rank"],
                "description": "Identify top and bottom performers",
                "services": ["data_processor", "nl_to_python", "visualization"]
            },
            "seasonality": {
                "keywords": ["seasonal", "cyclical", "periodic", "recurring", "monthly", "quarterly"],
                "description": "Analyze seasonal patterns and cycles",
                "services": ["data_processor", "visualization", "insight_generator"]
            },
            "prediction": {
                "keywords": ["predict", "forecast", "future", "estimate", "projection", "anticipate"],
                "description": "Generate predictions and forecasts",
                "services": ["data_processor", "insight_generator", "visualization"]
            },
            "data_exploration": {
                "keywords": ["explore", "overview", "summary", "describe", "analyze", "insight", "understand"],
                "description": "General data exploration and insights",
                "services": ["data_processor", "visualization", "insight_generator"]
            }
        }
        
        # Common entities to extract
        self.entity_patterns = {
            "metrics": ["revenue", "sales", "profit", "cost", "volume", "count", "amount", "value"],
            "dimensions": ["region", "category", "segment", "channel", "product", "customer", "time"],
            "time_periods": ["month", "quarter", "year", "week", "day", "period"],
            "aggregations": ["sum", "average", "count", "max", "min", "median"]
        }
        
        logger.info("Enhanced Query Agent initialized successfully")

    async def process_enhanced_query(self, request: EnhancedChatRequest, workbook_data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Main orchestration method for processing queries with full service integration
        
        Args:
            request: Enhanced chat request with context
            workbook_data: Available workbook data if any
            
        Returns:
            Comprehensive response with analysis, visualization, and insights
        """
        try:
            start_time = datetime.now()
            logger.info(f"Processing enhanced query: '{request.message[:100]}...'")
            
            # Store workbook_data for potential chart statistics use
            self._current_workbook_data = workbook_data
            
            # Step 1: Understand the query intent
            intent_result = await self.understand_query(request.message, request.context)
            
            # Step 2: Determine if auto-analysis is needed (chart selection case)
            logger.info(f"=== AUTO-ANALYSIS CHECK ===")
            logger.info(f"request.selected_chart: {request.selected_chart}")
            logger.info(f"workbook_data exists: {workbook_data is not None}")
            if workbook_data:
                logger.info(f"workbook_data keys: {list(workbook_data.keys())}")
                logger.info(f"workbook_data length: {len(workbook_data)}")
            
            if request.selected_chart and workbook_data:
                logger.info(f"SUCCESS: Attempting to generate auto-analysis for chart: {request.selected_chart}")
                try:
                    # Extract workbook_id from request context (for nested cache structure)
                    workbook_id = request.context.workbook_id if request.context else None
                    csv_path = request.context.csv_file_path if request.context else None
                    auto_analysis = await self._generate_auto_analysis_for_chart(
                        request.selected_chart, workbook_data, intent_result, workbook_id=workbook_id, csv_path=csv_path
                    )
                    logger.info(f"Auto-analysis generation result: {auto_analysis is not None}")
                    if auto_analysis:
                        logger.info(f"Auto-analysis type: {type(auto_analysis)}")
                        logger.info(f"Auto-analysis has summary_lines: {hasattr(auto_analysis, 'summary_lines')}")
                        if hasattr(auto_analysis, 'summary_lines'):
                            logger.info(f"Summary lines count: {len(auto_analysis.summary_lines) if auto_analysis.summary_lines else 0}")
                except Exception as e:
                    logger.error(f"ERROR: Error generating auto-analysis: {e}")
                    logger.error(f"Exception type: {type(e).__name__}")
                    auto_analysis = None
            else:
                logger.info(f"ERROR: Skipping auto-analysis: selected_chart={bool(request.selected_chart)}, workbook_data={workbook_data is not None}")
                auto_analysis = None
            
            # Step 3: Process based on intent and available data
            # OPTIMIZATION: Skip intent analysis and visualization if auto-analysis was already generated
            # Auto-analysis is a complete response that doesn't need additional processing
            if auto_analysis:
                logger.info("[AUTO_ANALYSIS] Auto-analysis generated, skipping intent analysis and visualization")
                analysis_result = None
                visualization = None
            elif workbook_data and request.message.strip():
                # User asked a specific question with data available
                analysis_result = await self._execute_intent_analysis(
                    intent_result, request.message, workbook_data
                )
                
                # Step 4: Generate visualization if needed
                visualization = None
                if analysis_result and self._requires_visualization(intent_result):
                    visualization = await self._create_visualization_for_intent(
                        intent_result, analysis_result, workbook_data
                    )
            else:
                analysis_result = None
                visualization = None
            
            # Step 5: Generate enhanced response
            response_text = await self._generate_enhanced_response(
                request.message, intent_result, analysis_result, auto_analysis
            )
            
            execution_time = (datetime.now() - start_time).total_seconds()
            
            return {
                "success": True,
                "reply": response_text,
                "intent": intent_result,
                "auto_analysis": auto_analysis,
                "analysis_result": analysis_result,
                "visualization": visualization,
                "execution_time": execution_time,
                "requires_chart_selection": self._requires_chart_selection(intent_result, workbook_data),
                "suggested_actions": self._generate_suggested_actions(intent_result, workbook_data)
            }
            
        except Exception as e:
            logger.error(f"Error in enhanced query processing: {e}")
            return self._create_error_response(request.message, str(e))

    async def understand_query(self, query: str, context: Optional[Dict[str, Any]] = None) -> QueryIntent:
        """
        Enhanced query understanding that returns structured QueryIntent
        
        Args:
            query: User's natural language query
            context: Optional context about available data/columns
            
        Returns:
            QueryIntent object with classification and metadata
        """
        try:
            logger.info(f"Understanding query: '{query[:100]}...'")
            
            # Step 1: LLM-based intent classification
            llm_intent = await self._classify_intent_with_llm(query, context)
            
            # Step 2: Rule-based intent classification (fallback/validation)
            rule_intent = self._classify_intent_with_rules(query)
            
            # Step 3: Entity extraction
            entities = self._extract_entities(query, context)
            
            # Step 4: Combine and validate results
            final_intent = self._combine_intent_results(llm_intent, rule_intent)
            
            # Step 5: Generate agent routing recommendations
            required_agents = self._recommend_services(final_intent["intent"], entities)
            
            # Step 6: Analyze chart requirements
            chart_requirements = self._analyze_chart_requirements(final_intent["intent"], entities)
            
            # Create QueryIntent object
            intent = QueryIntent(
                primary_intent=IntentType(final_intent["intent"]),
                confidence=final_intent["confidence"],
                entities=entities,
                requires_agents=required_agents,
                chart_requirements=chart_requirements,
                metadata={
                    "llm_classification": llm_intent,
                    "rule_classification": rule_intent,
                    "complexity_score": self._calculate_complexity(query, entities),
                    "processing_time": datetime.now().isoformat()
                }
            )
            
            logger.info(f"Query understanding complete: {final_intent['intent']} (confidence: {final_intent['confidence']:.2f})")
            return intent
            
        except Exception as e:
            logger.error(f"Error in query understanding: {e}")
            return self._create_fallback_intent(query)

    async def _generate_auto_analysis_for_chart(self, chart_name: str, workbook_data: Dict[str, Any], intent: QueryIntent, workbook_id: Optional[str] = None, csv_path: Optional[str] = None) -> AutoAnalysisResult:
        """Generate auto-analysis when user selects a chart"""
        
        try:
            logger.info(f"=== _generate_auto_analysis_for_chart called ===")
            logger.info(f"chart_name: {chart_name}")
            logger.info(f"workbook_data keys: {list(workbook_data.keys())}")
            logger.info(f"intent: {intent}")
            if workbook_id:
                logger.info(f"[AUTO_ANALYSIS] workbook_id: {workbook_id}")
            if csv_path:
                logger.info(f"[AUTO_ANALYSIS] csv_path override provided: {csv_path}")
            
            # Find the selected worksheet data
            worksheet_data = None
            for ws_name, ws_data in workbook_data.items():
                logger.info(f"Checking worksheet: {ws_name} (data shape: {ws_data.shape if hasattr(ws_data, 'shape') else 'not a DataFrame'})")
                if chart_name in ws_name or ws_name in chart_name:
                    worksheet_data = ws_data
                    logger.info(f"SUCCESS: Found matching worksheet: {ws_name}")
                    break
            
            if worksheet_data is None:
                logger.info("ERROR: No exact match found, using first available worksheet")
                worksheet_data = list(workbook_data.values())[0]
                original_chart_name = chart_name
                chart_name = list(workbook_data.keys())[0]
                logger.info(f"Using fallback: {original_chart_name} → {chart_name}")
            
            logger.info(f"Final worksheet_data shape: {worksheet_data.shape if hasattr(worksheet_data, 'shape') else 'not a DataFrame'}")
            
            # Get full CSV dataset for impact analysis - use the request CSV path (multi-user safe)
            full_dataset = None
            try:
                import os
                import pandas as pd
                if csv_path and os.path.exists(csv_path):
                    full_dataset = pd.read_csv(csv_path)
                    logger.info(f"SUCCESS: Loaded full CSV dataset from request path: shape {full_dataset.shape}, columns: {len(full_dataset.columns)}")
                    logger.info(f"CSV columns preview: {list(full_dataset.columns[:10])}")
                else:
                    logger.warning(f"ERROR: CSV path not provided or not found: {csv_path}")
                    logger.info("Will attempt to use worksheet data as fallback")
            except Exception as e:
                logger.warning(f"ERROR: Failed reading CSV path '{csv_path}': {e}")
                logger.info("Will attempt to use worksheet data as fallback")
            
            # Clean Tableau column names and map to CSV columns
            def _clean_tableau_column_name_local(tableau_col: str) -> str:
                """Clean Tableau column name by removing prefixes and converting to lowercase"""
                cleaned = tableau_col.lower()
                
                # Remove common Tableau prefixes (same logic as csv_data_loader.py)
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
                
                cleaned = cleaned.replace(' ', '_')
                return cleaned.strip()
            
            # Extract and clean Tableau column names
            original_tableau_columns = list(worksheet_data.columns)
            cleaned_tableau_columns = [_clean_tableau_column_name_local(col) for col in original_tableau_columns]
            
            logger.info(f"Original Tableau columns: {original_tableau_columns}")
            logger.info(f"Cleaned Tableau columns: {cleaned_tableau_columns}")
            
            # Store original Tableau data ONLY for Y-axis statistical analysis
            original_tableau_data = worksheet_data.copy() if worksheet_data is not None else None
            
            # Map to actual CSV columns if full dataset is available
            chart_data = worksheet_data  # Default fallback
            if full_dataset is not None:
                csv_columns = list(full_dataset.columns)
                mapped_csv_columns = []
                
                logger.info(f"[AUTO_ANALYSIS_MAPPING] Starting fuzzy column mapping for {len(cleaned_tableau_columns)} Tableau columns")
                
                for tableau_col, cleaned_col in zip(original_tableau_columns, cleaned_tableau_columns):
                    # Use fuzzy matcher for robust column matching
                    matched_csv_col = self.fuzzy_matcher.find_best_match(
                        cleaned_col,
                        csv_columns,
                        context=f"auto_analysis|{chart_name}"
                    )
                    
                    if matched_csv_col:
                        mapped_csv_columns.append(matched_csv_col)
                        logger.info(f"[AUTO_ANALYSIS_MAPPING] ✓ Mapped Tableau column '{tableau_col}' → CSV column '{matched_csv_col}'")
                        
                        # Log type inference
                        col_dtype = full_dataset[matched_csv_col].dtype
                        logger.info(f"[TYPE_INFERENCE] Column '{matched_csv_col}' type: {col_dtype}")
                    else:
                        logger.warning(f"[AUTO_ANALYSIS_MAPPING] ✗ No match found for Tableau column '{tableau_col}' (cleaned: '{cleaned_col}')")
                
                # Use mapped CSV data if successful
                if mapped_csv_columns:
                    chart_data = full_dataset[mapped_csv_columns]
                    logger.info(f"SUCCESS: Using mapped CSV columns: {mapped_csv_columns}")
                    logger.info(f"Chart data shape after mapping: {chart_data.shape}")
                else:
                    logger.warning("ERROR: No CSV mapping found, using original worksheet data")
            else:
                logger.info("No CSV data available, using original worksheet data")
            
            # Generate simplified auto-analysis showing chart columns and most impactful columns
            logger.info("Generating simplified auto-analysis with chart columns and most impactful")
            
            # Generate chart statistics from original Tableau data and add to display
            # Get original chart data from global app storage
            original_chart_data = None
            try:
                import app
                logger.info(f"DEBUG: Checking for original chart data...")
                logger.info(f"DEBUG: hasattr(app, 'original_chart_data'): {hasattr(app, 'original_chart_data')}")
                logger.info(f"DEBUG: hasattr(app, 'original_tableau_columns'): {hasattr(app, 'original_tableau_columns')}")
                
                if hasattr(app, 'original_chart_data') and hasattr(app, 'original_tableau_columns'):
                    logger.info(f"DEBUG: Available charts in app.original_chart_data: {list(app.original_chart_data.keys())}")
                    logger.info(f"DEBUG: Available charts in app.original_tableau_columns: {list(app.original_tableau_columns.keys())}")
                    
                    if chart_name in app.original_chart_data:
                        original_chart_data = app.original_chart_data[chart_name]
                        logger.info(f"DEBUG: Found original chart data for {chart_name}")
                        logger.info(f"DEBUG: Original chart columns: {list(original_chart_data.columns)}")
                    else:
                        logger.warning(f"DEBUG: Chart '{chart_name}' not found in original_chart_data")
                else:
                    logger.warning(f"DEBUG: app.original_chart_data or app.original_tableau_columns not available")
            except Exception as e:
                logger.warning(f"DEBUG: Error accessing original chart data: {e}")
            
            chart_stats = self._generate_chart_statistics(original_chart_data) if original_chart_data is not None else None
            chart_stats_text = self._format_chart_statistics(chart_stats) if chart_stats else ""
            
            # Create simple result with chart columns + chart statistics
            chart_columns = list(set(chart_data.columns.tolist()))
            
            # Get top 2 most impactful columns from cache or calculate
            most_impactful_columns = self._get_top_2_impactful_columns(chart_name, chart_columns, workbook_id=workbook_id, csv_path_override=csv_path)
            
            summary_text = f"""**Chart Columns:** {', '.join(chart_columns)}

**Most Impactful Columns:** {most_impactful_columns}

{chart_stats_text}"""
            
            auto_analysis = AutoAnalysisResult(
                summary_lines=[summary_text],
                statistical_analysis={},
                data_aggregation={},
                most_impactful_columns=[]
            )
            
            logger.info(f"SUCCESS: Generated auto-analysis for chart: {chart_name}")
            logger.info(f"Auto-analysis result type: {type(auto_analysis)}")
            if auto_analysis and hasattr(auto_analysis, 'summary_lines'):
                logger.info(f"Summary lines: {auto_analysis.summary_lines}")
            
            return auto_analysis
            
        except Exception as e:
            logger.error(f"ERROR: Error generating auto-analysis: {e}")
            logger.error(f"Exception details: {type(e).__name__}: {str(e)}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            return self._create_fallback_auto_analysis(chart_name)

    def _calculate_categorical_correlation(self, dataset, col1, col2):
        """Calculate correlation between categorical/mixed columns using Cramér's V or point-biserial"""
        import pandas as pd
        import numpy as np
        from scipy.stats import chi2_contingency
        
        try:
            # Handle missing values
            data_clean = dataset[[col1, col2]].dropna()
            if len(data_clean) < 2:
                return 0.0
            
            # Check if both are numeric
            if pd.api.types.is_numeric_dtype(data_clean[col1]) and pd.api.types.is_numeric_dtype(data_clean[col2]):
                return abs(data_clean[col1].corr(data_clean[col2]))
            
            # For categorical-categorical or categorical-numeric, use Cramér's V
            contingency_table = pd.crosstab(data_clean[col1], data_clean[col2])
            
            # Chi-square test
            chi2, p_value, dof, expected = chi2_contingency(contingency_table)
            
            # Cramér's V calculation
            n = contingency_table.sum().sum()
            cramers_v = np.sqrt(chi2 / (n * (min(contingency_table.shape) - 1)))
            
            return min(cramers_v, 1.0)  # Cap at 1.0
            
        except Exception as e:
            logger.warning(f"Error calculating correlation between {col1} and {col2}: {e}")
            return 0.0

    async def _detect_xy_axes_with_llm_and_data(self, chart_data):
        """Use OpenAI with actual chart data to accurately detect X and Y axes"""
        import pandas as pd
        import json
        
        try:
            # Prepare comprehensive data context for OpenAI
            columns_info = []
            for col in chart_data.columns:
                col_info = {
                    'name': col,
                    'type': str(chart_data[col].dtype),
                    'unique_count': int(chart_data[col].nunique()),
                    'null_count': int(chart_data[col].isnull().sum()),
                    'sample_values': chart_data[col].dropna().head(5).tolist()
                }
                
                # Add statistical info for numeric columns
                if pd.api.types.is_numeric_dtype(chart_data[col]):
                    col_info.update({
                        'min': float(chart_data[col].min()),
                        'max': float(chart_data[col].max()),
                        'mean': float(chart_data[col].mean())
                    })
                
                # Add categorical info for object columns
                elif chart_data[col].dtype == 'object':
                    value_counts = chart_data[col].value_counts().head(3)
                    col_info['top_categories'] = value_counts.to_dict()
                
                columns_info.append(col_info)
            
            # Create rich prompt with actual data context
            system_prompt = """You are a data visualization expert. Given detailed information about chart columns including data types, sample values, and statistics, identify which column should be the X-axis (independent variable) and which should be the Y-axis (dependent variable to analyze).

            X-axis typically: time/dates, categories, independent variables, dimensions
            Y-axis typically: metrics, counts, amounts, KPIs, dependent variables to analyze

            Consider:
            - Temporal columns (dates/times) usually go on X-axis
            - Metrics/measurements usually go on Y-axis  
            - High-cardinality numeric columns often indicate IDs or metrics
            - Low-cardinality columns often indicate categories/dimensions

            Return JSON: {"x_axis": "column_name", "y_axis": "column_name", "reasoning": "detailed explanation", "confidence": 0.0-1.0}"""
            
            user_prompt = f"""Chart Data Analysis:

            **Data Shape**: {chart_data.shape[0]} rows × {chart_data.shape[1]} columns

            **Column Details**:
            {json.dumps(columns_info, indent=2, default=str)}

            **Sample Data Rows**:
            {json.dumps(chart_data.head(3).fillna('').to_dict('records'), indent=2, default=str)}

            Based on this comprehensive data analysis, identify the most appropriate X-axis and Y-axis columns for causal analysis."""
            
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ]
            
            logger.info("Sending chart data to OpenAI for X/Y detection: {} columns, {} rows".format(len(columns_info), len(chart_data)))
            
            response = await self.llm_service._make_request(messages, max_tokens=600, temperature=0.1)
            
            # Handle empty or invalid responses
            if not response or not response.strip():
                logger.warning("OpenAI returned empty response for X/Y detection")
                return None
            
            # Strip markdown formatting if present
            response_clean = response.strip()
            if response_clean.startswith('```json'):
                response_clean = response_clean[7:]  # Remove ```json
            if response_clean.endswith('```'):
                response_clean = response_clean[:-3]  # Remove ```
            response_clean = response_clean.strip()
            
            try:
                result = json.loads(response_clean)
            except json.JSONDecodeError as e:
                logger.warning("OpenAI returned invalid JSON: {} - Response: '{}'".format(e, response[:200]))
                logger.info("Cleaned response attempt: '{}'".format(response_clean[:200]))
                return None
            
            # Validate result structure
            if not isinstance(result, dict) or not result.get('x_axis') or not result.get('y_axis'):
                logger.warning("OpenAI returned invalid result structure: {}".format(result))
                return None
            
            logger.info("OpenAI X/Y detection result: X={}, Y={}, confidence={}".format(
                result.get('x_axis'), result.get('y_axis'), result.get('confidence', 0)))
            logger.info("OpenAI reasoning: {}".format(result.get('reasoning', 'No reasoning provided')))
            
            return result
            
        except Exception as e:
            logger.warning("LLM X/Y detection failed: {}, using heuristics".format(e))
            return self._detect_xy_axes_heuristic(chart_data)

    def _detect_xy_axes_heuristic(self, chart_data):
        """Fallback heuristic method for X/Y axis detection"""
        import pandas as pd
        
        columns = chart_data.columns.tolist()
        logger.info("Heuristic X/Y detection starting with columns: {}".format(columns))
        
        # Simple heuristics
        x_axis = None
        y_axis = None
        
        # Look for time/date columns for X-axis
        for col in columns:
            col_lower = col.lower()
            if any(keyword in col_lower for keyword in ['date', 'time', 'month', 'year', 'day']):
                x_axis = col
                logger.info("Found time/date column for X-axis: {}".format(col))
                break
        
        # Look for numeric/count columns for Y-axis
        for col in columns:
            if pd.api.types.is_numeric_dtype(chart_data[col]):
                col_lower = col.lower()
                if any(keyword in col_lower for keyword in ['count', 'total', 'amount', 'volume', 'revenue']):
                    y_axis = col
                    logger.info("Found numeric/count column for Y-axis: {}".format(col))
                    break
        
        # Special case: Look for ID columns that represent counts (common in line charts)
        if not y_axis:
            for col in columns:
                col_lower = col.lower()
                if any(keyword in col_lower for keyword in ['id', '_id', 'case', 'ticket', 'order', 'transaction']):
                    y_axis = col
                    logger.info("Found ID/count column for Y-axis (line chart pattern): {}".format(col))
                    break
        
        # Fallback: first categorical for X, first numeric for Y
        if not x_axis:
            for col in columns:
                if chart_data[col].dtype == 'object':
                    x_axis = col
                    logger.info("Fallback: Found categorical column for X-axis: {}".format(col))
                    break
        
        if not y_axis:
            for col in columns:
                if pd.api.types.is_numeric_dtype(chart_data[col]):
                    y_axis = col
                    logger.info("Fallback: Found numeric column for Y-axis: {}".format(col))
                    break
        
        # Final fallback - ensure axes are different
        if not y_axis and columns:
            y_axis = columns[0]
            logger.info("Final fallback: Y-axis set to first column: {}".format(y_axis))
        if not x_axis and len(columns) > 1:
            x_axis = columns[1] if columns[1] != y_axis else columns[0]
            logger.info("Final fallback: X-axis set to: {}".format(x_axis))
        
        # Critical fix: If both axes are the same, try to differentiate intelligently
        if x_axis == y_axis and len(columns) > 1:
            logger.warning("Both axes are the same ({}), attempting to differentiate".format(x_axis))
            
            # Check if the shared axis is a time/date column
            shared_col_lower = x_axis.lower() if x_axis else ""
            is_time_column = any(keyword in shared_col_lower for keyword in ['date', 'time', 'month', 'year', 'day'])
            
            if is_time_column:
                # If shared column is time-based, keep it as X-axis and change Y-axis
                for col in columns:
                    if col != x_axis:
                        y_axis = col
                        logger.info("Differentiated: Kept time column '{}' as X-axis, changed Y-axis to '{}'".format(x_axis, y_axis))
                        break
            else:
                # If shared column is not time-based, change X-axis
                for col in columns:
                    if col != y_axis:
                        x_axis = col
                        logger.info("Differentiated: Y-axis kept as '{}', changed X-axis to '{}'".format(y_axis, x_axis))
                        break
            
            # If still the same (only 1 column), keep them the same but log warning
            if x_axis == y_axis:
                logger.warning("Only one column available - both X and Y axes set to: {}".format(x_axis))
        
        logger.info("Heuristic result: X-axis={}, Y-axis={}".format(x_axis, y_axis))
        
        return {
            'x_axis': x_axis,
            'y_axis': y_axis,
            'reasoning': 'Heuristic fallback detection',
            'confidence': 0.5
        }

    def _get_csv_path(self):
        """Get CSV file path from CSVDataLoader with enhanced detection"""
        import os
        import glob
        
        try:
            # Method 1: Try CSVDataLoader
            logger.info("Method 1: Trying CSVDataLoader")
            from services.csv_data_loader import CSVDataLoader
            csv_loader = CSVDataLoader()
            if csv_loader.csv_file_path and os.path.exists(csv_loader.csv_file_path):
                logger.info("Found CSV file path via CSVDataLoader: {}".format(csv_loader.csv_file_path))
                return csv_loader.csv_file_path
            else:
                logger.warning("CSVDataLoader path invalid or missing: {}".format(csv_loader.csv_file_path))
        except Exception as e:
            logger.error("CSVDataLoader failed: {}".format(e))
        
        # Method 2: Check for CSV files in project root
        logger.info("Method 2: Searching project root for CSV files")
        try:
            csv_files = glob.glob("*.csv")
            if csv_files:
                csv_path = csv_files[0]
                logger.info("Found CSV file in project root: {}".format(csv_path))
                return os.path.abspath(csv_path)
        except Exception as e:
            logger.error("Error searching for CSV files: {}".format(e))
        
        # Method 3: Check common CSV file names
        logger.info("Method 3: Checking common CSV file names")
        common_names = ["data.csv", "dataset.csv", "tickets.csv", "sample.csv"]
        for name in common_names:
            if os.path.exists(name):
                logger.info("Found common CSV file: {}".format(name))
                return os.path.abspath(name)
        
        logger.error("No CSV file found using any method")
        logger.info("Available files in current directory: {}".format(os.listdir('.')))
        return None

    async def _run_statistical_fallback_analysis(self, csv_path, y_column, x_axis_column, chart_columns):
        """Statistical fallback analysis that mimics causal analysis output format"""
        import pandas as pd
        import numpy as np
        from scipy.stats import spearmanr, chi2_contingency
        
        try:
            logger.info("Loading CSV data for statistical analysis")
            df = pd.read_csv(csv_path)
            logger.info("CSV loaded: {} rows, {} columns".format(len(df), len(df.columns)))
            
            # Get all categorical features excluding chart columns and target
            excluded_cols = set(chart_columns + [y_column] if y_column else chart_columns)
            if x_axis_column:
                excluded_cols.add(x_axis_column)
            
            # Find categorical features
            categorical_features = []
            for col in df.columns:
                if col not in excluded_cols:
                    if (df[col].dtype == 'object' or 
                        (pd.api.types.is_numeric_dtype(df[col]) and df[col].nunique() <= 50)):
                        unique_ratio = df[col].nunique() / len(df)
                        if 0.005 <= unique_ratio <= 0.8 and df[col].nunique() >= 2:
                            categorical_features.append(col)
            
            logger.info("Found {} categorical features for analysis".format(len(categorical_features)))
            
            # Calculate feature importance scores
            feature_scores = {}
            for feature in categorical_features[:15]:  # Limit to top 15 for speed
                try:
                    score = self._calculate_feature_importance_score(df, feature, y_column)
                    if score > 0:
                        feature_scores[feature] = score
                except Exception as e:
                    logger.warning("Error calculating score for {}: {}".format(feature, e))
            
            # Sort features by score and take top 5
            sorted_features = sorted(feature_scores.items(), key=lambda x: x[1], reverse=True)[:5]
            
            # Create mock domain detection with LLM
            domain_type = await self._detect_domain_with_llm(categorical_features[:10], y_column)
            
            # Create top_drivers format that matches causal analysis
            top_drivers = []
            for i, (feature, score) in enumerate(sorted_features):
                reasoning = await self._generate_feature_reasoning(feature, y_column, score, domain_type)
                top_drivers.append((feature, {
                    'llm_reasoning': reasoning,
                    'combined_score': score,
                    'statistical_method': 'correlation_analysis'
                }))
            
            top_5_features = [feature for feature, _ in top_drivers]
            
            logger.info("Statistical analysis complete - found {} impactful features".format(len(top_5_features)))
            return top_5_features, domain_type, top_drivers
            
        except Exception as e:
            logger.error("Statistical fallback failed: {}".format(e))
            # Ultimate fallback - return something reasonable
            return ['statistical_analysis_unavailable'], 'General Analytics', [
                ('statistical_analysis_unavailable', {
                    'llm_reasoning': 'Statistical analysis encountered technical issues',
                    'combined_score': 0.5,
                    'statistical_method': 'fallback'
                })
            ]

    def _calculate_feature_importance_score(self, df, feature, y_column):
        """Calculate a simple importance score using correlation/association"""
        import pandas as pd
        import numpy as np
        from scipy.stats import spearmanr
        
        try:
            if not y_column or y_column not in df.columns:
                return 0.0
            
            # For numerical Y, use correlation
            if pd.api.types.is_numeric_dtype(df[y_column]):
                if pd.api.types.is_numeric_dtype(df[feature]):
                    # Numeric-Numeric: Spearman correlation
                    corr, p_val = spearmanr(df[feature].dropna(), df[y_column].dropna())
                    return abs(corr) if not np.isnan(corr) else 0.0
                else:
                    # Categorical-Numeric: ANOVA-style
                    groups = df.groupby(feature)[y_column].mean()
                    if len(groups) > 1:
                        return groups.std() / (df[y_column].std() + 1e-10)
            else:
                # Categorical Y: use chi-square association
                try:
                    from scipy.stats import chi2_contingency
                    contingency = pd.crosstab(df[feature], df[y_column])
                    chi2, p_val, dof, expected = chi2_contingency(contingency)
                    # Normalized chi-square
                    n = contingency.sum().sum()
                    return np.sqrt(chi2 / (n * (min(contingency.shape) - 1)))
                except:
                    return 0.0
            
            return 0.0
            
        except Exception as e:
            logger.warning("Error in feature importance calculation: {}".format(e))
            return 0.0

    async def _detect_domain_with_llm(self, features, y_column):
        """Use LLM to detect business domain from features"""
        try:
            system_prompt = "You are a business analyst. Given these column names, identify the business domain in 2-3 words."
            
            user_prompt = "Target: {}\nFeatures: {}\nWhat business domain is this?".format(
                y_column, ', '.join(features[:10]))
            
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ]
            
            response = await self.llm_service._make_request(messages, max_tokens=50, temperature=0.3)
            domain = response.strip().replace('"', '').replace('.', '')
            return domain if len(domain) < 50 else "Business Analytics"
            
        except Exception as e:
            logger.warning("Domain detection failed: {}".format(e))
            return "Business Analytics"

    async def _generate_feature_reasoning(self, feature, y_column, score, domain_type):
        """Generate LLM reasoning for why a feature is impactful"""
        try:
            system_prompt = "You are a business analyst. Explain in 8-12 words why this feature impacts the target variable."
            
            user_prompt = "Domain: {}\nFeature: {}\nTarget: {}\nScore: {:.2f}\nWhy is this impactful?".format(
                domain_type, feature, y_column, score)
            
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ]
            
            response = await self.llm_service._make_request(messages, max_tokens=100, temperature=0.3)
            reasoning = response.strip().replace('"', '')
            return reasoning if len(reasoning) < 100 else "Statistically significant relationship with target variable"
            
        except Exception as e:
            logger.warning("Reasoning generation failed: {}".format(e))
            return "Statistically significant relationship with target variable"

    def _check_causal_cache(self, chart_name, workbook_id: Optional[str] = None):
        """Check if causal analysis results are cached (nested: cache[workbook_id][chart_name])"""
        import json
        import os
        from datetime import datetime
        
        cache_file = "causal_analysis_cache.json"
        
        if not os.path.exists(cache_file):
            return None
        
        try:
            with open(cache_file, 'r') as f:
                cache_data = json.load(f)
            
            # Nested lookup first
            candidate = None
            candidate_age = None
            if workbook_id and workbook_id in cache_data and chart_name in cache_data[workbook_id]:
                candidate = cache_data[workbook_id][chart_name]
            else:
                # Scan all workbooks for the chart; pick the freshest
                for wb_id, charts in cache_data.items():
                    if isinstance(charts, dict) and chart_name in charts:
                        entry = charts[chart_name]
                        ts_str = entry.get('timestamp', '2000-01-01T00:00:00Z')
                        try:
                            ts = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
                        except Exception:
                            ts = None
                        if candidate is None or (ts and candidate_age and ts > candidate_age) or (ts and candidate_age is None):
                            candidate = entry
                            candidate_age = ts
            
            if candidate:
                # Validate age within 24 hours
                ts_str = candidate.get('timestamp', '2000-01-01T00:00:00Z')
                try:
                    ts = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
                    age_hours = (datetime.now().replace(tzinfo=ts.tzinfo) - ts).total_seconds() / 3600
                except Exception:
                    age_hours = 0.0
                if age_hours < 24:
                    logger.info("Found valid cached result for chart: {} (age: {:.1f} hours)".format(chart_name, age_hours))
                    return candidate
                logger.info("Cached result for chart {} is too old ({:.1f} hours), will regenerate".format(chart_name, age_hours))
        
        except Exception as e:
            logger.error("Error reading cache: {}".format(e))
        
        return None

    def _cache_causal_results(self, chart_name, top_5_features, domain_type, csv_path, x_axis, y_axis, xy_detection, top_drivers, workbook_id: Optional[str] = None):
        """Cache causal analysis results to JSON file (nested: cache[workbook_id][chart_name])"""
        import json
        import os
        from datetime import datetime
        
        cache_file = "causal_analysis_cache.json"
        
        try:
            logger.info("Starting cache operation for chart: {}".format(chart_name))
            logger.info("Cache file path: {}".format(os.path.abspath(cache_file)))
            logger.info("Current working directory: {}".format(os.getcwd()))
            
            # Load existing cache or create new
            cache_data = {}
            if os.path.exists(cache_file):
                try:
                    with open(cache_file, 'r') as f:
                        cache_data = json.load(f)
                    logger.info("Loaded existing cache with {} entries".format(len(cache_data)))
                except Exception as e:
                    logger.error("Error reading existing cache: {}".format(e))
                    cache_data = {}
            else:
                logger.info("No existing cache file, creating new one")
            
            # Prepare feature details
            logger.info("Preparing feature details for {} drivers".format(len(top_drivers)))
            feature_details = []
            for i, (feature, data) in enumerate(top_drivers[:5]):
                detail = {
                    'feature': feature,
                    'llm_reasoning': data.get('llm_reasoning', 'Statistical selection'),
                    'combined_score': data.get('combined_score', 0.0),
                    'rank': i + 1
                }
                feature_details.append(detail)
                logger.info("Feature {}: {} (score: {:.3f})".format(i+1, feature, detail['combined_score']))
            
            # Create cache entry
            cache_entry = {
                'top_5_features': top_5_features,
                'domain_type': domain_type,
                'x_axis_detected': x_axis,
                'y_axis_detected': y_axis,
                'xy_detection_confidence': xy_detection.get('confidence', 0.0),
                'xy_detection_reasoning': xy_detection.get('reasoning', ''),
                'feature_details': feature_details,
                'timestamp': datetime.now().isoformat() + 'Z',
                'csv_file_used': os.path.basename(csv_path) if csv_path else ''
            }
            
            logger.info("Cache entry created with keys: {}".format(list(cache_entry.keys())))
            # Enforce nested structure
            if not workbook_id:
                logger.warning(f"[CACHE WRITE] workbook_id not provided; skipping cache write for chart '{chart_name}' to enforce nested structure")
            else:
                if not isinstance(cache_data, dict):
                    cache_data = {}
                if workbook_id not in cache_data:
                    cache_data[workbook_id] = {}
                cache_data[workbook_id][chart_name] = cache_entry
                
                # Write cache file
                logger.info("Writing cache to file: {}".format(cache_file))
                with open(cache_file, 'w') as f:
                    json.dump(cache_data, f, indent=2)
            
            # Verify file was written
            if workbook_id:
                if os.path.exists(cache_file):
                    file_size = os.path.getsize(cache_file)
                    logger.info("Cache file written successfully. Size: {} bytes".format(file_size))
                else:
                    logger.error("Cache file was not created!")
                
        except Exception as e:
            logger.error("Error in cache operation: {}".format(e))
            import traceback
            logger.error("Cache traceback: {}".format(traceback.format_exc()))

    def _strip_tableau_prefixes(self, column_name):
        """Strip common Tableau prefixes from column names"""
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
        
        cleaned = column_name.lower()
        for prefix in prefixes_to_remove:
            if cleaned.startswith(prefix):
                cleaned = cleaned[len(prefix):]
                break
        
        # Convert back to proper case
        return cleaned.replace('_', ' ').title()
    
    def _identify_x_axis_column(self, chart_data):
        """Identify the X-axis (time/month) column from chart data"""
        time_indicators = ['month', 'date', 'time', 'year', 'day', 'week']
        
        for col in chart_data.columns:
            col_lower = col.lower()
            if any(indicator in col_lower for indicator in time_indicators):
                return col
        
        # Fallback: return first column if no time column found
        return chart_data.columns[0] if len(chart_data.columns) > 0 else None
    
    def _calculate_column_statistics(self, chart_data, x_col, y_col, clean_name):
        """Calculate statistics for a single Y-column from chart data"""
        import pandas as pd
        import numpy as np
        
        # Get Y-axis values (already aggregated by Tableau)
        y_values = chart_data[y_col].dropna()
        x_values = chart_data[x_col].dropna()
        
        if len(y_values) < 2:
            return None
        
        perc_flag=False
        # Clean comma-separated numbers and percent signs before numeric check
        if y_values.dtype == 'object':
            # Try to convert comma-separated numbers to float
            try:
                y_values_cleaned = y_values.astype(str).str.replace(',', '').astype(float)
                logger.info(f"DEBUG: Successfully cleaned comma-separated numbers for {clean_name}")
                y_values = y_values_cleaned  # Use cleaned values
                
            except:
                logger.info(f"DEBUG: Could not clean comma-separated numbers for {clean_name}")
            # Try to remove percent signs
            try:
                y_values_cleaned = y_values.astype(str).str.replace('%', '').astype(float)
                logger.info(f"DEBUG: Successfully cleaned percent signs for {clean_name}")
                y_values = y_values_cleaned  # Use cleaned values
                perc_flag=True # If we successfully cleaned percent signs, set perc_flag to True
            except:
                logger.info(f"DEBUG: Could not clean percent signs for {clean_name}")
        print (y_values)
        print (perc_flag)
        logger.info(f"INFO: y_values from _calculate_column_statistics: {y_values}")

        # Check if Y-column is numeric (after cleaning)
        if not pd.api.types.is_numeric_dtype(y_values):
            # For non-numeric columns, skip statistical analysis
            return {
                'column_name': clean_name,
                'original_column': y_col,
                'x_column': x_col,
                'analysis_type': 'non_numeric',
                'data_type': str(y_values.dtype),
                'unique_values': len(y_values.unique()),
                'sample_values': list(y_values.unique()[:3]),
                'note': f'Column contains non-numeric data ({y_values.dtype}), statistical analysis not applicable'
            }
        
        # Calculate basic statistics
        try:
            min_val = float(y_values.min())
            max_val = float(y_values.max())
            median_val = float(y_values.median())
            percentile_25 = float(y_values.quantile(0.25))
            percentile_75 = float(y_values.quantile(0.75))
        except (ValueError, TypeError) as e:
            # Fallback for any conversion issues
            return {
                'column_name': clean_name,
                'original_column': y_col,
                'x_column': x_col,
                'analysis_type': 'conversion_error',
                'error': str(e),
                'note': f'Unable to perform numerical analysis on column {clean_name}'
            }
        
        # Find months for each statistic
        min_month = x_values[y_values.idxmin()]
        max_month = x_values[y_values.idxmax()]
        
        # Find months closest to percentile values
        median_month = x_values[y_values.sub(median_val).abs().idxmin()]
        percentile_25_month = x_values[y_values.sub(percentile_25).abs().idxmin()]
        percentile_75_month = x_values[y_values.sub(percentile_75).abs().idxmin()]

        if not perc_flag:
            stats = {
                'column_name': clean_name,
                'original_column': y_col,
                'x_column': x_col,
                'analysis_type': 'chart_data',
                'total_periods': len(y_values),
                'min': round(min_val, 2),
                'max': round(max_val, 2),
                'median': round(median_val, 2),
                '25th_percentile': round(percentile_25, 2),
                '75th_percentile': round(percentile_75, 2),
                'min_month': str(min_month),
                'max_month': str(max_month),
                'median_month': str(median_month),
                '25th_percentile_month': str(percentile_25_month),
                '75th_percentile_month': str(percentile_75_month)
            }
        else:
            stats = {
                'column_name': clean_name,
                'original_column': y_col,
                'x_column': x_col,
                'analysis_type': 'chart_data',
                'total_periods': len(y_values),
                'min': str(round(min_val, 2))+"%",
                'max': str(round(max_val, 2))+"%",
                'median': str(round(median_val, 2))+"%",
                '25th_percentile': str(round(percentile_25, 2))+"%",
                '75th_percentile': str(round(percentile_75, 2))+"%",
                'min_month': str(min_month),
                'max_month': str(max_month),
                'median_month': str(median_month),
                '25th_percentile_month': str(percentile_25_month),
                '75th_percentile_month': str(percentile_75_month)
            }
        
        # Simple anomaly detection on chart values
        if len(y_values) >= 3:
            Q1 = y_values.quantile(0.25)
            Q3 = y_values.quantile(0.75)
            IQR = Q3 - Q1
            
            lower_bound = Q1 - 1.5 * IQR
            upper_bound = Q3 + 1.5 * IQR
            
            anomalies = []
            for idx, value in y_values.items():
                if value < lower_bound or value > upper_bound:
                    month_value = x_values.iloc[idx] if idx < len(x_values) else x_values.iloc[0]
                    anomalies.append({
                        'period': str(month_value),
                        'value': round(float(value), 2),
                        'type': 'high' if value > upper_bound else 'low'
                    })
            
            # Sort by deviation from median and take top 3
            median_val = y_values.median()
            anomalies.sort(key=lambda x: abs(x['value'] - median_val), reverse=True)
            stats['anomalies'] = anomalies[:3]
        
        return stats
    
    def _generate_chart_statistics(self, chart_data):
        """Generate statistics for ALL Y-axis columns directly from original Tableau chart data"""
        import pandas as pd
        import numpy as np
        
        if chart_data is None or chart_data.empty:
            return None
        
        # 1. Identify X-axis column (month/time column)
        x_col = self._identify_x_axis_column(chart_data)
        if not x_col:
            return None
        
        # 2. Identify ALL Y-axis columns (everything except X-axis)
        y_columns = [col for col in chart_data.columns if col != x_col]
        
        if not y_columns:
            return None
        
        # 3. Process each Y-column
        all_stats = []
        for y_col in y_columns:
            if y_col == "Measure Names":
                continue
            # Strip Tableau prefixes
            clean_name = self._strip_tableau_prefixes(y_col)
            
            # Calculate statistics from chart data
            col_stats = self._calculate_column_statistics(chart_data, x_col, y_col, clean_name)
            if col_stats:
                all_stats.append(col_stats)
        
        return all_stats if all_stats else None
    
    def _format_month_for_display(self, month_value):
        """Format month value for display as YYYY-MM"""
        try:
            # Try to format as date if it has strftime method
            if hasattr(month_value, 'strftime'):
                return month_value.strftime('%Y-%m')
            # If it's already a simple string (like "December", "January"), return as-is
            elif isinstance(month_value, str):
                # Check if it's a date string like "2025-07-01"
                if '-' in month_value and len(month_value) >= 7:
                    return month_value[:7]  # Return "2025-07"
                else:
                    return str(month_value)  # Return month name as-is
            else:
                return str(month_value)
        except:
            return str(month_value)
    
    def _format_chart_statistics(self, stats_list):
        """Format chart statistics into readable text for multiple columns"""
        if not stats_list:
            return ""
        
        # Handle both single stats dict and list of stats
        if isinstance(stats_list, dict):
            stats_list = [stats_list]
        
        all_lines = []
        
        for stats in stats_list:
            if not stats:
                continue
                
            lines = [f"\n\n**Analysis of {stats['column_name']}:**"]
            
            # Handle non-numeric columns
            if stats.get('analysis_type') in ['non_numeric', 'conversion_error']:
                if stats.get('note'):
                    lines.append(f"• {stats['note']}")
                if stats.get('data_type'):
                    lines.append(f"• Data type: {stats['data_type']}")
                if stats.get('unique_values') is not None:
                    lines.append(f"• Unique values: {stats['unique_values']}")
                if stats.get('sample_values'):
                    sample_str = ', '.join(str(v) for v in stats['sample_values'])
                    lines.append(f"• Sample values: {sample_str}")
                all_lines.extend(lines)
                continue
            
            # Format month values for display (numeric columns only)
            min_month = self._format_month_for_display(stats['min_month'])
            max_month = self._format_month_for_display(stats['max_month'])
            median_month = self._format_month_for_display(stats['median_month'])
            p25_month = self._format_month_for_display(stats['25th_percentile_month'])
            p75_month = self._format_month_for_display(stats['75th_percentile_month'])
            
            # Clean format without "Chart values:" heading
            lines.append(f"• Min={stats['min']} (in {min_month})")
            lines.append(f"• Max={stats['max']} (in {max_month})")
            lines.append(f"• Median={stats['median']} (in {median_month})")
            lines.append(f"• 25th percentile={stats['25th_percentile']} (in {p25_month})")
            lines.append(f"• 75th percentile={stats['75th_percentile']} (in {p75_month})")
            
            if stats.get('anomalies'):
                lines.append("• Anomalous months:")
                for anomaly in stats['anomalies']:
                    formatted_period = self._format_month_for_display(anomaly['period'])
                    lines.append(f"  {formatted_period} ({anomaly['value']} - {anomaly['type']})")
            else:
                lines.append("• No significant anomalies detected")
            
            all_lines.extend(lines)
        
        return '\n'.join(all_lines)

    def _get_original_tableau_data_for_chart_statistics(self, chart_name: str, workbook_data: Dict[str, Any]) -> Optional[Any]:
        """
        Extract original Tableau data for chart statistics by checking if it's available
        in the raw workbook data before any CSV mapping occurs.
        """
        try:
            logger.info(f"Attempting to get original Tableau data for chart: {chart_name}")
            logger.info(f"DEBUG: workbook_data keys: {list(workbook_data.keys())}")
            logger.info(f"DEBUG: workbook_data type: {type(workbook_data)}")
            
            # Check in original_tableau_data for original Tableau column names (before processing)
            worksheets_data = workbook_data.get('original_tableau_data', {})
            logger.info(f"DEBUG: original_tableau_data type: {type(worksheets_data)}")
            logger.info(f"Available worksheets in original data: {list(worksheets_data.keys())}")
            
            if chart_name in worksheets_data:
                data = worksheets_data[chart_name]
                if hasattr(data, 'columns'):
                    # Check if this data has original Tableau column names (not CSV mapped)
                    columns = list(data.columns)
                    logger.info(f"Found original worksheets_data with columns: {columns}")
                    
                    # Look for Tableau-style column names (with prefixes like "Count of", "Month of")
                    has_tableau_names = any(
                        col.lower().startswith(('count of', 'distinct count of', 'sum of', 'average of', 'month of', 'year of'))
                        for col in columns
                    )
                    
                    if has_tableau_names:
                        logger.info(f"SUCCESS: Found original Tableau data for {chart_name}")
                        logger.info(f"Original columns: {columns}")
                        logger.info(f"Data shape: {data.shape}")
                        return data
                    else:
                        logger.info(f"Data found but appears to be mapped CSV data: {columns}")
                else:
                    logger.info(f"Data found but has no columns attribute: {type(data)}")
            else:
                logger.info(f"Chart '{chart_name}' not found in worksheets_data")
            
            logger.warning(f"No original Tableau data found for chart: {chart_name}")
            return None
            
        except Exception as e:
            logger.warning(f"Error getting original Tableau data for {chart_name}: {e}")
            return None

    def _create_cached_result(self, chart_name, chart_data, cache_result, worksheet_data=None):
        """Create AutoAnalysisResult from cached data"""
        chart_columns = list(set(chart_data.columns.tolist()))
        
        # Get cached features and create display format
        feature_details = cache_result.get('feature_details', [])
        if len(feature_details) >= 1:
            impactful_list = []
            for detail in feature_details[:2]:
                impactful_list.append(detail['feature'])
            
            # Generate chart statistics using original Tableau chart data
            # Get original Tableau data from workbook_data
            original_chart_data = None
            if hasattr(self, '_current_workbook_data') and self._current_workbook_data:
                original_chart_data = self._get_original_tableau_data_for_chart_statistics(chart_name, self._current_workbook_data)
            
            chart_stats = self._generate_chart_statistics(original_chart_data) if original_chart_data is not None else None
            chart_stats_text = self._format_chart_statistics(chart_stats) if chart_stats else ""
            
            summary_text = """**Chart Columns:** {}

**Most Impactful Columns:** {}{}""".format(', '.join(chart_columns), ', '.join(impactful_list), chart_stats_text)
        else:
            # Generate chart statistics even without impactful columns
            chart_stats = self._generate_chart_statistics(worksheet_data) if worksheet_data is not None else None
            chart_stats_text = self._format_chart_statistics(chart_stats) if chart_stats else ""
            
            summary_text = """**Chart Columns:** {}

**Most Impactful Columns:** No cached features available{}""".format(', '.join(chart_columns), chart_stats_text)
        
        return AutoAnalysisResult(
            summary_lines=[summary_text],
            statistical_analysis={},
            data_aggregation={},
            most_impactful_columns=[]
        )

    def _create_causal_result(self, chart_name, chart_columns, top_5_features, domain_type, top_drivers, worksheet_data=None):
        """Create AutoAnalysisResult from fresh causal analysis"""
        
        # Extract column names from top_drivers
        impactful_list = []
        for i, (feature, data) in enumerate(top_drivers[:2]):
            impactful_list.append(feature)
        
        # Generate chart statistics using original Tableau chart data
        chart_stats = self._generate_chart_statistics(worksheet_data) if worksheet_data is not None else None
        chart_stats_text = self._format_chart_statistics(chart_stats) if chart_stats else ""
        
        if len(impactful_list) >= 1:
            summary_text = """**Chart Columns:** {}

**Most Impactful Columns:** {}{}""".format(', '.join(chart_columns), ', '.join(impactful_list), chart_stats_text)
        else:
            summary_text = """**Chart Columns:** {}

**Most Impactful Columns:** No significant causal drivers found{}""".format(', '.join(chart_columns), chart_stats_text)
        
        return AutoAnalysisResult(
            summary_lines=[summary_text],
            statistical_analysis={},
            data_aggregation={},
            most_impactful_columns=[]
        )

    async def _generate_simplified_auto_analysis(self, chart_name: str, chart_data, full_dataset=None) -> AutoAnalysisResult:
        """Generate causal analysis using LLM-enhanced feature selection with data-aware X/Y detection"""
        import pandas as pd
        import numpy as np
        import json
        import os
        
        try:
            logger.info("=== CAUSAL ANALYSIS DIAGNOSTIC START ===")
            logger.info("Step 1: Starting causal analysis for chart: {}".format(chart_name))
            logger.info("Chart data shape: {}".format(chart_data.shape if hasattr(chart_data, 'shape') else 'not a DataFrame'))
            logger.info("Current working directory: {}".format(os.getcwd()))
            
            # Check cache FIRST for instant response
            logger.info("Step 2: Checking cache (priority check)")
            cache_result = self._check_causal_cache(chart_name)
            if cache_result:
                logger.info("SUCCESS: Using cached causal results for chart: {} (instant response)".format(chart_name))
                return self._create_cached_result(chart_name, chart_data, cache_result, worksheet_data)
            logger.info("No cache found, proceeding with fresh analysis")
            
            # Only check dependencies if cache miss
            logger.info("Step 3: Checking required dependencies")
            dependencies_available = True
            
            try:
                import econml
                logger.info("EconML package is available")
            except ImportError as e:
                logger.error("Missing EconML dependency: {}".format(e))
                logger.info("Install with: pip install econml")
                dependencies_available = False
            
            # Check shap separately since it has Windows/DLL issues
            try:
                import shap
                logger.info("SHAP package is available")
            except ImportError as e:
                logger.warning("SHAP package not available: {}".format(e))
                logger.info("SHAP is optional - proceeding without SHAP explainability")
            except Exception as e:
                logger.warning("SHAP package has issues (likely DLL problems): {}".format(e))
                logger.info("SHAP is optional - proceeding without SHAP explainability")
            
            if not dependencies_available:
                logger.error("Critical dependencies missing")
                return await self._generate_simplified_auto_analysis1(chart_name, chart_data, full_dataset)
            
            # Check OpenAI client
            logger.info("Step 4: Checking OpenAI client availability")
            if not hasattr(self, 'llm_service') or not self.llm_service:
                logger.error("LLM service not available")
                return await self._generate_simplified_auto_analysis1(chart_name, chart_data, full_dataset)
            
            if not hasattr(self.llm_service, 'client') or not self.llm_service.client:
                logger.error("OpenAI client not initialized in LLM service")
                return await self._generate_simplified_auto_analysis1(chart_name, chart_data, full_dataset)
            
            logger.info("OpenAI client is available")
            
            # Get chart columns
            logger.info("Step 5: Extracting chart columns")
            chart_columns = list(set(chart_data.columns.tolist()))
            logger.info("Chart columns: {}".format(chart_columns))
            
            # OpenAI-powered X/Y axis detection using actual data
            logger.info("Step 6: Starting OpenAI X/Y axis detection")
            try:
                xy_detection = await self._detect_xy_axes_with_llm_and_data(chart_data)
                if xy_detection and xy_detection.get('x_axis') and xy_detection.get('y_axis'):
                    logger.info("OpenAI X/Y detection successful")
                else:
                    logger.warning("OpenAI X/Y detection returned invalid result, using heuristics")
                    xy_detection = self._detect_xy_axes_heuristic(chart_data)
            except Exception as e:
                logger.warning("OpenAI X/Y detection failed: {}, using heuristics".format(e))
                xy_detection = self._detect_xy_axes_heuristic(chart_data)
            
            y_column = xy_detection.get('y_axis')
            x_axis_column = xy_detection.get('x_axis')
            confidence = xy_detection.get('confidence', 0.0)
            
            logger.info("Detected Y-axis: {}, X-axis: {}, confidence: {:.2f}".format(y_column, x_axis_column, confidence))
            
            # Validate detected columns exist
            logger.info("Step 7: Validating detected columns")
            if not y_column or y_column not in chart_data.columns:
                logger.error("Detected Y-axis '{}' not found in chart columns: {}".format(y_column, chart_columns))
                return await self._generate_simplified_auto_analysis1(chart_name, chart_data, full_dataset)
            
            if x_axis_column and x_axis_column not in chart_data.columns:
                logger.warning("Detected X-axis '{}' not found, setting to None".format(x_axis_column))
                x_axis_column = None
            
            # Get CSV path with enhanced detection
            logger.info("Step 8: Detecting CSV path")
            csv_path = self._get_csv_path()
            logger.info("CSV path detection result: {}".format(csv_path))
            
            if not csv_path:
                logger.warning("Primary CSV detection failed, trying fallback methods")
                # Fallback: check project root for any CSV files
                import glob
                csv_files = glob.glob("*.csv")
                if csv_files:
                    csv_path = csv_files[0]
                    logger.info("Found fallback CSV file: {}".format(csv_path))
                else:
                    logger.error("No CSV files found in project root. Available files: {}".format(os.listdir('.')))
                    return await self._generate_simplified_auto_analysis1(chart_name, chart_data, full_dataset)
            
            # Import causal analysis function
            logger.info("Step 9: Importing causal analysis function")
            try:
                from services.causal_feature_importance_aniket import analyze_universal_causal_impact_integrated
                logger.info("Successfully imported causal analysis function")
                
                # Run causal analysis with detected axes
                logger.info("Step 10: Running causal analysis")
                logger.info("Parameters: CSV={}, Y={}, X={}".format(csv_path, y_column, x_axis_column))
                
                analyzer, top_drivers = analyze_universal_causal_impact_integrated(
                    csv_path=csv_path,
                    y_column=y_column,
                    x_axis_column=x_axis_column,
                    openai_client=self.llm_service.client,
                    logger=logger,
                    time_aggregation="daily",
                    max_features=12
                )
                
                logger.info("Causal analysis completed successfully")
                
                # Extract results and cache
                logger.info("Step 11: Extracting results")
                top_5_features = [feature for feature, _ in top_drivers[:5]]
                domain_type = analyzer.llm_selection['domain']
                
                logger.info("Top 5 features: {}".format(top_5_features))
                logger.info("Domain: {}".format(domain_type))
                
            except Exception as e:
                logger.warning("EconML causal analysis failed (DLL issues): {}".format(e))
                logger.info("Step 9b: Using statistical fallback analysis")
                
                # Statistical fallback that mimics causal analysis output
                top_5_features, domain_type, top_drivers = await self._run_statistical_fallback_analysis(
                    csv_path, y_column, x_axis_column, chart_columns)
                
                logger.info("Statistical fallback completed")
                logger.info("Top 5 features: {}".format(top_5_features))
                logger.info("Domain: {}".format(domain_type))
            
            # Store with X/Y detection details
            logger.info("Step 12: Caching results")
            self._cache_causal_results(chart_name, top_5_features, domain_type, csv_path, 
                                     x_axis_column, y_column, xy_detection, top_drivers, workbook_id=workbook_id)
            
            # Create enhanced result
            logger.info("Step 13: Creating final result")
            result = self._create_causal_result(chart_name, chart_columns, top_5_features, domain_type, top_drivers, worksheet_data)
            logger.info("=== CAUSAL ANALYSIS SUCCESS ===")
            return result
            
        except Exception as e:
            logger.error("=== CAUSAL ANALYSIS FAILED ===")
            logger.error("Error: {}".format(str(e)))
            import traceback
            logger.error("Traceback: {}".format(traceback.format_exc()))
            logger.info("Falling back to correlation-based analysis")
            return await self._generate_simplified_auto_analysis1(chart_name, chart_data, full_dataset)

    async def _generate_simplified_auto_analysis1(self, chart_name: str, chart_data, full_dataset=None) -> AutoAnalysisResult:
        """Generate simplified auto-analysis showing only chart columns and most impactful columns"""
        import pandas as pd
        import numpy as np
        
        try:
            logger.info("=== FALLBACK TO CORRELATION ANALYSIS ===")
            logger.info("Running original correlation-based analysis for chart: {}".format(chart_name))
            logger.info("=== Generating simplified auto-analysis ===")
            logger.info(f"Chart: {chart_name}")
            logger.info(f"Data shape: {chart_data.shape if hasattr(chart_data, 'shape') else 'not a DataFrame'}")
            
            # Dynamically identify chart columns from the filtered chart data (remove duplicates)
            chart_columns = list(set(chart_data.columns.tolist()))
            
            logger.info(f"Chart columns (from selected chart): {chart_columns}")
            
            # Use full dataset for impact analysis if available, otherwise fall back to chart data
            analysis_dataset = full_dataset if full_dataset is not None else chart_data
            logger.info(f"Analysis dataset shape: {analysis_dataset.shape if hasattr(analysis_dataset, 'shape') else 'not a DataFrame'}")
            logger.info(f"Using {'full dataset' if full_dataset is not None else 'chart data only'} for impact analysis")
            
            # Get all columns from analysis dataset
            all_numeric_cols = analysis_dataset.select_dtypes(include=[np.number]).columns.tolist()
            all_categorical_cols = analysis_dataset.select_dtypes(include=['object']).columns.tolist()
            logger.info(f"All numeric columns: {all_numeric_cols}")
            logger.info(f"All categorical columns: {all_categorical_cols}")
            
            # Find remaining columns (not in current chart)
            remaining_numeric = [col for col in all_numeric_cols if col not in chart_columns]
            remaining_categorical = [col for col in all_categorical_cols if col not in chart_columns]
            logger.info(f"Remaining numeric columns for analysis: {remaining_numeric}")
            logger.info(f"Remaining categorical columns for analysis: {remaining_categorical}")
            
            # Correlation-based impact analysis
            most_impactful = []
            impact_reasons = []
            correlation_results = []
            
            # Analyze correlations for all remaining columns
            for remaining_col in remaining_numeric + remaining_categorical:
                max_correlation = 0
                correlated_chart_col = None
                
                # Find strongest correlation with any chart column
                for chart_col in chart_columns:
                    try:
                        if remaining_col in all_numeric_cols and chart_col in all_numeric_cols:
                            # Numeric-Numeric correlation using pandas corr
                            corr = abs(analysis_dataset[remaining_col].corr(analysis_dataset[chart_col]))
                            if not np.isnan(corr):
                                correlation = corr
                            else:
                                correlation = 0.0
                        else:
                            # Mixed or categorical correlation using custom function
                            correlation = self._calculate_categorical_correlation(analysis_dataset, remaining_col, chart_col)
                        
                        if correlation > max_correlation:
                            max_correlation = correlation
                            correlated_chart_col = chart_col
                            
                    except Exception as e:
                        logger.warning(f"Error calculating correlation between {remaining_col} and {chart_col}: {e}")
                        continue
                
                # Store result for sorting
                if max_correlation > 0.1:  # Minimum threshold for consideration
                    correlation_results.append((remaining_col, max_correlation, correlated_chart_col))
                    logger.info(f"Found correlation: {remaining_col} <-> {correlated_chart_col} (r={max_correlation:.3f})")
            
            # Sort by correlation strength and take top results
            correlation_results.sort(key=lambda x: x[1], reverse=True)
            
            # Filter out perfect correlations (r=1.0) unless ALL correlations are perfect
            non_perfect_correlations = [result for result in correlation_results if result[1] < 0.99]
            perfect_correlations = [result for result in correlation_results if result[1] >= 0.99]
            
            # Strategy: Always try to show 2 most impactful, preferring non-perfect correlations
            final_correlations = []
            
            if len(non_perfect_correlations) >= 2:
                # We have 2+ meaningful correlations, use top 2 non-perfect
                final_correlations = non_perfect_correlations[:2]
                logger.info(f"Using top 2 non-perfect correlations, ignoring {len(perfect_correlations)} perfect correlations (likely duplicates)")
            elif len(non_perfect_correlations) == 1:
                # 1 meaningful + need 1 more, take best perfect correlation
                final_correlations = non_perfect_correlations[:1] + perfect_correlations[:1]
                logger.info(f"Using 1 non-perfect + 1 perfect correlation (mixed results)")
            else:
                # All correlations are perfect - don't show specific columns, show explanation instead
                logger.info(f"All correlations are perfect - showing explanation message instead of column names")
                final_correlations = []  # Empty list to trigger special message
            
            # Build most impactful list from final_correlations (only if we have meaningful correlations)
            for remaining_col, correlation, chart_col in final_correlations:
                if correlation > 0.3:  # Strong correlation
                    reason = f"strongly correlated with {chart_col} (r={correlation:.2f})"
                elif correlation > 0.2:  # Moderate correlation
                    reason = f"moderately correlated with {chart_col} (r={correlation:.2f})"
                else:  # Weak but notable correlation
                    reason = f"correlated with {chart_col} (r={correlation:.2f})"
                
                most_impactful.append(remaining_col)
                impact_reasons.append(reason)
                logger.info(f"Added impactful column: {remaining_col} - {reason}")
            
            # Create simplified output - only show chart columns and most impactful
            if len(most_impactful) >= 2:
                summary_text = f"""**Chart Columns:** {', '.join(chart_columns)}

**Most Impactful Columns:** {most_impactful[0]}, {most_impactful[1]}"""
            elif len(most_impactful) >= 1:
                summary_text = f"""**Chart Columns:** {', '.join(chart_columns)}

**Most Impactful Columns:** {most_impactful[0]}"""
            else:
                # Better fallback messaging based on actual situation
                if len(perfect_correlations) > 0 and len(non_perfect_correlations) == 0:
                    # All correlations were perfect - show special message
                    summary_text = f"""**Chart Columns:** {', '.join(chart_columns)}

**Most Impactful Columns:** All correlations were perfect (r=1.0) - indicating highly structured data relationships"""
                elif full_dataset is None:
                    summary_text = f"""**Chart Columns:** {', '.join(chart_columns)}

**Most Impactful Columns:** Analysis limited to chart data only - no additional columns available for comparison"""
                elif len(remaining_numeric + remaining_categorical) == 0:
                    summary_text = f"""**Chart Columns:** {', '.join(chart_columns)}

**Most Impactful Columns:** All available dataset columns are already displayed in this chart"""
                else:
                    summary_text = f"""**Chart Columns:** {', '.join(chart_columns)}

**Most Impactful Columns:** No significant correlations found with remaining {len(remaining_numeric + remaining_categorical)} columns"""
            
            logger.info(f"Generated simplified summary: {summary_text}")
            
            return AutoAnalysisResult(
                summary_lines=[summary_text],
                statistical_analysis={},
                data_aggregation={},
                most_impactful_columns=[]
            )
            
        except Exception as e:
            logger.error(f"Error in simplified auto-analysis: {e}")
            return AutoAnalysisResult(
                summary_lines=[f"**Chart Columns:** {', '.join(chart_data.columns.tolist()) if hasattr(chart_data, 'columns') else 'Unable to identify columns'}"],
                statistical_analysis={},
                data_aggregation={},
                most_impactful_columns=[]
            )

    async def _execute_intent_analysis(self, intent_result: QueryIntent, query: str, workbook_data: Dict[str, Any]) -> Dict[str, Any]:
        """Execute analysis based on the identified intent"""
        
        try:
            intent_type = intent_result.primary_intent.value
            
            # Get first available worksheet for analysis
            worksheet_name = list(workbook_data.keys())[0]
            df = list(workbook_data.values())[0]
            
            analysis_result = {
                "intent_type": intent_type,
                "worksheet_analyzed": worksheet_name,
                "query": query
            }
            
            if intent_type == "top_bottom_analysis":
                # Use NL to Python for ranking analysis
                pandas_result = self.data_processor.execute_pandas_aggregation(query, df)
                analysis_result["pandas_execution"] = pandas_result
                
            elif intent_type == "comparison":
                # Use multi-table service for comparison
                if len(workbook_data) > 1:
                    # Add all worksheets to multi-table service
                    for ws_name, ws_df in workbook_data.items():
                        self.multi_table_service.add_worksheet_data(ws_name, ws_df)
                    
                    # Execute cross-worksheet comparison
                    comparison_result = self.multi_table_service.execute_cross_worksheet_query(query)
                    analysis_result["multi_table_analysis"] = comparison_result
                else:
                    # Single worksheet comparison
                    pandas_result = self.data_processor.execute_pandas_aggregation(query, df)
                    analysis_result["pandas_execution"] = pandas_result
                    
            elif intent_type in ["statistical_significance", "correlation"]:
                # Statistical analysis
                statistical_summary = self.data_processor.calculate_statistical_summary(df)
                analysis_result["statistical_analysis"] = statistical_summary
                
            elif intent_type == "trend_analysis":
                # Trend analysis with aggregation
                pandas_result = self.data_processor.execute_pandas_aggregation(query, df)
                analysis_result["pandas_execution"] = pandas_result
                analysis_result["trend_context"] = self._identify_temporal_columns(df)
                
            else:
                # General data exploration
                pandas_result = self.data_processor.execute_pandas_aggregation(query, df)
                analysis_result["pandas_execution"] = pandas_result
            
            return analysis_result
            
        except Exception as e:
            logger.error(f"Error executing intent analysis: {e}")
            return {"error": str(e), "intent_type": intent_result.primary_intent.value}

    async def _create_visualization_for_intent(self, intent_result: QueryIntent, analysis_result: Dict[str, Any], workbook_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Create appropriate visualization based on intent and analysis"""
        
        try:
            # Get the main DataFrame for visualization
            df = list(workbook_data.values())[0]
            query = analysis_result.get("query", "")
            
            # Generate visualization
            visualization = self.viz_service.create_intelligent_visualization(
                query, df
            )
            
            if not visualization.get("needs_visualization", False):
                return None
            
            return {
                "chart_type": visualization.get("chart_type"),
                "chart_image": visualization.get("chart_image"),
                "title": visualization.get("chart_config", {}).get("title", "Chart"),
                "description": visualization.get("chart_description", ""),
                "insights": visualization.get("data_insights", []),
                "metadata": {}
            }
            
        except Exception as e:
            logger.error(f"Error creating visualization: {e}")
            return None

    async def _generate_enhanced_response(self, query: str, intent_result: QueryIntent, analysis_result: Optional[Dict[str, Any]], auto_analysis: Optional[AutoAnalysisResult]) -> str:
        """Generate enhanced response using LLM service"""
        
        try:
            # DEBUG LOGGING
            logger.info(f"=== _generate_enhanced_response called ===")
            logger.info(f"Query (first 200 chars): '{query[:200]}...'")
            logger.info(f"Has auto_analysis: {auto_analysis is not None}")
            if auto_analysis:
                logger.info(f"Auto_analysis has summary_lines: {hasattr(auto_analysis, 'summary_lines') and bool(auto_analysis.summary_lines)}")
                if hasattr(auto_analysis, 'summary_lines') and auto_analysis.summary_lines:
                    logger.info(f"Summary lines count: {len(auto_analysis.summary_lines)}")
                    logger.info(f"Summary lines preview: {auto_analysis.summary_lines[:2]}")
            
            # Check if query matches our condition
            condition_match = "Generate auto-analysis for the chart" in query
            logger.info(f"Condition 'Generate auto-analysis for the chart' in query: {condition_match}")
            
            # For auto-analysis requests (chart clicks), return only the simplified auto-analysis summary
            if condition_match and auto_analysis and auto_analysis.summary_lines:
                logger.info("SUCCESS: CONDITION MATCHED: Returning simplified auto-analysis summary for chart auto-analysis request")
                simplified_response = "\n".join(auto_analysis.summary_lines)
                logger.info(f"Simplified response: {simplified_response}")
                return simplified_response
            else:
                logger.info(f"ERROR: CONDITION NOT MATCHED: condition_match={condition_match}, auto_analysis={auto_analysis is not None}, has_summary_lines={auto_analysis and hasattr(auto_analysis, 'summary_lines') and bool(auto_analysis.summary_lines) if auto_analysis else False}")
            
            # For other queries, generate detailed LLM response
            logger.info("Generating detailed LLM response")
            response = await self.llm_service.generate_enhanced_response(
                query, intent_result, analysis_result or {}, worksheet_name="Selected Data"
            )
            
            # If auto-analysis is available for non-auto-analysis queries, prepend it
            if auto_analysis and auto_analysis.summary_lines and "Generate auto-analysis for the chart" not in query:
                auto_summary = "\n".join(auto_analysis.summary_lines)
                response = f"{auto_summary}\n\n{response}"
                logger.info("Prepended auto-analysis summary to detailed response")
            
            logger.info(f"Final response length: {len(response)} characters")
            return response
            
        except Exception as e:
            logger.error(f"Error generating enhanced response: {e}")
            return f"I've analyzed your query '{query}' and identified it as {intent_result.primary_intent.value}. The analysis has been completed successfully."

    async def _classify_intent_with_llm(self, query: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Use LLM service for sophisticated intent classification"""
        
        # Prepare context information
        context_str = ""
        if context:
            # Handle both ChatContext objects and dictionaries
            if hasattr(context, 'get'):
                # It's a dictionary
                available_cols = context.get("available_columns", [])
            else:
                # It's a ChatContext object, check if it has available_columns attribute
                available_cols = getattr(context, 'available_columns', [])
            
            if available_cols:
                context_str = f"\nAvailable data columns: {', '.join(available_cols[:10])}"
        
        # Create comprehensive prompt for intent classification
        system_prompt = f"""You are an expert data analyst assistant. Classify the user's query into ONE of these 8 intent categories:

1. anomaly_detection - Outlier detection, unusual patterns, exceptions
2. trend_analysis - Temporal patterns, changes over time, progressions
3. statistical_significance - Hypothesis testing, correlations, statistical analysis
4. comparison - Comparing groups, segments, or categories
5. top_bottom_analysis - Rankings, best/worst performers, extremes
6. seasonality - Cyclical patterns, seasonal trends, periodic behavior
7. prediction - Forecasting, future estimates, projections
8. data_exploration - General analysis, summaries, insights, feature importance, impact analysis

Respond with JSON: {{"intent": "category_name", "confidence": 0.0-1.0, "reasoning": "explanation"}}"""

        user_prompt = f"""Query: "{query}"{context_str}

Classify this query's primary intent and provide confidence score."""

        try:
            # Use the LLM service's internal method
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ]
            
            response = await self.llm_service._make_request(messages, max_tokens=200, temperature=0.1)
            
            # Parse JSON response
            try:
                llm_result = json.loads(response.strip())
                return {
                    "intent": llm_result.get("intent", "data_exploration"),
                    "confidence": float(llm_result.get("confidence", 0.5)),
                    "reasoning": llm_result.get("reasoning", "LLM classification"),
                    "method": "llm"
                }
            except json.JSONDecodeError:
                logger.warning(f"Invalid JSON response from LLM: {response}")
                return self._fallback_llm_classification(query)
                
        except Exception as e:
            logger.error(f"Error in LLM intent classification: {e}")
            return self._fallback_llm_classification(query)

    def _classify_intent_with_rules(self, query: str) -> Dict[str, Any]:
        """Rule-based intent classification as fallback"""
        
        query_lower = query.lower()
        intent_scores = {}
        
        # Calculate scores for each intent based on keyword matching
        for intent, config in self.intent_categories.items():
            score = 0
            keywords = config["keywords"]
            
            for keyword in keywords:
                if keyword in query_lower:
                    # Give higher weight to exact matches
                    if f" {keyword} " in f" {query_lower} ":
                        score += 2
                    else:
                        score += 1
            
            # Normalize score
            intent_scores[intent] = score / len(keywords)
        
        # Find best match
        if intent_scores:
            best_intent = max(intent_scores.items(), key=lambda x: x[1])
            confidence = min(best_intent[1], 1.0)
            
            # Require minimum confidence threshold
            if confidence < 0.1:
                return {
                    "intent": "data_exploration",
                    "confidence": 0.3,
                    "reasoning": "No clear intent pattern detected",
                    "method": "rule_fallback"
                }
            
            return {
                "intent": best_intent[0],
                "confidence": confidence,
                "reasoning": f"Keyword-based classification: {confidence:.2f}",
                "method": "rule_based"
            }
        else:
            return {
                "intent": "data_exploration",
                "confidence": 0.3,
                "reasoning": "Default classification",
                "method": "default"
            }

    def _extract_entities(self, query: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, List[str]]:
        """Extract relevant entities from the query"""
        
        query_lower = query.lower()
        entities = {}
        
        # Extract entities based on patterns
        for entity_type, patterns in self.entity_patterns.items():
            found_entities = []
            
            for pattern in patterns:
                if pattern in query_lower:
                    found_entities.append(pattern)
            
            if found_entities:
                entities[entity_type] = found_entities
        
        # Extract column names if context is provided
        available_cols = []
        if context:
            # Handle both ChatContext objects and dictionaries
            if hasattr(context, 'get'):
                # It's a dictionary
                if "available_columns" in context:
                    available_cols = context["available_columns"]
            else:
                # It's a ChatContext object
                available_cols = getattr(context, 'available_columns', [])
        
        mentioned_columns = []
        if available_cols:
            logger.debug(f"[ENTITY_EXTRACTION] Searching for column mentions in query: '{query[:100]}...'")
            logger.debug(f"[ENTITY_EXTRACTION] Available columns: {len(available_cols)} columns")
            
            # First try exact matches (fast path)
            for col in available_cols:
                if col.lower() in query_lower:
                    mentioned_columns.append(col)
                    logger.debug(f"[ENTITY_EXTRACTION] ✓ Exact mention: '{col}'")
            
            # If no exact matches found, try fuzzy matching for query terms
            if not mentioned_columns:
                # Extract potential column names from query (words that might be columns)
                import re
                # Look for words that could be column names (alphanumeric with underscores)
                potential_col_terms = re.findall(r'\b[a-z_][a-z0-9_]{2,}\b', query_lower)
                
                logger.debug(f"[ENTITY_EXTRACTION] Potential column terms in query: {potential_col_terms}")
                
                for term in potential_col_terms:
                    fuzzy_match = self.fuzzy_matcher.find_best_match(
                        term,
                        available_cols,
                        context="entity_extraction",
                        query_context=query
                    )
                    
                    if fuzzy_match and fuzzy_match not in mentioned_columns:
                        mentioned_columns.append(fuzzy_match)
                        logger.info(f"[ENTITY_EXTRACTION] ✓ Fuzzy matched term '{term}' → column '{fuzzy_match}'")
            
            if mentioned_columns:
                entities["columns"] = mentioned_columns
                logger.info(f"[ENTITY_EXTRACTION] Found {len(mentioned_columns)} column mentions: {mentioned_columns}")
        
        # Extract numeric values for top/bottom analysis
        import re
        numbers = re.findall(r'\b\d+\b', query)
        if numbers:
            entities["numbers"] = numbers
        
        return entities

    def _combine_intent_results(self, llm_result: Dict[str, Any], rule_result: Dict[str, Any]) -> Dict[str, Any]:
        """Combine LLM and rule-based classifications intelligently"""
        
        # If LLM has high confidence, use it
        if llm_result["confidence"] >= 0.7:
            return llm_result
        
        # If rule-based has higher confidence, use it
        if rule_result["confidence"] > llm_result["confidence"]:
            return rule_result
        
        # If both agree, increase confidence
        if llm_result["intent"] == rule_result["intent"]:
            combined_confidence = min((llm_result["confidence"] + rule_result["confidence"]) / 2 + 0.2, 1.0)
            return {
                "intent": llm_result["intent"],
                "confidence": combined_confidence,
                "reasoning": "LLM and rule-based agreement",
                "method": "combined"
            }
        
        # Default to LLM if available
        return llm_result if llm_result["confidence"] > 0.3 else rule_result

    def _recommend_services(self, intent: str, entities: Dict[str, List[str]]) -> List[str]:
        """Recommend which services should handle this query"""
        
        services = []
        
        # Get services from intent configuration
        intent_config = self.intent_categories.get(intent, {})
        services.extend(intent_config.get("services", ["data_processor", "visualization"]))
        
        # Entity-based additional services
        if "time_periods" in entities:
            if "multi_table" not in services:
                services.append("multi_table")
        
        if "metrics" in entities and "dimensions" in entities:
            if "nl_to_python" not in services:
                services.append("nl_to_python")
        
        return list(set(services))  # Remove duplicates

    def _analyze_chart_requirements(self, intent: str, entities: Dict[str, List[str]]) -> Dict[str, Any]:
        """Analyze what type of charts this query might need"""
        
        chart_req = {
            "suggested_chart_types": [],
            "requires_temporal": False,
            "requires_comparison": False,
            "requires_distribution": False
        }
        
        # Intent-based chart suggestions
        chart_mapping = {
            "trend_analysis": ["line", "scatter"],
            "comparison": ["bar", "box"],
            "top_bottom_analysis": ["bar", "pie"],
            "anomaly_detection": ["scatter", "box"],
            "statistical_significance": ["scatter", "heatmap"],
            "data_exploration": ["histogram", "bar", "scatter"]
        }
        
        chart_req["suggested_chart_types"] = chart_mapping.get(intent, ["bar", "scatter"])
        
        # Entity-based requirements
        chart_req["requires_temporal"] = "time_periods" in entities
        chart_req["requires_comparison"] = "dimensions" in entities and len(entities["dimensions"]) > 1
        chart_req["requires_distribution"] = intent in ["data_exploration", "anomaly_detection"]
        
        return chart_req

    def _requires_visualization(self, intent_result: QueryIntent) -> bool:
        """Determine if visualization is needed"""
        
        visualization_intents = [
            "trend_analysis", "comparison", "top_bottom_analysis", 
            "seasonality", "anomaly_detection", "data_exploration"
        ]
        
        return intent_result.primary_intent.value in visualization_intents

    def _requires_chart_selection(self, intent_result: QueryIntent, workbook_data: Optional[Dict[str, Any]]) -> bool:
        """Determine if chart selection UI should be shown"""
        
        # If no specific query and multiple charts available
        if workbook_data and len(workbook_data) > 1:
            intent_type = intent_result.primary_intent.value
            
            # These intents benefit from chart selection
            chart_selection_intents = [
                "comparison", "trend_analysis", "data_exploration"
            ]
            
            return intent_type in chart_selection_intents
        
        return False

    def _generate_suggested_actions(self, intent_result: QueryIntent, workbook_data: Optional[Dict[str, Any]]) -> List[str]:
        """Generate suggested follow-up actions"""
        
        suggestions = []
        intent_type = intent_result.primary_intent.value
        
        intent_suggestions = {
            "data_exploration": [
                "Ask about trends over time",
                "Compare different categories",
                "Find top performers"
            ],
            "comparison": [
                "Analyze statistical significance",
                "Look for outliers",
                "Examine seasonal patterns"
            ],
            "trend_analysis": [
                "Predict future trends",
                "Identify anomalies",
                "Compare with other metrics"
            ],
            "top_bottom_analysis": [
                "Analyze what drives top performance",
                "Compare time periods",
                "Look for patterns in rankings"
            ]
        }
        
        suggestions.extend(intent_suggestions.get(intent_type, [
            "Explore data patterns",
            "Create visualizations",
            "Perform statistical analysis"
        ]))
        
        return suggestions[:3]  # Limit to 3 suggestions

    def _calculate_complexity(self, query: str, entities: Dict[str, List[str]]) -> float:
        """Calculate query complexity score (0.0 - 1.0)"""
        
        complexity = 0.0
        
        # Length factor
        complexity += min(len(query.split()) / 20, 0.3)
        
        # Entity diversity factor
        complexity += len(entities) * 0.1
        
        # Multiple metrics/dimensions increase complexity
        if "metrics" in entities:
            complexity += len(entities["metrics"]) * 0.05
        
        if "dimensions" in entities:
            complexity += len(entities["dimensions"]) * 0.05
        
        # Statistical terms increase complexity
        statistical_terms = ["correlation", "significance", "hypothesis", "regression", "model"]
        for term in statistical_terms:
            if term in query.lower():
                complexity += 0.1
        
        return min(complexity, 1.0)

    def _identify_temporal_columns(self, df) -> List[str]:
        """Identify columns that might contain temporal data"""
        
        temporal_cols = []
        
        for col in df.columns:
            col_lower = col.lower()
            if any(keyword in col_lower for keyword in ['date', 'time', 'month', 'year', 'day']):
                temporal_cols.append(col)
        
        return temporal_cols

    def _fallback_llm_classification(self, query: str) -> Dict[str, Any]:
        """Fallback classification when LLM fails"""
        
        return {
            "intent": "data_exploration",
            "confidence": 0.4,
            "reasoning": "LLM classification failed, using fallback",
            "method": "fallback"
        }

    def _create_fallback_intent(self, query: str) -> QueryIntent:
        """Create fallback intent when everything fails"""
        
        return QueryIntent(
            primary_intent=IntentType.DATA_EXPLORATION,
            confidence=0.3,
            entities={},
            requires_agents=["data_processor", "visualization"],
            chart_requirements={"suggested_chart_types": ["bar", "scatter"]},
            metadata={"error": "Query understanding failed, using defaults"}
        )

    def _create_fallback_auto_analysis(self, chart_name: str) -> AutoAnalysisResult:
        """Create fallback auto-analysis"""
        
        return AutoAnalysisResult(
            most_impactful_columns=[],
            data_aggregation={"error": "Unable to generate data aggregation"},
            statistical_analysis={"error": "Unable to generate statistical analysis"},
            summary_lines=[
                f"**Selected Chart:** {chart_name} ready for analysis",
                "**Data Overview:** Chart data loaded and available for exploration",
                "**Analysis Ready:** Ask questions to dive deeper into the data"
            ],
            execution_time=0.1
        )

    def _create_error_response(self, query: str, error: str) -> Dict[str, Any]:
        """Create error response"""
        
        return {
            "success": False,
            "reply": f"I encountered an issue processing your query '{query}'. Please try rephrasing your question or selecting a different chart for analysis.",
            "error": error,
            "intent": self._create_fallback_intent(query),
            "requires_chart_selection": True,
            "suggested_actions": [
                "Try a simpler question",
                "Select a specific chart first",
                "Check your data connection"
            ]
        }

    def _detect_axes_with_llm(self, chart_name, chart_columns):
        """
        Use LLM to intelligently detect x-axes and y-axes from chart columns.
        Enhanced to support multiple X-axis features for multi-dimensional analysis.
        
        Args:
            chart_name: Name of the chart for context
            chart_columns: List of column names from the chart
            
        Returns:
            dict: {'x_axes': List[str], 'y_axes': List[str], 'reasoning': str, 'x_axis': str} or None if failed
            Note: x_axis is maintained for backward compatibility (first x_axes element)
        """
        try:
            if not self.llm_service or not self.llm_service.client:
                logger.error("LLM service not available for axis detection")
                return None
                
            # Construct enhanced prompt for accurate axis detection
            prompt = f"""
You are a data visualization expert analyzing a chart called "{chart_name}" with these columns: {chart_columns}

Your task is to correctly identify X-axes (independent variables/dimensions) and Y-axes (dependent variables/metrics).

CRITICAL PRINCIPLES:

1. **X-AXES (Independent Variables/Dimensions):**
   - Time periods (dates, months, years, quarters)
   - Categories for grouping (regions, departments, product types)
   - Demographic dimensions (age groups, customer segments)
   - Any variable that serves as a basis for comparison or grouping
   - Can be multiple for multi-dimensional analysis

2. **Y-AXES (Dependent Variables/Metrics):**
   - Business measurements (revenue, profit, costs)
   - Volumes and quantities (sales volume, ticket volume, transaction volume)
   - Counts and totals (number of customers, total orders, case count)
   - Rates and percentages (conversion rate, growth rate, satisfaction score)
   - Any numerical value that represents business performance or outcomes
   - Multiple related metrics can coexist (e.g., open volume + closed volume)

3. **KEY DECISION RULES:**
   - If a column represents "how much" or "how many" → Y-axis (metric)
   - If a column represents "when", "where", or "what category" → X-axis (dimension)
   - Business volumes, amounts, and counts are almost always Y-axes
   - Time dimensions are almost always X-axes

4. **MULTI-METRIC CHARTS:**
   - Charts often compare multiple related metrics (e.g., "Open_Volume" and "Closed_Volume")
   - All related business metrics should be Y-axes together
   - Don't mix metrics with dimensions in X-axes

VALIDATION CHECKLIST:
- Are all numerical business values (volumes, counts, amounts) in Y-axes?
- Are all time/category dimensions in X-axes?
- Does the axis assignment make business sense for trend analysis?

Respond in this exact JSON format:
{{
    "x_axes": ["dimension1", "dimension2"],
    "y_axes": ["metric1", "metric2"],
    "reasoning": "Detailed explanation of classification logic, emphasizing why each column is a dimension vs metric"
}}

Analyze carefully and ensure proper classification of business metrics vs dimensions.
"""

            # Make LLM call using existing SSL-bypassed client
            response = self.llm_service.client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=500,
                temperature=0.1
            )
            
            response_text = response.choices[0].message.content.strip()
            logger.info(f"[LLM_AXIS_DETECTION] Raw response: {response_text}")
            
            # Parse JSON response
            import json
            try:
                # Extract JSON from response (handle potential markdown formatting)
                if "```json" in response_text:
                    json_start = response_text.find("```json") + 7
                    json_end = response_text.find("```", json_start)
                    response_text = response_text[json_start:json_end].strip()
                elif "```" in response_text:
                    json_start = response_text.find("```") + 3
                    json_end = response_text.find("```", json_start)
                    response_text = response_text[json_start:json_end].strip()
                
                axis_detection = json.loads(response_text)
                
                # Handle both new format (x_axes) and legacy format (x_axis) for backward compatibility
                if 'x_axes' in axis_detection:
                    # New multi-axis format
                    x_axes = axis_detection['x_axes']
                    y_axes = axis_detection['y_axes']
                    
                    # Validate that x_axes is a list
                    if not isinstance(x_axes, list):
                        logger.error("LLM response x_axes should be a list")
                        return None
                    
                    # Validate that all suggested x_axes exist in chart_columns
                    invalid_x_axes = [x for x in x_axes if x not in chart_columns]
                    if invalid_x_axes:
                        logger.error(f"LLM suggested invalid x_axes: {invalid_x_axes}")
                        return None
                    
                    # Add backward compatibility x_axis (primary x-axis)
                    axis_detection['x_axis'] = x_axes[0] if x_axes else None
                    
                    logger.info(f"[LLM_AXIS_DETECTION] Success - X-axes: {x_axes}, Y-axes: {y_axes}")
                    logger.info(f"[LLM_AXIS_DETECTION] Primary X-axis (backward compatibility): {axis_detection['x_axis']}")
                    
                elif 'x_axis' in axis_detection:
                    # Legacy single-axis format - convert to new format
                    x_axis = axis_detection['x_axis']
                    y_axes = axis_detection['y_axes']
                    
                    if x_axis not in chart_columns:
                        logger.error(f"LLM suggested x_axis '{x_axis}' not in chart_columns")
                        return None
                    
                    # Convert to new format
                    axis_detection['x_axes'] = [x_axis]
                    
                    logger.info(f"[LLM_AXIS_DETECTION] Legacy format detected - converted X: {x_axis} to X-axes: {[x_axis]}")
                    logger.info(f"[LLM_AXIS_DETECTION] Y-axes: {y_axes}")
                    
                else:
                    logger.error("LLM response missing both x_axes and x_axis keys")
                    return None
                
                # Validate response structure
                if not all(key in axis_detection for key in ['y_axes', 'reasoning']):
                    logger.error("LLM response missing required keys (y_axes, reasoning)")
                    return None
                    
                # Validate that suggested y_axes exist in chart_columns
                invalid_y_axes = [y for y in y_axes if y not in chart_columns]
                if invalid_y_axes:
                    logger.error(f"LLM suggested invalid y_axes: {invalid_y_axes}")
                    return None
                
                logger.info(f"[LLM_AXIS_DETECTION] Reasoning: {axis_detection['reasoning']}")
                
                return axis_detection
                
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse LLM response as JSON: {e}")
                return None
                
        except Exception as e:
            logger.error(f"LLM axis detection failed: {e}")
            return None

    def _get_top_2_impactful_columns(self, chart_name, chart_columns, workbook_id: Optional[str] = None, csv_path_override: Optional[str] = None):
        """Get top 2 most impactful columns from cache or calculate new ones, top 2 is written just for name sake,
        in reality it returns top 5 only, didn't change the variable and function name to avoid breaking changes"""
        try:
            import json
            import os
            cache_file = "causal_analysis_cache.json"
            
            # Check cache first
            if os.path.exists(cache_file):
                with open(cache_file, 'r') as f:
                    cache_data = json.load(f)
                
                # NEW: Nested structure -> cache[workbook_id][chart_name]
                selected_cache_entry = None
                selected_wb = None
                if isinstance(cache_data, dict):
                    if workbook_id and workbook_id in cache_data and isinstance(cache_data[workbook_id], dict):
                        if chart_name in cache_data[workbook_id]:
                            selected_cache_entry = cache_data[workbook_id][chart_name]
                            selected_wb = workbook_id
                    if selected_cache_entry is None:
                        # Fallback: scan all workbooks to find the chart (pick latest by timestamp if multiple)
                        latest_ts = None
                        for wb_id, charts in cache_data.items():
                            if isinstance(charts, dict) and chart_name in charts:
                                entry = charts[chart_name]
                                ts_str = entry.get('timestamp')
                                ts = ts_str
                                try:
                                    # Normalize timestamp if possible
                                    from datetime import datetime
                                    if ts_str:
                                        ts = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
                                except Exception:
                                    ts = None
                                if selected_cache_entry is None or (ts and latest_ts and ts > latest_ts) or (ts and latest_ts is None):
                                    selected_cache_entry = entry
                                    selected_wb = wb_id
                                    latest_ts = ts
                if selected_cache_entry and "top_5_features" in selected_cache_entry:
                    top_2 = selected_cache_entry["top_5_features"]
                    logger.info(f"Found cached top 2 impactful columns for {chart_name} (workbook={selected_wb}): {top_2}")
                    return ", ".join(top_2)
            
            # SHAP causal analysis temporarily disabled - shap_analysis_v6 removed
            logger.info(f"No cache found for {chart_name}, SHAP analysis temporarily disabled")
            return "Feature analysis temporarily unavailable"
                
        except Exception as e:
            logger.warning(f"Error getting most impactful columns for {chart_name}: {e}")
            return "Error in analysis"
