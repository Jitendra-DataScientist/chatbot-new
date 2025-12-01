"""
CodeT5 Service for Pandas Code Generation (Gemma3 API)

Uses the Gemma3 API to generate pandas code from natural language queries 
with structured input format. Previously used local CodeT5 model.

Class name and interface kept for backward compatibility.
Based on reference_code/df_agent.ipynb
"""

import os
import sys
import torch
import pandas as pd
import requests
import json
from typing import Dict, Any, Optional
from transformers import AutoTokenizer, AutoModelForSeq2SeqLM
from dotenv import load_dotenv
import re

# Add parent directory to path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from master_logger import setup_module_logger


class CodeT5Service:
    """
    Service for generating pandas code using Gemma3 API.
    
    Input format sent to API:
    {
        "query": "Instruction: ... Query: ... Schema: ... Sub-category: ... Period: ..."
    }
    
    Response format from API:
    {
        "answer": "generated pandas code"
    }
    """
    
    def __init__(self, model_path: str, smart_aggregation_decider, period_extractor):
        """
        Initialize CodeT5 service.
        
        Args:
            model_path: Path to CodeT5 model directory (kept for backward compatibility, not used)
            smart_aggregation_decider: SmartAggregationDecider instance for column aggregation decisions
            period_extractor: PeriodExtractionService instance for temporal extraction
        """
        self.logger = setup_module_logger('services.codet5_service')
        self.model_path = model_path
        self.smart_aggregation_decider = smart_aggregation_decider
        self.period_extractor = period_extractor
        
        self.logger.info("=" * 80)
        self.logger.info("INITIALIZING CODET5 SERVICE (GEMMA3 API)")
        self.logger.info("=" * 80)
        
        try:
            # Load environment variables
            load_dotenv()
            
            # Get API URL from environment
            self.api_url = os.getenv("codet5url")
            if not self.api_url:
                raise ValueError("Environment variable 'codet5url' is not set")
            
            self.logger.info(f"Using Gemma3 API endpoint: {self.api_url}")
            self.logger.info("✓ CodeT5 service initialized successfully with Gemma3 API")
            self.logger.info("=" * 80)
            
        except Exception as e:
            self.logger.error(f"Failed to initialize CodeT5 service: {e}", exc_info=True)
            raise
    
    def _build_schema(self, df: pd.DataFrame, query: str, chart_context: Optional[Dict] = None) -> str:
        """
        Build schema string with unique values for each column.
        
        Format: column_name: type[AGG] (unique values: val1, val2, val3, ...)
        
        Args:
            df: DataFrame with columns to include in schema (already coerced)
            query: User query (for smart aggregation context)
            chart_context: Chart context containing domain_type
            
        Returns:
            Schema string with columns and their unique values
        """
        self.logger.debug(f"[SCHEMA_BUILD] Building schema for {len(df.columns)} columns")
        
        # Get domain type from chart context (for logging only)
        domain_type = "General"
        if chart_context and 'domain_type' in chart_context:
            domain_type = chart_context['domain_type']
            self.logger.info(f"[SCHEMA_BUILD] Using domain_type from chart_context: '{domain_type}'")
        else:
            self.logger.warning(f"[SCHEMA_BUILD] No domain_type in chart_context, using default: '{domain_type}'")
        
        # Build column schema parts with unique values
        schema_parts = []
        for col in df.columns:
            try:
                dtype = df[col].dtype
                
                # Get unique values (limit to 100)
                unique_vals = df[col].unique()
                unique_vals_list = [str(val) for val in unique_vals[:100]]
                unique_vals_str = ', '.join(unique_vals_list)
                
                # Add suffix if truncated
                truncation_suffix = " (limited to first 100)" if len(unique_vals) > 100 else ""
                
                # Determine if numeric or categorical
                if pd.api.types.is_numeric_dtype(dtype):
                    # Use smart aggregation to decide aggregation type
                    if self.smart_aggregation_decider:
                        try:
                            decision = self.smart_aggregation_decider.decide_aggregation(
                                query=query,
                                column=col,
                                df=df
                            )
                            agg_type = decision['aggregation']
                            self.logger.debug(f"[SCHEMA_BUILD] Column '{col}': numeric[{agg_type}] (confidence: {decision.get('confidence', 'N/A')})")
                            schema_parts.append(f"{col}: numeric[{agg_type}] (unique values: {unique_vals_str}{truncation_suffix})")
                        except Exception as e:
                            self.logger.warning(f"[SCHEMA_BUILD] Smart aggregation failed for '{col}', defaulting to SUM: {e}")
                            schema_parts.append(f"{col}: numeric[SUM] (unique values: {unique_vals_str}{truncation_suffix})")
                    else:
                        # Fallback if smart aggregation not available
                        self.logger.debug(f"[SCHEMA_BUILD] Column '{col}': numeric[SUM] (no smart aggregation)")
                        schema_parts.append(f"{col}: numeric[SUM] (unique values: {unique_vals_str}{truncation_suffix})")
                else:
                    # Categorical column
                    self.logger.debug(f"[SCHEMA_BUILD] Column '{col}': categorical")
                    schema_parts.append(f"{col}: categorical (unique values: {unique_vals_str}{truncation_suffix})")
                    
            except Exception as e:
                self.logger.warning(f"[SCHEMA_BUILD] Error processing column '{col}': {e}, treating as categorical")
                schema_parts.append(f"{col}: categorical")
        
        # Build final schema: each column on a new line
        schema = '\n'.join(schema_parts)
        
        self.logger.info(f"[SCHEMA_BUILD] Generated schema with unique values ({len(schema_parts)} columns)")
        
        return schema
    
    def _extract_period(self, query: str) -> str:
        """
        Extract temporal period(s) from query using period extraction service.
        
        Args:
            query: User's natural language query
            
        Returns:
            Period string - single period (e.g., "last month") or comma-separated 
            multiple periods (e.g., "january, february"), or empty string if none found
        """
        self.logger.debug(f"[PERIOD_EXTRACT] Extracting period from query: '{query}'")
        
        try:
            # Use period extraction service
            result = self.period_extractor.extract_periods_and_events(query, apply_cleanup=True)
            
            if result.get('has_period', False) and result.get('periods'):
                # Collect all valid periods
                valid_periods = []
                for period_entity in result['periods']:
                    period_text = period_entity.get('word', '').strip().lower()
                    confidence = period_entity.get('score', 0)
                    
                    self.logger.info(f"[PERIOD_EXTRACT] Found period candidate: '{period_text}' (confidence: {confidence:.2f})")
                    
                    # Validate if this is actually a temporal period
                    if self._is_valid_period(period_text, confidence):
                        self.logger.info(f"[PERIOD_EXTRACT] ✓ Valid period: '{period_text}'")
                        valid_periods.append(period_text)
                    else:
                        self.logger.warning(f"[PERIOD_EXTRACT] ✗ Invalid period rejected: '{period_text}'")
                
                # Join all valid periods with commas
                if valid_periods:
                    combined_period = ", ".join(valid_periods)
                    self.logger.info(f"[PERIOD_EXTRACT] Final combined period: '{combined_period}'")
                    return combined_period
                else:
                    self.logger.debug(f"[PERIOD_EXTRACT] No valid periods found in query")
                    return ""
            else:
                self.logger.debug(f"[PERIOD_EXTRACT] No period found in query")
                return ""
                
        except Exception as e:
            self.logger.warning(f"[PERIOD_EXTRACT] Period extraction failed: {e}")
            return ""
    
    def _is_valid_period(self, period_text: str, confidence: float) -> bool:
        """
        Validate if extracted period is a genuine temporal period.
        
        Args:
            period_text: Extracted period text
            confidence: Confidence score from BERT model
            
        Returns:
            True if valid temporal period, False otherwise
        """
        if not period_text:
            return False
        
        # List of invalid/noise words that are not temporal periods
        invalid_keywords = {
            'wise', 'by', 'of', 'the', 'a', 'an', 'is', 'are', 
            'and', 'or', 'in', 'on', 'at', 'to', 'for', 'with'
        }
        
        # Check if it's just noise
        if period_text in invalid_keywords:
            self.logger.debug(f"[PERIOD_VALIDATION] Rejected: '{period_text}' is a noise word")
            return False
        
        # List of valid temporal keywords that indicate real periods
        valid_temporal_keywords = {
            # Time units
            'day', 'days', 'week', 'weeks', 'month', 'months', 
            'quarter', 'quarters', 'year', 'years',
            'daily', 'weekly', 'monthly', 'quarterly', 'yearly', 'annual',
            
            # Relative time
            'today', 'yesterday', 'tomorrow',
            'last', 'previous', 'next', 'current', 'this',
            'ago', 'past', 'recent',
            
            # Specific periods
            'q1', 'q2', 'q3', 'q4',
            'january', 'february', 'march', 'april', 'may', 'june',
            'july', 'august', 'september', 'october', 'november', 'december',
            'jan', 'feb', 'mar', 'apr', 'may', 'jun', 
            'jul', 'aug', 'sep', 'oct', 'nov', 'dec',
            'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday',
            'mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun',
            
            # Date formats (check if contains digits)
            '2020', '2021', '2022', '2023', '2024', '2025'  # years
        }
        
        # Check if period contains any valid temporal keyword
        period_lower = period_text.lower()
        for keyword in valid_temporal_keywords:
            if keyword in period_lower:
                self.logger.debug(f"[PERIOD_VALIDATION] Accepted: '{period_text}' contains valid temporal keyword '{keyword}'")
                return True
        
        # Check if it contains digits (likely a date/year)
        if any(char.isdigit() for char in period_text):
            self.logger.debug(f"[PERIOD_VALIDATION] Accepted: '{period_text}' contains digits (likely date/year)")
            return True
        
        # If confidence is very high (>0.9) and longer than 3 chars, might be valid
        if confidence > 0.9 and len(period_text) > 3:
            self.logger.debug(f"[PERIOD_VALIDATION] Accepted: '{period_text}' has high confidence ({confidence:.2f})")
            return True
        
        # Reject if none of the above
        self.logger.debug(f"[PERIOD_VALIDATION] Rejected: '{period_text}' doesn't match any temporal patterns")
        return False
    
    def _format_input(self, schema: str, sub_category: str, query: str, period: str) -> str:
        """
        Format the input string for CodeT5 model with instruction-based format.
        
        Format:
        Instruction: (guidelines)
        Query: user query text
        Schema: columns with unique values...
        Sub-category: category_name
        Period: period_text (if present)
        
        Args:
            schema: Schema string from _build_schema (with unique values)
            sub_category: Intent sub-category from BERT model
            query: Original user query
            period: Period string from _extract_period
            
        Returns:
            Formatted input string for CodeT5
        """
        # Build instruction section
        instruction = """Instruction:
Generate raw Python codes using pandas to compute the requested result directly from a DataFrame named `df`. The output should only contain python code.
The code should:
- Only use columns provided in the schema.
- Use only the columns required to generate the output.
- Have no import statements or functions, just groupby code or cascade operations if necessary.
- Be concise (no explanations or comments).
- Separate multiple codes with a newline character.
- use the unique values in the schema to generate the output.
- the datetime, time, date, etc. columns are in string format, they should be converted to datetime objects only and only when needed, do not convert them to datetime objects unless absolutely necessary."""
        
        # Build input parts
        input_parts = [
            instruction,
            f"Query: {query}",
            f"Schema:\n{schema}",
            f"Sub-category: {sub_category}"
        ]
        
        # Add period only if it exists
        if period:
            input_parts.append(f"Period: {period}")
        
        input_string = "\n\n".join(input_parts)
        
        self.logger.debug(f"[INPUT_FORMAT] Formatted input:\n{input_string}")
        
        return input_string
    
    def generate_code(self, query: str, df: pd.DataFrame, 
                     intent_result=None, chart_context: Optional[Dict] = None) -> str:
        """
        Generate pandas code using CodeT5 model.
        
        Args:
            query: User's natural language query
            df: DataFrame to analyze (for schema building)
            intent_result: Intent classification result (contains primary_intent for sub-category)
            chart_context: Chart context (contains domain_type)
            
        Returns:
            Generated pandas code as string
        """
        self.logger.info("=" * 80)
        self.logger.info("[CODET5_GENERATION] Starting code generation")
        self.logger.info(f"[CODET5_GENERATION] Query: '{query}'")
        self.logger.info(f"[CODET5_GENERATION] DataFrame shape: {df.shape}")
        
        try:
            # Extract sub-category from intent_result (use BERT subcategory, not primary_intent)
            if intent_result:
                # Try to get the actual BERT subcategory from entities
                if hasattr(intent_result, 'entities') and isinstance(intent_result.entities, dict):
                    sub_category = intent_result.entities.get('sub_category', 'general')
                    self.logger.info(f"[CODET5_GENERATION] Using BERT sub-category: '{sub_category}'")
                elif isinstance(intent_result, dict) and 'entities' in intent_result:
                    sub_category = intent_result['entities'].get('sub_category', 'general')
                    self.logger.info(f"[CODET5_GENERATION] Using BERT sub-category: '{sub_category}'")
                else:
                    sub_category = "general"
                    self.logger.warning(f"[CODET5_GENERATION] Could not extract BERT subcategory, using default: '{sub_category}'")
            else:
                sub_category = "general"
                self.logger.warning(f"[CODET5_GENERATION] No intent_result provided, using default sub-category: '{sub_category}'")
            
            # Build schema (without domain type wrapper)
            schema = self._build_schema(df, query, chart_context)
            
            # Extract period
            period = self._extract_period(query)
            print ("------period-------")
            print (period)
            print ("--------------------------------")
            # Format input for model
            input_string = self._format_input(schema, sub_category, query, period)
            print ("------input_string-------")
            print (input_string)
            print ("--------------------------------")
            
            # Prepare API payload
            payload = {
                "query": input_string
            }
            
            self.logger.info(f"[CODET5_GENERATION] API payload prepared:")
            self.logger.info(f"  - query length: {len(payload['query'])} chars")
            
            # Make API request to Gemma3
            self.logger.info("[CODET5_GENERATION] Calling Gemma3 API...")
            try:
                response = requests.post(self.api_url, json=payload)
                response.raise_for_status()  # Raise exception for bad status codes
                
                # Parse response
                response_data = response.json()
                generated_code = response_data.get("answer", "")
                generated_code = re.sub(r"^```python\s*\n?|```$", "", generated_code.strip())
                if not generated_code:
                    raise ValueError("API response does not contain 'answer' field")
                
                self.logger.info(f"[CODET5_GENERATION] ✓ Code generated successfully via API")
                self.logger.info(f"[CODET5_GENERATION] Generated code: {generated_code}")
                
                # Post-process the generated code to fix common issues
                processed_code = self._post_process_code(generated_code)
                
                self.logger.info("=" * 80)
                
                return processed_code
                
            except requests.exceptions.RequestException as e:
                self.logger.error(f"[CODET5_GENERATION] API request failed: {e}", exc_info=True)
                raise Exception(f"Gemma3 API request failed: {e}")
            except (json.JSONDecodeError, KeyError, ValueError) as e:
                self.logger.error(f"[CODET5_GENERATION] Failed to parse API response: {e}", exc_info=True)
                raise Exception(f"Failed to parse Gemma3 API response: {e}")
            
        except Exception as e:
            self.logger.error(f"[CODET5_GENERATION] Code generation failed: {e}", exc_info=True)
            self.logger.error("=" * 80)
            raise
    
    def _post_process_code(self, code: str) -> str:
        """
        Post-process generated code to fix common issues.
        
        Currently:
        - Injects errors='coerce' into pd.to_datetime() calls to handle NULL values
        
        Args:
            code: Generated pandas code
            
        Returns:
            Post-processed code
        """
        import re
        
        # Find all pd.to_datetime calls and fix them
        # We need to handle nested parentheses properly
        processed_code = code
        modified = False
        
        # Find all occurrences of pd.to_datetime
        pattern = r'pd\.to_datetime\('
        matches = list(re.finditer(pattern, code))
        
        # Process matches in reverse order to avoid offset issues
        for match in reversed(matches):
            start_pos = match.start()
            paren_start = match.end() - 1  # Position of opening '('
            
            # Find the matching closing parenthesis
            paren_count = 0
            end_pos = None
            for i in range(paren_start, len(code)):
                if code[i] == '(':
                    paren_count += 1
                elif code[i] == ')':
                    paren_count -= 1
                    if paren_count == 0:
                        end_pos = i
                        break
            
            if end_pos is None:
                continue  # Malformed code, skip
            
            # Extract the full pd.to_datetime(...) call
            full_call = code[start_pos:end_pos + 1]
            
            # Check if errors= parameter is already present
            if 'errors=' in full_call:
                continue  # Already has errors parameter
            
            # Inject errors='coerce' before the closing parenthesis
            args_section = code[paren_start + 1:end_pos]
            
            # Check if there are already arguments
            if args_section.strip():
                # Add comma and errors='coerce'
                new_call = f"pd.to_datetime({args_section}, errors='coerce')"
            else:
                # No arguments (shouldn't happen, but handle it)
                new_call = f"pd.to_datetime(errors='coerce')"
            
            # Replace in the code
            processed_code = processed_code[:start_pos] + new_call + processed_code[end_pos + 1:]
            modified = True
        
        if modified:
            self.logger.info(f"[POST_PROCESS] Injected errors='coerce' into pd.to_datetime() calls")
            self.logger.debug(f"[POST_PROCESS] Before: {code}")
            self.logger.debug(f"[POST_PROCESS] After: {processed_code}")
        
        return processed_code


