"""
Utility Functions for Type Conversion and Data Handling

This module provides universal type conversion utilities that work across
different object types (Pydantic models, dataclasses, dicts, etc.)
"""

from typing import Any, Dict


def to_dict_safe(obj: Any) -> Dict[str, Any]:
    """
    Safely convert any object to dictionary.
    
    This is a universal converter that handles multiple object types:
    - dict: Returns as-is
    - Pydantic v2 models: Uses model_dump()
    - Pydantic v1 models: Uses dict()
    - NamedTuple: Uses _asdict()
    - Dataclass: Uses asdict()
    - Regular objects: Uses __dict__
    - None: Returns empty dict
    
    Args:
        obj: Object to convert (can be any type)
        
    Returns:
        Dictionary representation of object
        
    Examples:
        >>> # Pydantic model
        >>> result = NLToPythonResult(entity='countries', metric='count')
        >>> to_dict_safe(result)
        {'entity': 'countries', 'metric': 'count', ...}
        
        >>> # Already a dict
        >>> to_dict_safe({'key': 'value'})
        {'key': 'value'}
        
        >>> # None
        >>> to_dict_safe(None)
        {}
    """
    # None check
    if obj is None:
        return {}
    
    # Already a dictionary - return as-is
    if isinstance(obj, dict):
        return obj
    
    # Pydantic v2 (uses model_dump)
    if hasattr(obj, 'model_dump') and callable(getattr(obj, 'model_dump')):
        return obj.model_dump()
    
    # Pydantic v1 (uses dict)
    if hasattr(obj, 'dict') and callable(getattr(obj, 'dict')):
        return obj.dict()
    
    # NamedTuple (has _asdict method)
    if hasattr(obj, '_asdict') and callable(getattr(obj, '_asdict')):
        return obj._asdict()
    
    # Dataclass (has __dataclass_fields__ attribute)
    if hasattr(obj, '__dataclass_fields__'):
        try:
            from dataclasses import asdict
            return asdict(obj)
        except (ImportError, TypeError):
            pass
    
    # Regular class with __dict__ attribute
    if hasattr(obj, '__dict__'):
        return obj.__dict__
    
    # Fallback: return empty dict (graceful degradation)
    return {}


def get_nested_value(obj: Any, *keys: str, default: Any = None) -> Any:
    """
    Safely get nested value from object/dict.
    
    Works with both dictionary access and attribute access.
    
    Args:
        obj: Object or dict to get value from
        *keys: Keys to try (will try first match)
        default: Default value if none of the keys found
        
    Returns:
        Value if found, else default
        
    Examples:
        >>> result = {'entity': 'countries'}
        >>> get_nested_value(result, 'entity', 'dimension', default='')
        'countries'
        
        >>> result = NLToPythonResult(dimension='products')
        >>> get_nested_value(result, 'entity', 'dimension', default='')
        'products'
    """
    if obj is None:
        return default
    
    # Try each key in order
    for key in keys:
        # Try dict-style access
        if isinstance(obj, dict) and key in obj:
            value = obj[key]
            if value:  # Return first non-empty value
                return value
        
        # Try attribute access
        elif hasattr(obj, key):
            value = getattr(obj, key, None)
            if value:  # Return first non-empty value
                return value
    
    return default

