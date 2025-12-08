# Layer 0: Query Normalizer with Driver-Aware Rephrasing

## Overview

Layer 0 is a reasoning-based query normalizer that uses GPT-4o mini with constrained decoding to:
- **Reason about driver selection** - validates if driver requirements can be extracted from query
- **Self-validate choices** - only selects drivers whose requirements are present/implied in query
- **Make implicit facts explicit** - converts "trend" → "over time", adds clarifying context
- **Select relevant drivers** from 16 predefined operation types (REQUIRED - not optional)
- **Rephrase queries** to match patterns that already work in the system
- **Fix grammar and spelling** without changing column names (e.g., "emp_sal" stays "emp_sal")
- **Only pass normalized query forward** (driver hints stay internal)

## Architecture

```
User Query: "monthly sales trend"
    ↓
[Layer 0: Query Normalizer]
  GPT-4o mini + Constrained Decoding (instructor/Pydantic)
  
  Internal Processing:
  1. Selects drivers: ["time_series"]
  2. Knows time_series needs: metric + temporal dimension
  3. Rephrases: "sales trend by month"
     (Shows metric + temporal structure for driver consumption)
  
  Output: "sales trend by month"  ← Only this passes forward
    ↓
[Stage 1: Column Identification]
  Sees normalized query
  Maps actual columns from metadata
    ↓
[Stage 2: Operation Selection]
  Independently decides operation based on structure + columns
  (Does NOT see Layer 0's driver hints)
    ↓
[Stage 3: Code Generation]
```

## Key Design Principles

### 1. Reasoning-Based Driver Selection

GPT-4o mini **reasons first** before selecting drivers:

```
Query: "weekly product sales trend"

Step 1: Analyze
- Intent: Show sales changes over weeks
- Present: metric="sales", temporal="weekly", pattern="trend"
- Implied: "trend" means time-based comparison

Step 2: Validate Driver
- time_series needs: metric + temporal
- Can extract metric? YES ("sales")
- Can extract temporal? YES ("weekly")
- Conclusion: time_series is CORRECT

Step 3: Normalize
- Reorder: "weekly" → "by week"
- Output: "product sales trend by week"
```

**Key**: Only selects driver if ALL requirements can be extracted from query.

### 2. Strict Preservation Rules

Layer 0 **ONLY changes structure, NOT content**:

```
✅ CAN DO:
- Reorder words: "weekly sales" → "sales by week"
- Change prepositions: "on" → "by"
- Add structural words: add "by" for clarity
- Fix spelling in regular words (NOT column names)

❌ CANNOT DO:
- Change content words: "trend" MUST stay "trend" (NOT "over time")
- Replace with synonyms: "sales" MUST stay "sales"
- Add content words that weren't in query
- Remove any words from query
```

**Examples**:
```
Input:  "weekly sales trend"
Output: "sales trend by week"
(Only reordered - kept "trend" exactly)

Input:  "top 5 regions on revenue"
Output: "top 5 regions by revenue"
(Only changed preposition "on" → "by")
```

**This helps Stage 1/Stage 2** by making structure clearer without changing meaning.

### 3. Constrained Selection (MUST Choose Driver)
```python
class NormalizedQueryOutput(BaseModel):
    selected_drivers: List[DriverType] = Field(
        ...,
        min_items=1,  # REQUIRED - must select at least 1
        max_items=3
    )
    normalized_query: str
    reasoning: Optional[str]  # Shows why drivers were selected
```

**GPT-4o mini cannot skip driver selection** - it MUST choose 1-3 from the 16 drivers after reasoning.

### 4. Driver Hints Stay Internal
```python
# Layer 0 internally knows:
{
  "selected_drivers": ["time_series"],  # ← Not passed forward
  "normalized_query": "sales trend by month"  # ← Only this passes
}

# Stage 1 receives:
"sales trend by month"  # Clean, structured query
```

**Why?**
- Stage 2 makes final operation decision independently
- Prevents bias from Layer 0's hints
- If Layer 0 is slightly wrong, Stage 2 can correct it

### 5. Preserves Column Names
```python
# ✅ Correct:
"emp_sal by dept_code" → "emp_sal by dept_code"
# Column names preserved exactly

# ❌ Wrong:
"emp_sal by dept_code" → "employee_salary by dept_code"
# DON'T change column names!
```

## The 16 Drivers

| Driver | Requirements | Example |
|--------|-------------|---------|
| `time_series` | metric + temporal dimension | "sales trend by week" |
| `comparison` | metric + 2+ categories/periods | "revenue USA vs India" |
| `top_n` | metric + dimension + N | "top 5 regions by revenue" |
| `bottom_n` | metric + dimension + N | "bottom 3 products by sales" |
| `distribution` | metric + dimension | "orders spread by priority" |
| `aggregation` | metric + aggregation function | "sum of revenue" |
| `filtering` | dimension + condition | "orders where status=open" |
| `ranking` | metric + dimension | "regions ordered by sales" |
| `correlation` | 2+ metrics | "revenue vs profit" |
| `grouping` | metric + dimensions | "sales by region and category" |
| `period_over_period` | metric + temporal + comparison | "sales this month vs last month" |
| `cumulative` | metric + temporal | "running total of sales" |
| `moving_average` | metric + temporal + window | "7-day average sales" |
| `percentage` | metric + dimension | "sales percentage by region" |
| `conditional` | metric + threshold | "orders above 100" |
| `multi_metric` | 2+ metrics + dimension | "revenue and profit by region" |

## Usage

### Integration in Code

```python
from services.layer0_constrained_parser import create_query_normalizer

# In data_exploration.execute_pandas_aggregation_with_codet5():
layer0_normalizer = create_query_normalizer(self.llm_client)
normalized_query = layer0_normalizer.normalize(query)

# Pass normalized_query to nl_to_python
nl_result = self.nl_to_python.generate_python_code(
    query=normalized_query,  # ← Use normalized query
    df_columns=list(df_for_code.columns),
    df_sample=df_for_code
)
```

### Example Flow

**Query**: `"weekly sales week over week"`

**Layer 0 (internal)**:
```json
{
  "selected_drivers": ["period_over_period"],
  "reasoning": "Query mentions 'week over week' comparison of weekly sales",
  "normalized_query": "sales week over week by week"
}
```

**To Stage 1**: `"sales week over week by week"`

**Stage 1**: Identifies columns from metadata
- Maps "sales" to appropriate metric column
- Maps "week" to appropriate temporal column

**Stage 2**: Decides operation
- Sees week-over-week pattern + temporal column
- Independently selects: period_over_period operation

**Stage 3**: Generates code

## Benefits

### Before (Without Layer 0)
```
Query: "monthly sales trend"
↓
Stage 1: Struggles with ambiguous phrasing
↓
Result: ❌ "No date column specified"
```

### After (With Layer 0)
```
Query: "monthly sales trend"
↓
Layer 0: "sales trend by month" (clear structure)
↓
Stage 1: Easily identifies temporal pattern
↓
Result: ✅ Works!
```

## Configuration

### Location
`services/layer0_constrained_parser.py`

### Tunable Parameters
```python
# In Layer0QueryNormalizer.normalize()
model="gpt-4o-mini",     # Fast & cheap
temperature=0.1,         # Low = deterministic
max_tokens=500          # Short output
```

### Driver Definitions
Add/modify drivers in `DRIVER_REQUIREMENTS` dict:
```python
DRIVER_REQUIREMENTS = {
    "new_driver": "Requires: X + Y. Shows Z.",
    # ...
}
```

## Error Handling

### Graceful Fallback
```python
try:
    normalized_query = layer0_normalizer.normalize(query)
except Exception as e:
    logger.warning(f"[LAYER0] Normalization failed: {e}")
    normalized_query = query  # ✅ Use original query
```

**No crashes** - always falls back to original query if Layer 0 fails.

### Common Failure Modes
1. **instructor library missing** → Falls back
2. **LLM timeout** → Falls back
3. **Invalid API key** → Falls back

## Logs

Enable detailed logging:
```python
import logging
logging.getLogger('services.layer0_constrained_parser').setLevel(logging.DEBUG)
```

**Example Output**:
```
[LAYER0] Normalizing query: 'monthly sales trend'
[LAYER0] Selected drivers: ['time_series']
[LAYER0] Reasoning: Query mentions monthly temporal pattern with trend keyword
[LAYER0] Normalized query: 'sales trend by month'
```

## Dependencies

```bash
pip install instructor pydantic openai
```

**Note**: Uses the same LLM client (with SSL bypass) that's already configured across the application.

## Testing

### Unit Test
```python
from services.layer0_constrained_parser import create_query_normalizer

normalizer = create_query_normalizer(llm_client)
result = normalizer.normalize("monthly sales trend")

assert "month" in result.lower()
assert "sales" in result.lower()
assert "trend" in result.lower()
```

### Integration Test
```bash
# Test with Flask server running:
curl -X POST http://localhost:5000/query \
  -d '{"query": "monthly sales trend"}'
  
# Check logs for:
# [LAYER0] Normalized: 'monthly sales trend' → 'sales trend by month'
```

## Performance

**Overhead per query**:
- GPT-4o mini API call: ~200-500ms
- Constrained decoding: ~50ms
- Total: ~300ms average

**Optimization**:
- Uses fast gpt-4o-mini (not gpt-4)
- Low temperature (0.1) for speed
- Small max_tokens (500)

## What Layer 0 Does NOT Do

❌ **Does NOT** identify actual column names (that's Stage 1's job)

❌ **Does NOT** force Stage 2's operation decision (Stage 2 decides independently)

❌ **Does NOT** pass driver hints forward (only normalized_query passes)

❌ **Does NOT** do complex transformations (keeps query simple)

## Future Enhancements

### Potential Additions
1. **Cache common normalizations** - Store frequent query patterns
2. **Multi-language support** - Normalize queries in other languages
3. **Query decomposition** - Split complex multi-part queries
4. **Learning from corrections** - Track when Stage 2 overrides Layer 0

### Not Currently Supported
- ❌ Nested boolean logic
- ❌ Subqueries
- ❌ Custom driver definitions per dataset
- ❌ User-specific query aliases

## Troubleshooting

### Issue: "No module named 'instructor'"

**Solution**:
```bash
pip install instructor
```

### Issue: Layer 0 selects wrong driver

**Check**:
1. Review `DRIVER_REQUIREMENTS` - is description clear?
2. Check logs for `reasoning` field - why did it choose that driver?
3. Remember: Stage 2 can override! Not critical if Layer 0 is slightly wrong.

### Issue: Normalized query looks wrong

**Check**:
1. Look at `selected_drivers` in logs - what drivers were chosen?
2. Review rephrasing rules in system prompt
3. Adjust temperature or add examples to prompt

## Contact

Questions or issues? Check:
1. This README
2. Code: `services/layer0_constrained_parser.py`
3. Integration: `services/data_exploration_no_chart.py` (lines ~1339-1358)
