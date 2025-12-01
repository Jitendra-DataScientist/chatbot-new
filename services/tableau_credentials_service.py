"""
Tableau Credentials Service - Secure credential management using Google Sheets
Fetches credentials from Google Sheets via Apps Script with username lookup
"""

import os
import json
import requests
import urllib3
from typing import Dict, Any, Optional, Tuple
from datetime import datetime
from master_logger import setup_module_logger

# Disable proxy for corporate network compatibility - Fix for connection issues
os.environ['NO_PROXY'] = '*'
for key in ['HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy']:
    os.environ.pop(key, None)

# Disable SSL warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Setup logger
master_logger = setup_module_logger('tableau_credentials')


class TableauCredentialsService:
    """Service for fetching Tableau credentials from Google Sheets"""
    
    def __init__(self, config_file: str = 'google_sheets_config.json'):
        """Initialize the credentials service with configuration"""
        self.config = self._load_config(config_file)
        
        # Extract credentials configuration
        credentials_config = self.config.get('tableau_credentials', {})
        self.enabled = credentials_config.get('enabled', False)
        self.apps_script_url = credentials_config.get('apps_script_url')
        self.sheet_url = credentials_config.get('sheet_url')
        self.timeout = credentials_config.get('timeout_seconds', 10)
        self.retry_attempts = credentials_config.get('retry_attempts', 2)
        
        # Cache for credentials (in-memory only, no persistent storage)
        self._credentials_cache = {}
        self._cache_timestamp = None
        self.cache_duration_seconds = credentials_config.get('cache_duration_seconds', 300)  # 5 minutes default
        
        if self.enabled and not self.apps_script_url:
            master_logger.warning("Tableau credentials service enabled but no Apps Script URL provided")
            self.enabled = False
        
        master_logger.info(f"TableauCredentialsService initialized - enabled: {self.enabled}")
    
    def _load_config(self, config_file: str) -> Dict[str, Any]:
        """Load configuration from JSON file"""
        try:
            with open(config_file, 'r') as f:
                return json.load(f)
        except FileNotFoundError:
            master_logger.warning(f"Config file {config_file} not found, using defaults")
            return {}
        except json.JSONDecodeError as e:
            master_logger.error(f"Invalid JSON in config file {config_file}: {e}")
            return {}
    
    def is_enabled(self) -> bool:
        """Check if credentials service is enabled"""
        return self.enabled
    
    def get_credentials_by_username(self, username: str) -> Tuple[bool, Optional[Dict[str, Any]], Optional[str]]:
        """
        Fetch Tableau credentials from Google Sheets by username lookup
        
        Args:
            username: The username to lookup in Google Sheets
            
        Returns:
            Tuple of (success: bool, credentials_dict: dict, error_message: str)
            credentials_dict contains: username, password, site_content_url, server_url, api_version
        """
        if not self.enabled:
            master_logger.error("Credentials service is disabled - falling back to pass_config.json")
            return False, None, "Credentials service is disabled"
        
        if not self.apps_script_url:
            return False, None, "Apps Script URL not configured"
        
        # Check cache first
        if self._is_cache_valid() and username in self._credentials_cache:
            master_logger.info(f"[CACHE_HIT] Retrieved credentials for {username} from cache")
            return True, self._credentials_cache[username], None
        
        master_logger.info(f"[CACHE_MISS] Fetching credentials for {username} from Google Sheets")
        
        # Fetch from Google Sheets with retries
        for attempt in range(self.retry_attempts + 1):
            try:
                success, credentials, error = self._fetch_credentials_from_sheets(username, attempt + 1)
                
                if success:
                    # Cache the credentials
                    self._credentials_cache[username] = credentials
                    self._cache_timestamp = datetime.now()
                    
                    master_logger.info(f"[SUCCESS] Retrieved credentials for {username} from Google Sheets")
                    return True, credentials, None
                else:
                    if attempt < self.retry_attempts:
                        master_logger.warning(f"Attempt {attempt + 1} failed for {username}, retrying: {error}")
                        import time
                        time.sleep(1 * (attempt + 1))  # Exponential backoff
                    else:
                        master_logger.error(f"All {self.retry_attempts + 1} attempts failed for {username}: {error}")
                        return False, None, error
                        
            except Exception as e:
                error_msg = f"Exception during credentials fetch (attempt {attempt + 1}): {str(e)}"
                if attempt < self.retry_attempts:
                    master_logger.warning(error_msg + ", retrying...")
                    import time
                    time.sleep(1 * (attempt + 1))
                else:
                    master_logger.error(error_msg)
                    return False, None, error_msg
        
        return False, None, "Max retry attempts exceeded"
    
    def _fetch_credentials_from_sheets(self, username: str, attempt_number: int) -> Tuple[bool, Optional[Dict[str, Any]], Optional[str]]:
        """Fetch credentials from Google Sheets via Apps Script"""
        headers = {
            'Content-Type': 'application/json',
            'User-Agent': 'Tableau-Credentials-Service/1.0'
        }
        
        # Prepare request payload with username lookup
        payload = {
            'action': 'get_credentials',
            'username': username
        }
        
        try:
            master_logger.debug(f"Fetching credentials from Google Sheets (attempt {attempt_number})")
            
            response = requests.post(
                self.apps_script_url,
                json=payload,
                headers=headers,
                timeout=self.timeout,
                verify=False  # Disable SSL verification for Google Apps Script
            )
            
            master_logger.debug(f"Response status: {response.status_code}")
            
            if response.status_code == 200:
                try:
                    response_data = response.json()
                    
                    if response_data.get('success', False):
                        credentials = response_data.get('data', {})
                        
                        # Validate credentials structure
                        required_fields = ['username', 'password', 'site_content_url']
                        missing_fields = [field for field in required_fields if field not in credentials]
                        
                        if missing_fields:
                            return False, None, f"Missing required fields: {', '.join(missing_fields)}"
                        
                        # Add default fields if not present
                        credentials.setdefault('tableau_server_url', 'https://prod-in-a.online.tableau.com')
                        credentials.setdefault('api_version', '3.21')
                        credentials.setdefault('auth_type', 'username_password')
                        credentials.setdefault('site_id', '')
                        
                        return True, credentials, None
                    else:
                        error_msg = response_data.get('message', 'Unknown error from Apps Script')
                        return False, None, f"Apps Script error: {error_msg}"
                        
                except json.JSONDecodeError:
                    return False, None, f"Invalid JSON response: {response.text}"
            else:
                return False, None, f"HTTP {response.status_code}: {response.text}"
                
        except requests.exceptions.Timeout:
            return False, None, f"Request timeout after {self.timeout} seconds"
        except requests.exceptions.ConnectionError:
            return False, None, "Connection error - unable to reach Apps Script endpoint"
        except requests.exceptions.RequestException as e:
            return False, None, f"Request exception: {str(e)}"
    
    def _is_cache_valid(self) -> bool:
        """Check if the in-memory cache is still valid"""
        if self._cache_timestamp is None:
            return False
        
        from datetime import timedelta
        age = (datetime.now() - self._cache_timestamp).total_seconds()
        return age < self.cache_duration_seconds
    
    def clear_cache(self):
        """Clear the in-memory credentials cache"""
        self._credentials_cache = {}
        self._cache_timestamp = None
        master_logger.info("Credentials cache cleared")
    
    def get_all_credentials(self) -> Tuple[bool, Optional[list], Optional[str]]:
        """
        Fetch all available credentials from Google Sheets (for admin purposes)
        WARNING: This returns all credentials, use with caution!
        
        Returns:
            Tuple of (success: bool, credentials_list: list, error_message: str)
        """
        if not self.enabled:
            return False, None, "Credentials service is disabled"
        
        headers = {
            'Content-Type': 'application/json',
            'User-Agent': 'Tableau-Credentials-Service/1.0'
        }
        
        payload = {
            'action': 'get_all_credentials'
        }
        
        try:
            response = requests.post(
                self.apps_script_url,
                json=payload,
                headers=headers,
                timeout=self.timeout,
                verify=False
            )
            
            if response.status_code == 200:
                response_data = response.json()
                
                if response_data.get('success', False):
                    credentials_list = response_data.get('data', [])
                    master_logger.info(f"Retrieved {len(credentials_list)} credential entries")
                    return True, credentials_list, None
                else:
                    error_msg = response_data.get('message', 'Unknown error')
                    return False, None, error_msg
            else:
                return False, None, f"HTTP {response.status_code}: {response.text}"
                
        except Exception as e:
            return False, None, f"Error fetching all credentials: {str(e)}"
    
    def test_connection(self) -> Tuple[bool, Optional[Dict[str, Any]], Optional[str]]:
        """Test the connection to Google Sheets Apps Script"""
        if not self.enabled:
            return False, None, "Credentials service is disabled"
        
        try:
            master_logger.info("Testing credentials service connection...")
            
            response = requests.get(
                self.apps_script_url,
                timeout=self.timeout,
                verify=False
            )
            
            if response.status_code == 200:
                try:
                    response_data = response.json()
                    
                    if response_data.get('success', False):
                        master_logger.info("Credentials service connection test successful")
                        return True, response_data, None
                    else:
                        error_msg = response_data.get('message', 'Unknown error')
                        return False, None, f"Test failed: {error_msg}"
                        
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


# Global instance for easy import
tableau_credentials_service = TableauCredentialsService()


def get_tableau_credentials(username: str) -> Tuple[bool, Optional[Dict[str, Any]], Optional[str]]:
    """
    Convenience function to get Tableau credentials by username
    
    Args:
        username: The username to lookup
        
    Returns:
        Tuple of (success: bool, credentials: dict, error_message: str)
    """
    return tableau_credentials_service.get_credentials_by_username(username)


def load_tableau_config(username: Optional[str] = None) -> Dict[str, Any]:
    """
    Load Tableau configuration from Google Sheets exclusively (NO FALLBACK)
    
    This function loads credentials from Google Sheets ONLY.
    If Google Sheets fails, the application will raise an error.
    
    Args:
        username: Username to lookup in Google Sheets (optional)
                 If not provided, will use environment variable TABLEAU_USERNAME
    
    Returns:
        Dictionary with Tableau configuration
        
    Raises:
        RuntimeError: If Google Sheets service fails or credentials not found
    """
    # Get username from parameter or environment variable
    if not username:
        import os
        username = os.getenv('TABLEAU_USERNAME', None)
        
        if not username:
            raise ValueError(
                "Username not provided and TABLEAU_USERNAME environment variable not set. "
                "Please set TABLEAU_USERNAME in your .env file or pass username parameter."
            )
    
    # Try Google Sheets (enabled or not)
    if tableau_credentials_service.is_enabled():
        success, credentials, error = get_tableau_credentials(username)
        if success:
            master_logger.info(f"[GOOGLE_SHEETS] Loaded credentials for {username}")
            return credentials
        else:
            master_logger.error(f"[GOOGLE_SHEETS] Failed to load credentials: {error}")
            raise RuntimeError(f"Failed to load credentials from Google Sheets: {error}")
    else:
        master_logger.error("[GOOGLE_SHEETS] Service is disabled in configuration")
        raise RuntimeError(
            "Google Sheets credentials service is disabled. "
            "Enable it in google_sheets_config.json: 'tableau_credentials': {'enabled': true}"
        )


# Test function
if __name__ == "__main__":
    print("Testing TableauCredentialsService...")
    
    # Test connection
    success, data, error = tableau_credentials_service.test_connection()
    if success:
        print(f"[OK] Connection test passed: {data}")
    else:
        print(f"[ERROR] Connection test failed: {error}")
    
    # Test fetching credentials
    test_username = "cca49542@gmail.com"
    success, credentials, error = get_tableau_credentials(test_username)
    if success:
        print(f"[OK] Retrieved credentials for {test_username}")
        print(f"     Username: {credentials.get('username')}")
        print(f"     Site Content URL: {credentials.get('site_content_url')}")
        print(f"     Server URL: {credentials.get('tableau_server_url')}")
    else:
        print(f"[ERROR] Failed to retrieve credentials: {error}")
    
    # Test fallback mechanism
    print("\nTesting fallback mechanism...")
    config = load_tableau_config(test_username)
    print(f"[OK] Loaded config with username: {config.get('username')}")

