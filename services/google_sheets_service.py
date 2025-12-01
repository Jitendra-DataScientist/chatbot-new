"""
Google Sheets Integration Service for Feedback System
Handles sending feedback data to Google Apps Script endpoint
"""

import json
import requests
import time
from typing import Dict, Any, Optional, Tuple
import logging
from datetime import datetime
import urllib3

# Disable SSL warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

class GoogleSheetsService:
    """Service for integrating with Google Sheets via Apps Script"""
    
    def __init__(self, config_file: str = 'google_sheets_config.json'):
        """Initialize the Google Sheets service with configuration"""
        self.config = self._load_config(config_file)
        self.logger = logging.getLogger(__name__)
        
        # Extract configuration for feedback system
        self.enabled = self.config.get('google_sheets', {}).get('enabled', False)
        self.apps_script_url = self.config.get('google_sheets', {}).get('apps_script_url')
        self.timeout = self.config.get('google_sheets', {}).get('timeout_seconds', 10)
        self.retry_attempts = self.config.get('google_sheets', {}).get('retry_attempts', 2)
        self.fallback_on_failure = self.config.get('google_sheets', {}).get('fallback_on_failure', True)
        self.log_responses = self.config.get('google_sheets', {}).get('log_responses', True)
        
        # Extract configuration for data exploration logging
        data_exploration_config = self.config.get('data_exploration_logging', {})
        self.data_exploration_enabled = data_exploration_config.get('enabled', False)
        self.data_exploration_apps_script_url = data_exploration_config.get('apps_script_url')
        self.data_exploration_timeout = data_exploration_config.get('timeout_seconds', 10)
        self.data_exploration_retry_attempts = data_exploration_config.get('retry_attempts', 2)
        self.data_exploration_log_responses = data_exploration_config.get('log_responses', True)
        
        if self.enabled and not self.apps_script_url:
            self.logger.warning("Google Sheets integration enabled but no Apps Script URL provided")
            self.enabled = False
            
        if self.data_exploration_enabled and not self.data_exploration_apps_script_url:
            self.logger.warning("Data exploration logging enabled but no Apps Script URL provided")
            self.data_exploration_enabled = False
    
    def _load_config(self, config_file: str) -> Dict[str, Any]:
        """Load configuration from JSON file"""
        try:
            with open(config_file, 'r') as f:
                return json.load(f)
        except FileNotFoundError:
            self.logger.warning(f"Config file {config_file} not found, using defaults")
            return {}
        except json.JSONDecodeError as e:
            self.logger.error(f"Invalid JSON in config file {config_file}: {e}")
            return {}
    
    def is_enabled(self) -> bool:
        """Check if Google Sheets integration is enabled"""
        return self.enabled
    
    def send_feedback_to_sheets(self, feedback_data: Dict[str, Any]) -> Tuple[bool, Optional[Dict[str, Any]], Optional[str]]:
        """
        Send feedback data to Google Sheets via Apps Script
        
        Args:
            feedback_data: Dictionary containing feedback information
            
        Returns:
            Tuple of (success: bool, response_data: dict, error_message: str)
        """
        if not self.enabled:
            return False, None, "Google Sheets integration is disabled"
        
        if not self.apps_script_url:
            return False, None, "Apps Script URL not configured"
        
        # Prepare the payload
        payload = self._prepare_payload(feedback_data)
        
        # Attempt to send with retries
        for attempt in range(self.retry_attempts + 1):
            try:
                success, response_data, error = self._make_request(payload, attempt + 1)
                
                if success:
                    if self.log_responses:
                        self.logger.info(f"Successfully sent feedback to Google Sheets: {response_data}")
                    return True, response_data, None
                else:
                    if attempt < self.retry_attempts:
                        self.logger.warning(f"Attempt {attempt + 1} failed, retrying: {error}")
                        time.sleep(1 * (attempt + 1))  # Exponential backoff
                    else:
                        self.logger.error(f"All {self.retry_attempts + 1} attempts failed: {error}")
                        return False, None, error
                        
            except Exception as e:
                error_msg = f"Exception during Google Sheets request (attempt {attempt + 1}): {str(e)}"
                if attempt < self.retry_attempts:
                    self.logger.warning(error_msg + ", retrying...")
                    time.sleep(1 * (attempt + 1))
                else:
                    self.logger.error(error_msg)
                    return False, None, error_msg
        
        return False, None, "Max retry attempts exceeded"
    
    def _prepare_payload(self, feedback_data: Dict[str, Any]) -> Dict[str, Any]:
        """Prepare the payload for Apps Script"""
        # Ensure all required fields are present
        payload = {
            'timestamp': feedback_data.get('timestamp', datetime.now().isoformat()),
            'product_name': feedback_data.get('product_name', 'Unknown Product'),
            'rating': feedback_data.get('rating', 0),
            'complaints': feedback_data.get('complaints', ''),
            'improvements': feedback_data.get('improvements', ''),
            'url': feedback_data.get('url', ''),
            'user_agent': feedback_data.get('user_agent', ''),
            'remote_addr': feedback_data.get('remote_addr', 'unknown'),
            'session_id': feedback_data.get('session_id', 'anonymous')
        }
        
        return payload
    
    def _make_request(self, payload: Dict[str, Any], attempt_number: int) -> Tuple[bool, Optional[Dict[str, Any]], Optional[str]]:
        """Make HTTP request to Apps Script endpoint"""
        headers = {
            'Content-Type': 'application/json',
            'User-Agent': 'Tableau-Feedback-System/1.0'
        }
        
        try:
            self.logger.debug(f"Sending request to Google Sheets (attempt {attempt_number}): {self.apps_script_url}")
            
            response = requests.post(
                self.apps_script_url,
                json=payload,
                headers=headers,
                timeout=self.timeout,
                verify=False  # Disable SSL verification for Google Apps Script
            )
            
            # Log the raw response for debugging
            self.logger.debug(f"Google Sheets response status: {response.status_code}")
            self.logger.debug(f"Google Sheets response text: {response.text}")
            
            if response.status_code == 200:
                try:
                    response_data = response.json()
                    
                    if response_data.get('success', False):
                        return True, response_data, None
                    else:
                        error_msg = response_data.get('message', 'Unknown error from Apps Script')
                        return False, None, f"Apps Script error: {error_msg}"
                        
                except json.JSONDecodeError:
                    return False, None, f"Invalid JSON response from Apps Script: {response.text}"
            else:
                return False, None, f"HTTP {response.status_code}: {response.text}"
                
        except requests.exceptions.Timeout:
            return False, None, f"Request timeout after {self.timeout} seconds"
        except requests.exceptions.ConnectionError:
            return False, None, "Connection error - unable to reach Apps Script endpoint"
        except requests.exceptions.RequestException as e:
            return False, None, f"Request exception: {str(e)}"
    
    def test_connection(self) -> Tuple[bool, Optional[Dict[str, Any]], Optional[str]]:
        """Test the connection to Google Sheets Apps Script"""
        if not self.enabled:
            return False, None, "Google Sheets integration is disabled"
        
        try:
            self.logger.info("Testing Google Sheets connection...")
            
            response = requests.get(
                self.apps_script_url,
                timeout=self.timeout,
                verify=False  # Disable SSL verification for Google Apps Script
            )
            
            if response.status_code == 200:
                try:
                    response_data = response.json()
                    
                    if response_data.get('success', False):
                        self.logger.info("Google Sheets connection test successful")
                        return True, response_data, None
                    else:
                        error_msg = response_data.get('message', 'Unknown error')
                        return False, None, f"Apps Script test failed: {error_msg}"
                        
                except json.JSONDecodeError:
                    return False, None, f"Invalid JSON response: {response.text}"
            else:
                return False, None, f"HTTP {response.status_code}: {response.text}"
                
        except requests.exceptions.Timeout:
            return False, None, f"Connection test timeout after {self.timeout} seconds"
        except requests.exceptions.ConnectionError:
            return False, None, "Connection error - unable to reach Apps Script endpoint"
        except Exception as e:
            return False, None, f"Connection test error: {str(e)}"
    
    def get_feedback_stats(self) -> Tuple[bool, Optional[Dict[str, Any]], Optional[str]]:
        """Get feedback statistics from Google Sheets"""
        if not self.enabled:
            return False, None, "Google Sheets integration is disabled"
        
        # This would require additional Apps Script function
        # For now, we'll use the test endpoint which returns basic stats
        return self.test_connection()
    
    def send_data_exploration_log(self, log_data: Dict[str, Any]) -> Tuple[bool, Optional[Dict[str, Any]], Optional[str]]:
        """
        Send data exploration log to Google Sheets via Apps Script
        
        Args:
            log_data: Dictionary containing data exploration log information
            
        Returns:
            Tuple of (success: bool, response_data: dict, error_message: str)
        """
        if not self.data_exploration_enabled:
            return False, None, "Data exploration logging is disabled"
        
        if not self.data_exploration_apps_script_url:
            return False, None, "Data exploration Apps Script URL not configured"
        
        # Prepare the payload
        payload = self._prepare_data_exploration_payload(log_data)
        
        # Attempt to send with retries
        for attempt in range(self.data_exploration_retry_attempts + 1):
            try:
                success, response_data, error = self._make_data_exploration_request(payload, attempt + 1)
                
                if success:
                    if self.data_exploration_log_responses:
                        self.logger.info(f"Successfully sent data exploration log to Google Sheets: {response_data}")
                    return True, response_data, None
                else:
                    if attempt < self.data_exploration_retry_attempts:
                        self.logger.warning(f"Data exploration log attempt {attempt + 1} failed, retrying: {error}")
                        time.sleep(1 * (attempt + 1))  # Exponential backoff
                    else:
                        self.logger.error(f"All {self.data_exploration_retry_attempts + 1} attempts failed for data exploration log: {error}")
                        return False, None, error
                        
            except Exception as e:
                error_msg = f"Exception during data exploration log request (attempt {attempt + 1}): {str(e)}"
                if attempt < self.data_exploration_retry_attempts:
                    self.logger.warning(error_msg + ", retrying...")
                    time.sleep(1 * (attempt + 1))
                else:
                    self.logger.error(error_msg)
                    return False, None, error_msg
        
        return False, None, "Max retry attempts exceeded"
    
    def _prepare_data_exploration_payload(self, log_data: Dict[str, Any]) -> Dict[str, Any]:
        """Prepare the payload for data exploration Apps Script - simplified to 4 fields"""
        payload = {
            'timestamp': log_data.get('timestamp', datetime.now().isoformat()),
            'user_query': log_data.get('user_query', ''),
            'generated_code': log_data.get('generated_code', ''),
            'generated_answer': log_data.get('generated_answer', '')
        }
        
        return payload
    
    def _make_data_exploration_request(self, payload: Dict[str, Any], attempt_number: int) -> Tuple[bool, Optional[Dict[str, Any]], Optional[str]]:
        """Make HTTP request to data exploration Apps Script endpoint"""
        headers = {
            'Content-Type': 'application/json',
            'User-Agent': 'Tableau-DataExploration-Logger/1.0'
        }
        
        try:
            self.logger.debug(f"Sending data exploration log to Google Sheets (attempt {attempt_number}): {self.data_exploration_apps_script_url}")
            
            response = requests.post(
                self.data_exploration_apps_script_url,
                json=payload,
                headers=headers,
                timeout=self.data_exploration_timeout,
                verify=False  # Disable SSL verification for Google Apps Script
            )
            
            # Log the raw response for debugging
            self.logger.debug(f"Data exploration Google Sheets response status: {response.status_code}")
            self.logger.debug(f"Data exploration Google Sheets response text: {response.text}")
            
            if response.status_code == 200:
                try:
                    response_data = response.json()
                    
                    if response_data.get('success', False):
                        return True, response_data, None
                    else:
                        error_msg = response_data.get('message', 'Unknown error from Apps Script')
                        return False, None, f"Apps Script error: {error_msg}"
                        
                except json.JSONDecodeError:
                    return False, None, f"Invalid JSON response from Apps Script: {response.text}"
            else:
                return False, None, f"HTTP {response.status_code}: {response.text}"
                
        except requests.exceptions.Timeout:
            return False, None, f"Request timeout after {self.data_exploration_timeout} seconds"
        except requests.exceptions.ConnectionError:
            return False, None, "Connection error - unable to reach data exploration Apps Script endpoint"
        except requests.exceptions.RequestException as e:
            return False, None, f"Request exception: {str(e)}"
    
    def test_data_exploration_connection(self) -> Tuple[bool, Optional[Dict[str, Any]], Optional[str]]:
        """Test the connection to data exploration Google Sheets Apps Script"""
        if not self.data_exploration_enabled:
            return False, None, "Data exploration logging is disabled"
        
        try:
            self.logger.info("Testing data exploration Google Sheets connection...")
            
            response = requests.get(
                self.data_exploration_apps_script_url,
                timeout=self.data_exploration_timeout,
                verify=False  # Disable SSL verification for Google Apps Script
            )
            
            if response.status_code == 200:
                try:
                    response_data = response.json()
                    
                    if response_data.get('success', False):
                        self.logger.info("Data exploration Google Sheets connection test successful")
                        return True, response_data, None
                    else:
                        error_msg = response_data.get('message', 'Unknown error')
                        return False, None, f"Apps Script test failed: {error_msg}"
                        
                except json.JSONDecodeError:
                    return False, None, f"Invalid JSON response: {response.text}"
            else:
                return False, None, f"HTTP {response.status_code}: {response.text}"
                
        except requests.exceptions.Timeout:
            return False, None, f"Connection test timeout after {self.data_exploration_timeout} seconds"
        except requests.exceptions.ConnectionError:
            return False, None, "Connection error - unable to reach data exploration Apps Script endpoint"
        except Exception as e:
            return False, None, f"Connection test error: {str(e)}"

    def update_config(self, new_config: Dict[str, Any]) -> bool:
        """Update configuration dynamically"""
        try:
            self.config.update(new_config)
            
            # Update instance variables for feedback system
            sheets_config = self.config.get('google_sheets', {})
            self.enabled = sheets_config.get('enabled', False)
            self.apps_script_url = sheets_config.get('apps_script_url')
            self.timeout = sheets_config.get('timeout_seconds', 10)
            self.retry_attempts = sheets_config.get('retry_attempts', 2)
            
            # Update instance variables for data exploration logging
            data_exploration_config = self.config.get('data_exploration_logging', {})
            self.data_exploration_enabled = data_exploration_config.get('enabled', False)
            self.data_exploration_apps_script_url = data_exploration_config.get('apps_script_url')
            self.data_exploration_timeout = data_exploration_config.get('timeout_seconds', 10)
            self.data_exploration_retry_attempts = data_exploration_config.get('retry_attempts', 2)
            
            if self.enabled and not self.apps_script_url:
                self.logger.warning("Google Sheets integration enabled but no Apps Script URL provided")
                self.enabled = False
                
            if self.data_exploration_enabled and not self.data_exploration_apps_script_url:
                self.logger.warning("Data exploration logging enabled but no Apps Script URL provided")
                self.data_exploration_enabled = False
            
            return True
        except Exception as e:
            self.logger.error(f"Failed to update config: {e}")
            return False


# Global instance for easy import
google_sheets_service = GoogleSheetsService()


def send_feedback_to_google_sheets(feedback_data: Dict[str, Any]) -> Tuple[bool, Optional[Dict[str, Any]], Optional[str]]:
    """
    Convenience function to send feedback to Google Sheets
    
    Args:
        feedback_data: Dictionary containing feedback information
        
    Returns:
        Tuple of (success: bool, response_data: dict, error_message: str)
    """
    return google_sheets_service.send_feedback_to_sheets(feedback_data)


def test_google_sheets_connection() -> Tuple[bool, Optional[Dict[str, Any]], Optional[str]]:
    """
    Convenience function to test Google Sheets connection
    
    Returns:
        Tuple of (success: bool, response_data: dict, error_message: str)
    """
    return google_sheets_service.test_connection()


def send_data_exploration_log_to_google_sheets(log_data: Dict[str, Any]) -> Tuple[bool, Optional[Dict[str, Any]], Optional[str]]:
    """
    Convenience function to send data exploration log to Google Sheets
    
    Args:
        log_data: Dictionary containing data exploration log information
        
    Returns:
        Tuple of (success: bool, response_data: dict, error_message: str)
    """
    return google_sheets_service.send_data_exploration_log(log_data)


def test_data_exploration_google_sheets_connection() -> Tuple[bool, Optional[Dict[str, Any]], Optional[str]]:
    """
    Convenience function to test data exploration Google Sheets connection
    
    Returns:
        Tuple of (success: bool, response_data: dict, error_message: str)
    """
    return google_sheets_service.test_data_exploration_connection()
