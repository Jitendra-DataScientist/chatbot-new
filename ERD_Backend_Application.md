# Backend Application - Entity Relationship Diagram (ERD)

## System Overview
**AI-Powered Tableau Analytics Platform** - Flask-based Python backend with LangGraph workflow orchestration for natural language to code generation and advanced analytics.

---

## Core Entities & Relationships

```
┌──────────────────┐         ┌──────────────────┐         ┌──────────────────┐
│  QueryIntent     │────────>│  ChatContext     │<────────│ TableauWorkbook  │
├──────────────────┤  uses   ├──────────────────┤  part of├──────────────────┤
│ primary_intent   │         │ workbook_name    │         │ id               │
│ confidence       │         │ workbook_id      │         │ name             │
│ entities         │         │ selected_worksheet│        │ worksheets[]     │
│ requires_agents[]│         │ available_columns│         │ total_rows       │
│ chart_requirements│        │ user_intent      │         │ total_columns    │
└──────────────────┘         │ previous_analysis│         └──────────────────┘
                             └──────────────────┘                   │
        │                            │                              │
        │                            │                              │ contains
        ▼                            ▼                              ▼
┌──────────────────┐         ┌──────────────────┐         ┌──────────────────┐
│EnhancedChatRequest│        │EnhancedChatResponse│       │TableauWorksheet  │
├──────────────────┤         ├──────────────────┤         ├──────────────────┤
│ message          │────────>│ reply            │         │ id               │
│ context          │ produces│ intent           │         │ name             │
│ tableau_context  │         │ auto_analysis    │         │ column_names[]   │
│ connection_key   │         │ visualization    │         │ data_types{}     │
│ selected_chart   │         │ python_code[]    │         │ data_shape       │
└──────────────────┘         │ execution_time   │         │ has_data         │
                             └──────────────────┘         └──────────────────┘
                                      │                            │
                                      │ contains                   │
                                      ▼                            │ described by
                             ┌──────────────────┐                 ▼
                             │AutoAnalysisResult│         ┌──────────────────┐
                             ├──────────────────┤         │  ChartMetadata   │
                             │impactful_columns │         ├──────────────────┤
                             │data_aggregation  │         │ worksheet_name   │
                             │statistical_analysis│       │ x_axis[]         │
                             │summary_lines[]   │         │ y_axis[]         │
                             │execution_time    │         │ filters[]        │
                             └──────────────────┘         │ dimensions[]     │
                                                          │ measures[]       │
                                                          │ calculated_fields│
        ┌──────────────────┐                             └──────────────────┘
        │NLToPythonResult  │                                     │
        ├──────────────────┤                                     │
        │ original_query   │                                     │ uses
        │ generated_code   │                                     ▼
        │ operation_type   │                             ┌──────────────────┐
        │ confidence       │                             │  FieldMetadata   │
        │ expected_columns │                             ├──────────────────┤
        │ suggested_chart  │                             │ name             │
        │ group_by_columns │                             │ data_type        │
        └──────────────────┘                             │ role (dim/measure)│
                │                                        │ aggregation      │
                │ contains                               │ calculation      │
                ▼                                        └──────────────────┘
        ┌──────────────────┐
        │ PandasOperation  │         ┌──────────────────────────┐
        ├──────────────────┤         │   VectorSearchResult     │
        │ operation_type   │         ├──────────────────────────┤
        │ code             │         │ chunk_id                 │
        │ description      │         │ similarity_score         │
        │ expected_output  │         │ data_summary             │
        └──────────────────┘         │ metadata                 │
                                     └──────────────────────────┘
                                                │
                                                │ retrieved from
                                                ▼
                                     ┌──────────────────────────┐
                                     │      DataChunk           │
                                     ├──────────────────────────┤
                                     │ chunk_id                 │
                                     │ workbook_id              │
                                     │ worksheet_name           │
                                     │ data_summary             │
                                     │ column_info              │
                                     │ embedding[]              │
                                     └──────────────────────────┘
```

---

## Architecture Components

### Data Flow: Query → Code → Execution
```
User Query → QueryIntent → NLToPythonResult → PandasOperation → AutoAnalysisResult → Response
```

### LangGraph Workflow Stages
1. **Stage 1**: Column Selection (strict Pydantic schema)
2. **Stage 1.5**: Value Grounding (fuzzy matching)
3. **Stage 2**: Operation Planning (9 operation types)
4. **Code Generation**: Pandas/Polars code execution

---

## Technology Stack

### Core Framework
- **Flask 3.1.2** - Web framework
- **Python 3.x** - Backend language

### AI/ML & Orchestration
- **OpenAI 2.2.0** - GPT integration (primary LLM)
- **LangChain-Core 1.1.1** - LLM framework
- **LangGraph 1.0.2** - Workflow orchestration (CRITICAL)
- **LangSmith 0.4.55** - Observability

### Data Processing
- **Pandas 2.3.3** - Data manipulation
- **Polars 1.35.2** - Fast DataFrame operations
- **NumPy 1.26.4** - Numerical computing
- **SciPy 1.15.3** - Statistical analysis
- **Scikit-learn 1.6.1** - Machine learning

### Data Storage
- **PyArrow 22.0.0** - Parquet format (70MB cache)
- **Pydantic 2.11.10** - Data validation & schemas
- **FAISS** - Vector similarity search (RAG)

### Tableau Integration
- **TableauHyperAPI 0.0.23576** - Hyper file access
- **Tableau REST API v3.19** - Server communication

### Text Processing
- **FuzzyWuzzy 0.18.0** - Fuzzy matching
- **RapidFuzz 3.14.1** - Fast string matching

### Utilities
- **DiskCache 5.6.3** - Disk-based caching
- **Requests 2.32.5** - HTTP client
- **Python-dateutil 2.9.0** - Date/time handling

---

## Data Storage Strategy

### In-Memory
- Session states (Flask session)
- 5-query conversation history per session
- Disambiguation cache

### File-Based
- **Parquet**: `data_cache/default.parquet` (70MB data cache)
- **JSON**: Config files (tableau_config.json, chart_column_mappings.json)
- **Pickle**: Authentication tokens

### Vector Database
- **FAISS**: Workbook embeddings for similarity search

**Note**: No traditional SQL/NoSQL database - hybrid file + in-memory approach.

---

## Key Features

1. **Multi-turn Conversation**: 5-query memory with follow-up detection
2. **Smart Aggregation**: LLM-based aggregation decisions with caching
3. **Type Safety**: Pydantic schemas for all data models
4. **Parallel Operations**: LangGraph enables parallel query execution
5. **Disambiguation**: Interactive user clarification when ambiguous
6. **Chart Generation**: Matplotlib, Plotly, Seaborn visualizations
7. **Context-Aware**: Temporal detection, entity extraction

---

**Total Backend Code**: 45,000+ lines Python | **Main App**: app.py (5,091 lines), tableau_backend.py (271,232 lines)
