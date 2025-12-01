"""
Backward compatibility wrapper for the refactored NL to Python module.

This file maintains the same interface as the original NL_to_python.py
while using the new modular architecture from the nlp_to_python package.
"""

# Import everything from the new modular package
from services.nlp_to_python import *

# Maintain the exact same interface as before
# All classes, functions, and constants are now available through the imports above

# For absolute backward compatibility, we can also do explicit re-exports:
from services.nlp_to_python import (
    NLToPythonGeneratorV5 as NLToPythonGeneratorV5,
    NLToPythonGenerator as NLToPythonGenerator,
    NLToPythonV4 as NLToPythonV4,
    DefaultContextManager as DefaultContextManager,
    create_stage1_schema as create_stage1_schema,
    UserInputRequiredException as UserInputRequiredException,
    # Add other key exports as needed
)

# If there were any module-level functions or constants in the original file,
# they would be re-exported here as well.
