"""
Natural Language to Python Code Generator - Refactored Module

This package contains the refactored NL to Python code generator, split into modular components:
- nl_to_python_schemas.py: Data models, validation, and grounding logic
- nl_to_python_operations.py: Individual operation nodes and routing
- nl_to_python_codegen.py: Pandas code generation for all operations
- nl_to_python_workflow.py: Main workflow orchestrator (LangGraph)

This __init__.py maintains backward compatibility by exposing the same interface
as the original NL_to_python.py file.
"""

# Import all classes and functions to maintain backward compatibility
from .nl_to_python_schemas import (
    # Exception classes
    UserInputRequiredException,
    MultiOperationQueryHandler,
    
    # Context management
    DefaultContextManager,
    
    # Schema creation and validation
    create_stage1_schema,
    TemporalFilter,
    OperationParams, 
    Stage2AgenticPlan,
    VALID_OPERATIONS,
    
    # Dynamic schema management
    DynamicSchemaManager,
    
    # Base operation classes and results
    ValidationResult,
    OperationResult,
    BaseOperationNode,
    
    # Value grounding
    ValueGroundingResult,
    fuzzy_match_value,
    semantic_match_value,
    ground_filter_value,
    
    # State management
    NLToPythonState,
    
    # Utilities
    setup_module_logger,
    traceable
)

from .nl_to_python_operations import (
    # Operation nodes registry
    OPERATION_NODES,
    
    # Individual operation nodes
    PeriodComparisonNode,
    TimeSeriesNode,
    RankingNode,
    BreakdownNode,
    GroupedAggregationNode,
    WindowFunctionNode,
    PercentileNode,
    CompositionPercentageNode,
    PivotNode,
    
    # Router and combiner
    RoutingPlan,
    OperationRouterNode,
    CombinationResult,
    ResultsCombinerNode,
)

from .nl_to_python_codegen import (
    # Code generators registry
    CODE_GENERATORS,
    
    # Base code generator
    OperationCodeGenerator,
    
    # Individual code generators
    PeriodComparisonCodeGen,
    TimeSeriesCodeGen,
    ComparisonCodeGen,
    PivotCodeGen,
    CompositionPercentageCodeGen,
    BreakdownCodeGen,
    PercentileCodeGen,
    RankingCodeGen,
    WindowFunctionCodeGen,
    GroupedAggregationCodeGen,
    DistributionCodeGen,
    StatisticalTestCodeGen,
    BinningCodeGen,
    DateArithmeticCodeGen,
    FilterCodeGen,
    TemporalFilterCodeGen,
    
    # Helper functions
    get_season,
    calc_percentiles,
)

from .nl_to_python_workflow import (
    # Performance tracking and logging
    LangGraphPerformanceTracker,
    LangGraphStateLogger,
    
    # Main generator class
    NLToPythonGeneratorV5,
    
    # Compatibility aliases
    NLToPythonV4,
    NLToPythonGenerator,
)

# Re-export main classes at package level for easy importing
__all__ = [
    # Main generator classes (most important for backward compatibility)
    'NLToPythonGeneratorV5',
    'NLToPythonGenerator',
    'NLToPythonV4',
    
    # Exception classes
    'UserInputRequiredException',
    'MultiOperationQueryHandler',
    
    # Context and session management
    'DefaultContextManager',
    'DynamicSchemaManager',
    
    # Schema classes
    'create_stage1_schema',
    'TemporalFilter',
    'OperationParams',
    'Stage2AgenticPlan',
    'VALID_OPERATIONS',
    
    # Operation node registry
    'OPERATION_NODES',
    
    # Code generators registry
    'CODE_GENERATORS',
    
    # Base classes
    'BaseOperationNode',
    'OperationCodeGenerator',
    'ValidationResult',
    'OperationResult',
    'ValueGroundingResult',
    
    # State management
    'NLToPythonState',
    
    # Utilities
    'setup_module_logger',
    'traceable',
    
    # Value grounding functions
    'fuzzy_match_value',
    'semantic_match_value', 
    'ground_filter_value',
    
    # Performance tracking
    'LangGraphPerformanceTracker',
    'LangGraphStateLogger',
]

# Package metadata
__version__ = "5.0.0"
__author__ = "Original NL to Python Team"
__description__ = "Natural Language to Python Code Generator - Refactored Modular Architecture"
