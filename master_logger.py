"""
Master Logger Configuration for Tableau Analytics Assistant
Provides centralized logging capabilities for all Python modules
"""

import logging
import logging.handlers
import os
import sys
import json
import traceback
import threading
import time
from datetime import datetime
from typing import Any, Dict, Optional, Union
from functools import wraps
from pathlib import Path

class MasterLogger:
    """
    Centralized logging system that ensures all logs go to the master log file
    """
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super(MasterLogger, cls).__new__(cls)
        return cls._instance
    
    def __init__(self):
        if hasattr(self, '_initialized'):
            return
        
        self._initialized = True
        self.master_log_file = "master_debug.log"
        self.setup_master_logger()
        
    def setup_master_logger(self):
        """Setup the master logger with comprehensive configuration"""
        
        # Create master logger
        self.master_logger = logging.getLogger('master_logger')
        self.master_logger.setLevel(logging.DEBUG)
        
        # Clear any existing handlers to avoid duplicates
        self.master_logger.handlers.clear()
        
        # Create formatter with detailed information
        detailed_formatter = logging.Formatter(
            fmt='%(asctime)s | %(levelname)-8s | %(name)-25s | %(filename)-20s:%(lineno)-4d | %(funcName)-20s | %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        
        # File handler for master log
        file_handler = logging.handlers.RotatingFileHandler(
            self.master_log_file,
            maxBytes=50 * 1024 * 1024,  # 50MB
            backupCount=5,
            encoding='utf-8'
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(detailed_formatter)
        
        # Console handler for immediate feedback
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(detailed_formatter)
        
        # Add handlers
        self.master_logger.addHandler(file_handler)
        self.master_logger.addHandler(console_handler)
        
        # Log the initialization
        self.master_logger.info("="*100)
        self.master_logger.info("MASTER LOGGER INITIALIZED - Tableau Analytics Assistant Logging System")
        self.master_logger.info("="*100)
        self.master_logger.info(f"Master log file: {os.path.abspath(self.master_log_file)}")
        self.master_logger.info(f"Process ID: {os.getpid()}")
        self.master_logger.info(f"Thread ID: {threading.current_thread().ident}")
        
    def get_logger(self, name: str) -> logging.Logger:
        """
        Get a logger for a specific module/component
        All logs will flow to the master log file
        """
        logger = logging.getLogger(name)
        logger.setLevel(logging.DEBUG)
        
        # Clear existing handlers to avoid duplicates
        logger.handlers.clear()
        
        # Create a handler that writes to master log
        master_handler = logging.StreamHandler()
        master_handler.emit = lambda record: self.master_logger.handle(record)
        
        logger.addHandler(master_handler)
        logger.propagate = False
        
        return logger
    
    def log_function_entry(self, logger: logging.Logger, func_name: str, args: tuple = None, kwargs: dict = None):
        """Log function entry with parameters"""
        params = []
        if args:
            params.extend([f"arg{i}={repr(arg)[:100]}" for i, arg in enumerate(args)])
        if kwargs:
            params.extend([f"{k}={repr(v)[:100]}" for k, v in kwargs.items()])
        
        param_str = ", ".join(params) if params else "no parameters"
        logger.debug(f"ENTRY: {func_name}({param_str})")
    
    def log_function_exit(self, logger: logging.Logger, func_name: str, result: Any = None, execution_time: float = None):
        """Log function exit with result and timing"""
        result_str = f"result={repr(result)[:200]}" if result is not None else "no return value"
        time_str = f", execution_time={execution_time:.4f}s" if execution_time else ""
        logger.debug(f"EXIT: {func_name}({result_str}{time_str})")
    
    def log_exception(self, logger: logging.Logger, func_name: str, exception: Exception):
        """Log exception with full traceback"""
        logger.error(f"EXCEPTION in {func_name}: {type(exception).__name__}: {str(exception)}")
        logger.error(f"TRACEBACK:\n{traceback.format_exc()}")
    
    def log_performance(self, logger: logging.Logger, operation: str, duration: float, **metrics):
        """Log performance metrics"""
        metrics_str = ", ".join([f"{k}={v}" for k, v in metrics.items()]) if metrics else ""
        logger.info(f"PERFORMANCE: {operation} took {duration:.4f}s {metrics_str}")
    
    def log_state_change(self, logger: logging.Logger, component: str, old_state: Any, new_state: Any, context: str = ""):
        """Log state changes"""
        context_str = f" ({context})" if context else ""
        logger.info(f"STATE_CHANGE: {component}{context_str}: {old_state} -> {new_state}")
    
    def log_api_call(self, logger: logging.Logger, endpoint: str, method: str, status_code: int = None, 
                     duration: float = None, request_data: Dict = None, response_data: Dict = None):
        """Log API calls and responses"""
        status_str = f", status={status_code}" if status_code else ""
        duration_str = f", duration={duration:.4f}s" if duration else ""
        
        logger.info(f"API_CALL: {method} {endpoint}{status_str}{duration_str}")
        
        if request_data:
            logger.debug(f"API_REQUEST_DATA: {json.dumps(request_data, default=str, indent=2)}")
        
        if response_data:
            logger.debug(f"API_RESPONSE_DATA: {json.dumps(response_data, default=str, indent=2)}")

def function_logger(logger_name: str = None):
    """
    Decorator to automatically log function entry, exit, exceptions, and performance
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # Get logger
            module_logger_name = logger_name or f"{func.__module__}.{func.__qualname__}"
            logger = get_master_logger().get_logger(module_logger_name)
            
            # Log entry
            get_master_logger().log_function_entry(logger, func.__name__, args, kwargs)
            
            start_time = time.time()
            try:
                # Execute function
                result = func(*args, **kwargs)
                
                # Log successful exit
                execution_time = time.time() - start_time
                get_master_logger().log_function_exit(logger, func.__name__, result, execution_time)
                
                return result
                
            except Exception as e:
                # Log exception
                get_master_logger().log_exception(logger, func.__name__, e)
                raise
                
        return wrapper
    return decorator

def get_master_logger() -> MasterLogger:
    """Get the singleton master logger instance"""
    return MasterLogger()

def log_js_message(message: str, level: str = "INFO", data: Dict = None, source: str = "frontend"):
    """
    Log messages from JavaScript frontend to the master log
    """
    logger = get_master_logger().get_logger(f"javascript.{source}")
    
    log_level = getattr(logging, level.upper(), logging.INFO)
    
    if data:
        message = f"{message} | Data: {json.dumps(data, default=str)}"
    
    logger.log(log_level, f"JS_LOG: {message}")

def setup_module_logger(module_name: str) -> logging.Logger:
    """
    Setup a logger for a specific module that feeds into the master log
    """
    return get_master_logger().get_logger(module_name)

# Initialize the master logger when module is imported
_master_logger_instance = MasterLogger()

# Export commonly used functions
__all__ = [
    'MasterLogger',
    'get_master_logger', 
    'function_logger',
    'log_js_message',
    'setup_module_logger'
]

