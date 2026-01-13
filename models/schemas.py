"""
Tableau Analytics Agent Data Models
Enhanced schemas for Tableau integration with agentic capabilities
"""

from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional, Union
from datetime import datetime
from dataclasses import dataclass
from enum import Enum

# ============================================================================
# Query Intent and Agent Models
# ============================================================================

class IntentType(str, Enum):
    """Supported query intent types"""
    SHAP_ANALYSIS = "shap_analysis"
    ANOMALY_DETECTION = "anomaly_detection"
    TREND_ANALYSIS = "trend_analysis"
    STATISTICAL_SIGNIFICANCE = "statistical_significance"
    COMPARISON = "comparison"
    TOP_BOTTOM_ANALYSIS = "top_bottom_analysis"
    SEASONALITY = "seasonality"
    PREDICTION = "prediction"
    DATA_EXPLORATION = "data_exploration"

class QueryIntent(BaseModel):
    """Structured representation of user query intent"""
    primary_intent: IntentType
    confidence: float = Field(ge=0.0, le=1.0)
    entities: Dict[str, List[str]] = Field(default_factory=dict)
    requires_agents: List[str] = Field(default_factory=list)
    chart_requirements: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)

class AgentResponse(BaseModel):
    """Standardized response from any agent"""
    agent_id: str
    success: bool
    data: Optional[Dict[str, Any]] = None
    message: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    execution_time: float = 0.0
    metadata: Dict[str, Any] = Field(default_factory=dict)

# ============================================================================
# Tableau-Specific Models
# ============================================================================

class TableauWorksheet(BaseModel):
    """Tableau worksheet metadata"""
    id: str
    name: str
    content_url: Optional[str] = None
    data_shape: tuple[int, int] = (0, 0)  # (rows, columns)
    column_names: List[str] = Field(default_factory=list)
    data_types: Dict[str, str] = Field(default_factory=dict)
    has_data: bool = False

class TableauWorkbook(BaseModel):
    """Tableau workbook with all worksheets"""
    id: str
    name: str
    worksheets: List[TableauWorksheet] = Field(default_factory=list)
    total_rows: int = 0
    total_columns: int = 0
    data_summary: str = ""
    connection_timestamp: datetime = Field(default_factory=datetime.now)

class WorkbookDataSummary(BaseModel):
    """Enhanced workbook summary for user display"""
    workbook_name: str
    total_rows: int
    total_worksheets: int
    key_metrics: List[str] = Field(default_factory=list)
    analysis_types: List[str] = Field(default_factory=list)
    summary_line1: str = ""
    summary_line2: str = ""

# ============================================================================
# Analysis and Visualization Models
# ============================================================================

class ChartType(str, Enum):
    """Supported chart types for static visualization"""
    BAR = "bar"
    LINE = "line"
    SCATTER = "scatter"
    PIE = "pie"
    HISTOGRAM = "histogram"
    BOX = "box"
    VIOLIN = "violin"
    HEATMAP = "heatmap"
    TREEMAP = "treemap"
    SUNBURST = "sunburst"

class StaticVisualization(BaseModel):
    """Static chart specification with base64 image"""
    chart_type: ChartType
    chart_image: Optional[str] = None  # base64 encoded image
    title: str
    description: str
    insights: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    needs_visualization: bool = True

class AutoAnalysisResult(BaseModel):
    """Auto-analysis result for chart selection"""
    most_impactful_columns: List[Dict[str, Any]] = Field(default_factory=list)
    data_aggregation: Dict[str, Any] = Field(default_factory=dict)
    statistical_analysis: Dict[str, Any] = Field(default_factory=dict)
    summary_lines: List[str] = Field(default_factory=list)
    execution_time: float = 0.0

class ImpactColumn(BaseModel):
    """Column impact analysis result"""
    column_name: str
    impact_score: float = Field(ge=0.0, le=1.0)
    variance_explained: float = Field(ge=0.0, le=1.0)
    data_type: str
    sample_values: List[Any] = Field(default_factory=list)

# ============================================================================
# Python Code Generation Models
# ============================================================================

class PandasOperation(BaseModel):
    """Generated pandas operation"""
    operation_type: str  # groupby, aggregate, filter, sort, etc.
    code: str
    description: str
    expected_output: Optional[str] = None

class NLToPythonResult(BaseModel):
    """Natural language to Python conversion result"""
    original_query: str
    generated_code: str
    operation_type: str
    confidence: float = Field(ge=0.0, le=1.0)
    explanation: str
    expected_columns: List[str] = Field(default_factory=list)
    suggested_chart_type: Optional[str] = None
    
    # 🆕 Column extractions from Stage1 (for template engine)
    group_by_columns: Optional[List[str]] = Field(default=None, description="Columns used for grouping (extracted from Stage1)")
    metric_column: Optional[str] = Field(default=None, description="Main metric/aggregation column (extracted from Stage1)")
    filter_column: Optional[str] = Field(default=None, description="Primary filter column (extracted from Stage1)")
    
    # 🆕 Ranking query flags (for proper display sorting)
    is_bottom_query: bool = Field(default=False, description="True if query asks for lowest/bottom/least values")
    is_top_query: bool = Field(default=False, description="True if query asks for highest/top/most values") 
# ============================================================================
# RAG and Vector Storage Models
# ============================================================================

class DataChunk(BaseModel):
    """Data chunk for vector storage"""
    chunk_id: str
    workbook_id: str
    worksheet_name: str
    data_summary: str
    column_info: Dict[str, Any]
    embedding: Optional[List[float]] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

class VectorSearchResult(BaseModel):
    """Vector search result"""
    chunk_id: str
    similarity_score: float = Field(ge=0.0, le=1.0)
    data_summary: str
    metadata: Dict[str, Any] = Field(default_factory=dict)

# ============================================================================
# Enhanced Chat Models
# ============================================================================

class ChatContext(BaseModel):
    """Enhanced chat context with Tableau integration"""
    model_config = {"extra": "allow"}  # Allow extra fields for backward compatibility
    
    workbook_name: Optional[str] = None  # 🆕 Add workbook_name for identifier detection
    workbook_id: Optional[str] = None
    selected_worksheet: Optional[str] = None
    available_worksheets: List[str] = Field(default_factory=list)
    available_columns: List[str] = Field(default_factory=list)  # 🆕 Add available_columns
    available_charts: List[str] = Field(default_factory=list)  # 🆕 Add available_charts
    user_intent: Optional[QueryIntent] = None
    previous_analysis: Optional[Dict[str, Any]] = None
    csv_file_path: Optional[str] = None  # Thread-safe CSV path for cache

class EnhancedChatRequest(BaseModel):
    """Enhanced chat request with agent routing"""
    message: str
    context: Optional[ChatContext] = None
    tableau_context: Optional[Dict[str, Any]] = None
    connection_key: Optional[str] = None
    selected_chart: Optional[str] = None
    chart_context: Optional[Dict[str, Any]] = None
    source: Optional[str] = None

    # Dashboard filter support (Phase 1 implementation)
    use_dashboard_filters: bool = False
    dashboard_filters: Optional[Dict[str, Any]] = None
    query_filters: Optional[Dict[str, Any]] = None

class EnhancedChatResponse(BaseModel):
    """Enhanced chat response with visualizations and analysis"""
    reply: str
    intent: Optional[QueryIntent] = None
    auto_analysis: Optional[AutoAnalysisResult] = None
    visualization: Optional[StaticVisualization] = None
    python_code_executed: Optional[List[PandasOperation]] = None
    requires_chart_selection: bool = False
    suggested_actions: List[str] = Field(default_factory=list)
    execution_time: float = 0.0
    error: bool = False

# ============================================================================
# Statistical Analysis Models
# ============================================================================

class StatisticalResult(BaseModel):
    """Statistical analysis result"""
    test_type: str
    statistic: float
    p_value: Optional[float] = None
    confidence_interval: Optional[tuple[float, float]] = None
    effect_size: Optional[float] = None
    interpretation: str
    significance_level: float = 0.05

class CorrelationMatrix(BaseModel):
    """Correlation analysis result"""
    matrix: Dict[str, Dict[str, float]]
    method: str = "pearson"  # pearson, spearman, kendall
    significant_pairs: List[tuple[str, str, float]] = Field(default_factory=list)

# ============================================================================
# Legacy Support Models (for backward compatibility)
# ============================================================================

class QueryRequest(BaseModel):
    """Legacy query request model"""
    dataset_id: str
    query: str

class DatasetInfo(BaseModel):
    """Legacy dataset info model"""
    id: str
    filename: str
    upload_time: str
    shape: List[int]
    columns: List[str]
    dtypes: Dict[str, str]

class InsightResponse(BaseModel):
    """Legacy insight response model"""
    dataset_id: str
    insights: List[Dict[str, Any]]
    generated_at: str

# ============================================================================
# Configuration Models
# ============================================================================

class AgentConfig(BaseModel):
    """Configuration for individual agents"""
    agent_id: str
    enabled: bool = True
    model_name: str = "gpt-4o-mini"
    temperature: float = Field(default=0.1, ge=0.0, le=2.0)
    max_tokens: int = Field(default=1000, gt=0)
    timeout: float = Field(default=300.0, gt=0)
    retry_attempts: int = Field(default=3, ge=0)

class SystemConfig(BaseModel):
    """System-wide configuration"""
    agents: Dict[str, AgentConfig] = Field(default_factory=dict)
    vector_db_path: str = "RAG/data"
    cache_ttl: int = Field(default=3600, gt=0)  # Cache TTL in seconds
    max_workbook_size_mb: int = Field(default=100, gt=0)
    enable_auto_analysis: bool = True
    chart_theme: str = "default"
