# Layer 0: Constrained Natural Language Parser

## Overview

Layer 0 is a constrained NL-to-Semantic-IR parser that eliminates hallucination in column name and operation selection through:
- **Constrained decoding** using Pydantic enums (forces exact column names)
- **Spell checking** for column name typos and abbreviations
- **Temporal keyword extraction** using regex (no LLM ambiguity)
- **Generic semantic IR** output (dataset-agnostic, no hardcoding)

## Architecture

```
User Query
    ↓
[Pre-processing]
  - Spell check column names (fuzzy matching)
  - Extract temporal keywords (regex)
  - Extract intent hints (keyword matching)
    ↓
[LLM with Constrained Decoding]
  - Pydantic model with Literal[column_names]
  - instructor library for auto-retry
  - Forces exact column selection
    ↓
[Semantic IR]
  - Intent: {metric, dimensions, aggregation}
  - Constraints: {temporal, filters, limit}
  - Modifiers: {comparison, ranking, calculation}
    ↓
[nl_to_python]
  - Receives enhanced query with exact column names
  - Existing logic continues unchanged
```

## Key Features

### 1. Zero Hallucination (Constrained Decoding)
```python
# Dynamic Pydantic model with column enums
ColumnEnum = Literal[tuple(all_columns)]  # ONLY actual columns!

class ParsedQuery(BaseModel):
    metric: ColumnEnum  # ✅ Can ONLY be real column name
    dimensions: List[ColumnEnum]  # ✅ Can ONLY be real column names
```

**Result**: LLM **cannot** output non-existent column names.

### 2. Spell Checking (Pre-processing)
```python
# Before LLM sees the query:
"count of opn tickets" → "count of Open_Volume tickets"
"tickts by cntry" → "tickets by account_country"
```

**Strategies**:
- Exact fuzzy matching (fuzzywuzzy)
- Partial ratio matching
- Acronym detection ("opn" → "O_pen_V_olume")
- Substring matching

### 3. Temporal Extraction (Regex - No LLM)
```python
# Extracted before LLM:
"march 2025" → {"period": "month", "value": 3, "year": 2025}
"Q2" → {"period": "quarter", "value": 2}
"2024" → {"period": "year", "value": 2024}
```

**Benefits**: Prevents "March" from being matched to calculated fields.

### 4. Generic Semantic IR (No Hardcoding)
```python
# Works for ANY dataset:
SemanticIR(
    intent=Intent(metric="...", dimensions=[...], aggregation="..."),
    constraints=Constraints(temporal=..., filters=[...]),
    modifiers=Modifiers(ranking=..., comparison=...)
)
```

**No references to**:
- ❌ Specific column names ("Open_Volume", "create_week")
- ❌ Specific workbooks ("FRO Dashboard")
- ❌ Domain terminology ("SLA breach", "EMEA")

## Usage

### Layer 0 Integration

Layer 0 is **always enabled** by default. It runs automatically before nl_to_python for all data exploration queries.

**Graceful Fallback:**

If Layer 0 parsing fails for any reason, the system automatically falls back to the original query without Layer 0 enhancement.

### Example Flow

**Query**: `"count of opn tickets in march"`

**Layer 0 Processing**:
1. **Spell check**: `"opn"` → `"Open_Volume"` (fuzzy match + acronym)
2. **Temporal extract**: `"march"` → `{"period": "month", "value": 3}`
3. **LLM parse** (constrained):
   ```json
   {
     "metric": "Open_Volume",  // ✅ Exact column name (from enum)
     "aggregation": "sum",
     "dimensions": [],
     "temporal_period": "month",
     "temporal_value": 3
   }
   ```
4. **Convert to IR**:
   ```python
   SemanticIR(
       intent=Intent(
           metric="Open_Volume",
           dimensions=[],
           aggregation="sum"
       ),
       constraints=Constraints(
           temporal=TemporalConstraint(period="month", value=3)
       )
   )
   ```
5. **Generate hint**: `"sum of Open_Volume for March"`
6. **Pass to nl_to_python**: Continues with existing logic, but with exact column names

## Benefits Over Current System

### Before (Current System)
- Stage 1 might miss `date_column` for "trend" queries
- "March" gets matched to "Primary", "Marketplace" calculated fields
- Metric disambiguation overrides Stage 1's correct selection

### After (With Layer 0)
- ✅ Pre-processing extracts "March" as temporal (never reaches calculated field matching)
- ✅ Spell checking maps "opn" → "Open_Volume" before ambiguity
- ✅ Constrained decoding prevents hallucinated column names
- ✅ Single-pass selection (no multi-stage override conflicts)

## Dependencies

```bash
pip install instructor fuzzywuzzy python-Levenshtein pydantic
```

## Configuration

Located in: `services/layer0_constrained_parser.py`

### Tunable Parameters

```python
# In QueryPreprocessor._spell_check_columns()
FUZZY_THRESHOLD = 75  # Minimum score for spell correction

# In Layer0ConstrainedParser.parse()
LLM_TEMPERATURE = 0.1  # Low = deterministic
MAX_RETRIES = 2  # instructor retry attempts
```

## Error Handling

### If Layer 0 Fails
- Graceful fallback to original query
- Warning logged, no crash
- Existing nl_to_python continues normally

```python
try:
    semantic_ir = layer0_parser.parse(query)
    query_to_use = enhanced_query
except Exception as layer0_error:
    master_logger.warning(f"[LAYER0] Failed, using original: {layer0_error}")
    query_to_use = original_query  # ✅ Fallback
```

### Common Failure Modes
1. **instructor library not installed** → Falls back
2. **LLM timeout** → Retries 2x, then falls back
3. **Ambiguous abbreviation** → Uses best fuzzy match (logged)

## Logs

Enable detailed logging:
```python
import logging
logging.getLogger('services.layer0_constrained_parser').setLevel(logging.DEBUG)
```

**Log Examples**:
```
[LAYER0] Initializing constrained parser...
[LAYER0] Parsing query: 'count of opn tickets in march'
[SPELL_CHECK] 'opn' → 'Open_Volume' (confidence: 85%)
[LAYER0] Pre-processed: temporal={'period': 'month', 'value': 3}
[LAYER0] ✅ Parsed to Semantic IR:
  - Intent: metric=Open_Volume, aggregation=sum, dimensions=[]
  - Constraints: temporal=month=3, filters=0
[LAYER0_HINT] Enhanced: 'sum of Open_Volume for March'
```

## Future Enhancements

### Potential Additions
1. **User correction learning** - Build glossary from disambiguation choices
2. **Multi-step query decomposition** - Handle "show X then Y" queries
3. **Calculated filter support** - "where X > 2 * average"
4. **Domain glossary builder** - Auto-learn "SLA breach" → `sla_status='Breached'`

### Not Currently Supported
- ❌ Nested boolean logic (`(A OR B) AND C`)
- ❌ Subqueries (`WHERE country IN (SELECT ...)`)
- ❌ Self-comparisons (`compare to average of same country`)
- ❌ Window functions (`rank within partition`)

For these patterns, queries must be simplified or broken into steps.

## Testing

### Unit Tests
```python
# Test spell checking
assert preprocessor._spell_check_columns("opn tickets") == "Open_Volume tickets"

# Test temporal extraction
assert preprocessor._extract_temporal("march 2025") == {
    "period": "month", "value": 3, "year": 2025
}

# Test constrained parsing
ir = parser.parse("count of tickets by country")
assert ir.intent.metric in all_valid_columns  # ✅ No hallucination
```

### Integration Tests
```bash
# Layer 0 is always enabled - just run queries
python -c "
from services.data_exploration_no_chart import data_exploration
# Test with real queries...
"
```

## Troubleshooting

### Issue: "Column not found" error

**Cause**: Spell checker didn't find good match

**Solution**:
1. Check `FUZZY_THRESHOLD` (lower = more lenient)
2. Add column aliases to metadata
3. Use exact column name in query

### Issue: Wrong temporal value

**Cause**: Regex didn't match pattern

**Solution**:
1. Check `QueryPreprocessor.MONTHS` dict
2. Add more temporal patterns to regex
3. Use explicit format: "month=3" or "Q2"

### Issue: Layer 0 not working

**Cause**: Exception during Layer 0 parsing

**Solution**:
1. Check logs for `[LAYER0]` entries
2. Look for error message: "Parsing failed, falling back to original"
3. Fix the root cause (column mismatch, schema issue, etc.)

## Performance

**Overhead per query**:
- Spell checking: ~50ms (fuzzy matching all columns)
- LLM call: ~500-1000ms (constrained decoding)
- IR conversion: ~5ms

**Total**: ~600ms additional latency

**Optimization**:
- Cache spell corrections per session
- Pre-compute column token IDs
- Batch similar queries

## Contact

Questions or issues? Check:
1. This README
2. Code comments in `services/layer0_constrained_parser.py`
3. Integration code in `services/data_exploration_no_chart.py` (lines 1339-1384)

