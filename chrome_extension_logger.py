"""
Chrome Extension Logger
Dedicated logging system for Chrome extension events and interactions
"""

import logging
import os
import json
from datetime import datetime
from typing import Dict, Any, Optional

class ChromeExtensionLogger:
    """Dedicated logger for Chrome extension events"""
    
    def __init__(self, log_file: str = "chrome_extension_debug.log"):
        self.log_file = log_file
        self.logger = self._setup_logger()
        
    def _setup_logger(self) -> logging.Logger:
        """Setup dedicated logger for Chrome extension"""
        logger = logging.getLogger('chrome_extension')
        logger.setLevel(logging.DEBUG)
        
        # Remove existing handlers to avoid duplicates
        if logger.handlers:
            logger.handlers.clear()
        
        # Create file handler
        file_handler = logging.FileHandler(self.log_file, encoding='utf-8')
        file_handler.setLevel(logging.DEBUG)
        
        # Create console handler for critical errors
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.ERROR)
        
        # Create detailed formatter
        formatter = logging.Formatter(
            '[%(asctime)s] %(levelname)s - CHROME_EXT - %(funcName)s:%(lineno)d - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        
        file_handler.setFormatter(formatter)
        console_handler.setFormatter(formatter)
        
        logger.addHandler(file_handler)
        logger.addHandler(console_handler)
        
        # Prevent propagation to root logger
        logger.propagate = False
        
        return logger
    
    def log_extension_event(self, level: str, event_type: str, message: str, 
                          data: Optional[Dict[str, Any]] = None, 
                          url: Optional[str] = None,
                          user_agent: Optional[str] = None):
        """Log Chrome extension event with structured data"""
        
        log_entry = {
            'timestamp': datetime.now().isoformat(),
            'event_type': event_type,
            'message': message,
            'url': url,
            'user_agent': user_agent,
            'data': data or {}
        }
        
        # Format the log message
        formatted_message = f"[{event_type}] {message}"
        if data:
            formatted_message += f" | Data: {json.dumps(data, default=str)}"
        if url:
            formatted_message += f" | URL: {url}"
        
        # Log at appropriate level
        if level.lower() == 'debug':
            self.logger.debug(formatted_message)
        elif level.lower() == 'info':
            self.logger.info(formatted_message)
        elif level.lower() == 'warning' or level.lower() == 'warn':
            self.logger.warning(formatted_message)
        elif level.lower() == 'error':
            self.logger.error(formatted_message)
        elif level.lower() == 'critical':
            self.logger.critical(formatted_message)
        else:
            self.logger.info(formatted_message)
    
    def log_chat_request(self, message: str, selected_chart: Optional[str] = None, 
                        connection_key: Optional[str] = None, 
                        request_data: Optional[Dict[str, Any]] = None):
        """Log chat request from extension"""
        self.log_extension_event(
            'info', 
            'CHAT_REQUEST', 
            f"User question: '{message[:100]}...' Chart: {selected_chart}",
            {
                'full_message': message,
                'selected_chart': selected_chart,
                'connection_key': connection_key,
                'request_data': request_data
            }
        )
    
    def log_chat_response(self, response: str, success: bool = True, 
                         error_details: Optional[str] = None):
        """Log chat response to extension"""
        level = 'info' if success else 'error'
        event_type = 'CHAT_RESPONSE' if success else 'CHAT_ERROR'
        
        self.log_extension_event(
            level,
            event_type,
            f"Response: '{response[:100]}...'",
            {
                'full_response': response,
                'success': success,
                'error_details': error_details
            }
        )
    
    def log_chart_selection(self, chart_name: str, chart_data: Optional[Dict[str, Any]] = None):
        """Log chart selection event"""
        self.log_extension_event(
            'info',
            'CHART_SELECTION',
            f"Chart selected: {chart_name}",
            {
                'chart_name': chart_name,
                'chart_data': chart_data
            }
        )
    
    def log_connection_event(self, event: str, success: bool = True, 
                           details: Optional[Dict[str, Any]] = None):
        """Log connection events"""
        level = 'info' if success else 'error'
        event_type = f"CONNECTION_{event.upper()}"
        
        self.log_extension_event(
            level,
            event_type,
            f"Connection {event}: {'SUCCESS' if success else 'FAILED'}",
            details
        )
    
    def log_error(self, error_type: str, error_message: str, 
                  stack_trace: Optional[str] = None,
                  context: Optional[Dict[str, Any]] = None):
        """Log extension errors"""
        self.log_extension_event(
            'error',
            f'ERROR_{error_type.upper()}',
            error_message,
            {
                'stack_trace': stack_trace,
                'context': context
            }
        )
    
    def log_debug_info(self, info_type: str, message: str, 
                       debug_data: Optional[Dict[str, Any]] = None):
        """Log debug information"""
        self.log_extension_event(
            'debug',
            f'DEBUG_{info_type.upper()}',
            message,
            debug_data
        )
    
    def log_network_request(self, method: str, url: str, status_code: Optional[int] = None,
                           request_data: Optional[Dict[str, Any]] = None,
                           response_data: Optional[Dict[str, Any]] = None,
                           error: Optional[str] = None):
        """Log network requests from extension"""
        level = 'info' if not error else 'error'
        event_type = 'NETWORK_REQUEST'
        
        self.log_extension_event(
            level,
            event_type,
            f"{method} {url} - Status: {status_code}",
            {
                'method': method,
                'url': url,
                'status_code': status_code,
                'request_data': request_data,
                'response_data': response_data,
                'error': error
            }
        )

# Global instance
chrome_extension_logger = ChromeExtensionLogger()
