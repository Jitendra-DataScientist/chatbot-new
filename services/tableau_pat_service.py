"""
Tableau PAT Credentials Service - Fetch PAT credentials from Google Sheets
Fetches Tableau Personal Access Token credentials by site_content_url lookup
Uses direct Google Sheets API with OAuth authentication (token.pickle)
"""

import pickle
import os.path
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
import pandas as pd
from typing import Dict, Optional, Tuple, Any, List
# gazelle:ignore master_logger
from master_logger import setup_module_logger
import re
from urllib.parse import urlparse

# Setup logger
master_logger = setup_module_logger('tableau_pat_service')

# Google Sheets API scopes
SCOPES = ['https://www.googleapis.com/auth/spreadsheets.readonly',
          'https://www.googleapis.com/auth/drive.readonly']


class TableauPATService:
    """Service for fetching Tableau PAT credentials from Google Sheets by site_content_url"""

    def __init__(self,
                 spreadsheet_id: str = '16EOmFnnVg9GRlp7dlkJBVi6rmCpoXNdXfbi4ZM4wSPs',
                 sheet_name: str = 'Credentials',
                 credentials_file: str = 'credentials.json',
                 token_file: str = 'token.pickle'):
        """
        Initialize the PAT credentials service

        Args:
            spreadsheet_id: Google Sheet ID containing PAT credentials
            sheet_name: Name of the sheet/tab to read from
            credentials_file: Path to credentials.json (OAuth client secrets)
            token_file: Path to token.pickle (cached OAuth token)
        """
        self.spreadsheet_id = spreadsheet_id
        self.sheet_name = sheet_name
        self.credentials_file = credentials_file
        self.token_file = token_file

        # Cache for credentials DataFrame (in-memory only)
        self._credentials_cache = None
        self._cache_timestamp = None
        self.cache_duration_seconds = 300  # 5 minutes

        master_logger.info(f"TableauPATService initialized - Sheet ID: {spreadsheet_id}")

    def _gsheet_api_check(self) -> Any:
        """
        Authenticate with Google Sheets API using OAuth
        Uses token.pickle for cached credentials, credentials.json for new auth

        Returns:
            Authenticated credentials object
        """
        creds = None

        # Load cached credentials from token.pickle
        if os.path.exists(self.token_file):
            try:
                with open(self.token_file, 'rb') as token:
                    creds = pickle.load(token)
                master_logger.info(f"Loaded cached credentials from {self.token_file}")
            except Exception as e:
                master_logger.warning(f"Failed to load token.pickle: {e}")
                creds = None

        # Refresh or create new credentials if needed
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                master_logger.info("Refreshing expired credentials...")
                creds.refresh(Request())
                master_logger.info("Credentials refreshed successfully")
            else:
                master_logger.info("No valid credentials found, initiating OAuth flow...")
                if not os.path.exists(self.credentials_file):
                    raise FileNotFoundError(
                        f"Credentials file not found: {self.credentials_file}. "
                        f"Please ensure credentials.json is in the project root."
                    )

                flow = InstalledAppFlow.from_client_secrets_file(
                    self.credentials_file, SCOPES)
                creds = flow.run_local_server(port=0)
                master_logger.info("OAuth flow completed successfully")

            # Save credentials for future use
            with open(self.token_file, 'wb') as token:
                pickle.dump(creds, token)
            master_logger.info(f"Saved credentials to {self.token_file}")

        return creds

    def _pull_sheet_data(self) -> pd.DataFrame:
        """
        Pull data from Google Sheet and return as pandas DataFrame

        Returns:
            DataFrame containing credentials data
        """
        try:
            master_logger.info(f"Fetching data from Google Sheet: {self.spreadsheet_id}")

            # Authenticate
            creds = self._gsheet_api_check()

            # Build Google Sheets API service
            service = build('sheets', 'v4', credentials=creds)
            sheet = service.spreadsheets()

            # Fetch data from the sheet
            range_name = self.sheet_name
            result = sheet.values().get(
                spreadsheetId=self.spreadsheet_id,
                range=range_name
            ).execute()

            values = result.get('values', [])

            if not values:
                master_logger.error('No data found in Google Sheet')
                return pd.DataFrame()

            # Convert to DataFrame
            headers = values[0]
            rows = values[1:]
            df = pd.DataFrame(rows, columns=headers)

            master_logger.info(f"Successfully fetched {len(df)} rows from Google Sheet")
            master_logger.info(f"Columns: {list(df.columns)}")

            return df

        except Exception as e:
            master_logger.error(f"Error fetching data from Google Sheet: {e}")
            raise

    def _is_cache_valid(self) -> bool:
        """Check if the in-memory cache is still valid"""
        if self._cache_timestamp is None or self._credentials_cache is None:
            return False

        from datetime import datetime, timedelta
        age = (datetime.now() - self._cache_timestamp).total_seconds()
        return age < self.cache_duration_seconds

    def _get_credentials_df(self, force_refresh: bool = False) -> pd.DataFrame:
        """
        Get credentials DataFrame with caching

        Args:
            force_refresh: Force refresh from Google Sheets even if cache is valid

        Returns:
            DataFrame containing credentials data
        """
        if not force_refresh and self._is_cache_valid():
            master_logger.info("[CACHE_HIT] Using cached credentials DataFrame")
            return self._credentials_cache

        master_logger.info("[CACHE_MISS] Fetching fresh data from Google Sheets")
        df = self._pull_sheet_data()

        # Update cache
        self._credentials_cache = df
        from datetime import datetime
        self._cache_timestamp = datetime.now()

        return df

    def extract_site_content_url_from_url(self, tableau_url: str) -> Optional[str]:
        """
        Extract site_content_url from Tableau URL

        Examples:
            https://tableau-aws.uberinternal.com/#/site/uMetricAnalytics/views/... -> uMetricAnalytics
            https://tableau-aws.uberinternal.com/t/uMetricAnalytics/views/... -> uMetricAnalytics
            https://tableau.uberinternal.com/#/views/... -> Default (default site)

        Args:
            tableau_url: Full Tableau URL

        Returns:
            site_content_url or "Default" if no site is specified (default site)
        """
        try:
            # Pattern 1: /#/site/{site_content_url}/
            pattern1 = r'/#/site/([^/]+)'
            match = re.search(pattern1, tableau_url)
            if match:
                site_content_url = match.group(1).strip()
                master_logger.info(f"Extracted site_content_url from URL (pattern 1): {site_content_url}")
                return site_content_url

            # Pattern 2: /t/{site_content_url}/
            pattern2 = r'/t/([^/]+)'
            match = re.search(pattern2, tableau_url)
            if match:
                site_content_url = match.group(1).strip()
                master_logger.info(f"Extracted site_content_url from URL (pattern 2): {site_content_url}")
                return site_content_url

            # Pattern 3: /site/{site_content_url}/
            pattern3 = r'/site/([^/]+)'
            match = re.search(pattern3, tableau_url)
            if match:
                site_content_url = match.group(1).strip()
                master_logger.info(f"Extracted site_content_url from URL (pattern 3): {site_content_url}")
                return site_content_url

            # fixed for default sitecontenturl
            master_logger.info(f"No site_content_url in URL, using 'Default' for default site: {tableau_url}")
            return "Default"

        except Exception as e:
            # fixed for default sitecontenturl
            master_logger.error(f"Error extracting site_content_url from URL: {e}, using 'Default'")
            return "Default"

    def get_credentials_by_site_content_url(self, site_content_url: str) -> Tuple[bool, Optional[Dict[str, Any]], Optional[str]]:
        """
        Fetch Tableau PAT credentials by site_content_url lookup

        Args:
            site_content_url: The site_content_url to lookup (e.g., "uMetricAnalytics")

        Returns:
            Tuple of (success: bool, credentials_dict: dict, error_message: str)
            credentials_dict contains: personal_access_token_name, personal_access_token_secret,
                                      tableau_server_url, api_version, site_content_url
        """
        try:
            # fixed for default sitecontenturl
            if not site_content_url:
                site_content_url = "Default"
                master_logger.info(f"Empty site_content_url provided, using 'Default'")

            master_logger.info(f"Fetching credentials for site_content_url: {site_content_url}")

            # Get credentials DataFrame
            df = self._get_credentials_df()

            if df.empty:
                return False, None, "No credentials data found in Google Sheet"

            # Check if site_content_url column exists
            if 'site_content_url' not in df.columns:
                return False, None, f"Column 'site_content_url' not found in sheet. Available columns: {list(df.columns)}"

            # Find ALL matching rows by site_content_url (for fallback support)
            matching_rows = df[df['site_content_url'] == site_content_url]

            if matching_rows.empty:
                master_logger.warning(f"No credentials found for site_content_url: {site_content_url}")
                master_logger.info(f"Available site_content_urls: {list(df['site_content_url'].unique())}")
                return False, None, f"No credentials found for site_content_url: {site_content_url}"

            master_logger.info(f"Found {len(matching_rows)} credential(s) for site_content_url: {site_content_url}")

            # Get first matching row (will be used by default, others available for fallback)
            row = matching_rows.iloc[0]

            # Build credentials dictionary - support both PAT and username/password
            # Strip whitespace from all string fields to prevent issues
            tableau_server = row.get('tableau_server_url', 'https://tableau-aws.uberinternal.com')
            tableau_server = tableau_server.strip() if isinstance(tableau_server, str) else str(tableau_server)

            api_ver = row.get('api_version', '3.19')
            api_ver = api_ver.strip() if isinstance(api_ver, str) else str(api_ver)

            credentials = {
                'tableau_server_url': tableau_server,
                'api_version': api_ver,
                'site_content_url': site_content_url.strip() if isinstance(site_content_url, str) else site_content_url
            }

            # Check for PAT authentication fields first
            if 'personal_access_token_name' in row and 'personal_access_token_secret' in row:
                pat_name = row.get('personal_access_token_name', '').strip()
                pat_secret = row.get('personal_access_token_secret', '').strip()

                if pat_name and pat_secret:
                    credentials['personal_access_token_name'] = pat_name
                    credentials['personal_access_token_secret'] = pat_secret
                    credentials['auth_type'] = 'personal_access_token'
                    master_logger.info(f"Using PAT authentication for {site_content_url}")

            # Check for username/password authentication fields
            if 'auth_type' not in credentials:
                if 'username' in row and 'password' in row:
                    username = row.get('username', '').strip()
                    password = row.get('password', '').strip()

                    if username and password:
                        credentials['username'] = username
                        credentials['password'] = password
                        credentials['auth_type'] = 'username_password'
                        master_logger.info(f"Using username/password authentication for {site_content_url}")

            # Validate that we have at least one authentication method
            if 'auth_type' not in credentials:
                available_columns = list(row.keys())
                return False, None, f"No valid authentication credentials found. Available columns: {available_columns}"

            master_logger.info(f"Successfully retrieved credentials for {site_content_url}")
            if credentials.get('auth_type') == 'personal_access_token':
                master_logger.debug(f"PAT Name: {credentials.get('personal_access_token_name')}")
            elif credentials.get('auth_type') == 'username_password':
                master_logger.debug(f"Username: {credentials.get('username')}")
            master_logger.debug(f"Server URL: {credentials['tableau_server_url']}")

            return True, credentials, None

        except Exception as e:
            error_msg = f"Error fetching credentials: {str(e)}"
            master_logger.error(error_msg)
            return False, None, error_msg

    def get_all_credentials_by_site_content_url(self, site_content_url: str) -> Tuple[bool, Optional[List[Dict[str, Any]]], Optional[str]]:
        """
        Fetch ALL Tableau credentials for a site_content_url (for fallback support)

        Args:
            site_content_url: The site_content_url to lookup (e.g., "uMetricAnalytics")

        Returns:
            Tuple of (success: bool, credentials_list: list, error_message: str)
            Returns list of all credentials for the site, ordered by priority
        """
        try:
            master_logger.info(f"Fetching ALL credentials for site_content_url: {site_content_url}")

            # Get credentials DataFrame
            df = self._get_credentials_df()

            if df.empty:
                return False, None, "No credentials data found in Google Sheet"

            # Check if site_content_url column exists
            if 'site_content_url' not in df.columns:
                return False, None, f"Column 'site_content_url' not found in sheet. Available columns: {list(df.columns)}"

            # Find ALL matching rows by site_content_url
            matching_rows = df[df['site_content_url'] == site_content_url]

            if matching_rows.empty:
                master_logger.warning(f"No credentials found for site_content_url: {site_content_url}")
                return False, None, f"No credentials found for site_content_url: {site_content_url}"

            master_logger.info(f"Found {len(matching_rows)} credential(s) for site_content_url: {site_content_url}")

            # Build list of credentials
            credentials_list = []

            for idx, row in matching_rows.iterrows():
                # Build credentials dictionary - support both PAT and username/password
                # Strip whitespace from all string fields to prevent issues
                tableau_server = row.get('tableau_server_url', 'https://tableau-aws.uberinternal.com')
                tableau_server = tableau_server.strip() if isinstance(tableau_server, str) else str(tableau_server)

                api_ver = row.get('api_version', '3.19')
                api_ver = api_ver.strip() if isinstance(api_ver, str) else str(api_ver)

                credentials = {
                    'tableau_server_url': tableau_server,
                    'api_version': api_ver,
                    'site_content_url': site_content_url.strip() if isinstance(site_content_url, str) else site_content_url,
                    '_row_index': idx  # Track which row this is for logging
                }

                # Check for PAT authentication fields first
                if 'personal_access_token_name' in row and 'personal_access_token_secret' in row:
                    pat_name = row.get('personal_access_token_name', '').strip() if pd.notna(row.get('personal_access_token_name')) else ''
                    pat_secret = row.get('personal_access_token_secret', '').strip() if pd.notna(row.get('personal_access_token_secret')) else ''

                    if pat_name and pat_secret:
                        credentials['personal_access_token_name'] = pat_name
                        credentials['personal_access_token_secret'] = pat_secret
                        credentials['auth_type'] = 'personal_access_token'
                        master_logger.debug(f"Row {idx}: Found PAT credentials")

                # Check for username/password authentication fields
                if 'auth_type' not in credentials:
                    if 'username' in row and 'password' in row:
                        username = row.get('username', '').strip() if pd.notna(row.get('username')) else ''
                        password = row.get('password', '').strip() if pd.notna(row.get('password')) else ''

                        if username and password:
                            credentials['username'] = username
                            credentials['password'] = password
                            credentials['auth_type'] = 'username_password'
                            master_logger.debug(f"Row {idx}: Found username/password credentials")

                # Only add if we have valid auth credentials
                if 'auth_type' in credentials:
                    credentials_list.append(credentials)
                    master_logger.info(f"✅ Added credential #{len(credentials_list)} for {site_content_url} - auth_type: {credentials['auth_type']}")
                else:
                    master_logger.warning(f"⚠️ Row {idx} has no valid authentication credentials, skipping")

            if not credentials_list:
                return False, None, f"No valid credentials found for site_content_url: {site_content_url}"

            master_logger.info(f"Successfully retrieved {len(credentials_list)} credential(s) for {site_content_url}")
            return True, credentials_list, None

        except Exception as e:
            error_msg = f"Error fetching all credentials: {str(e)}"
            master_logger.error(error_msg)
            return False, None, error_msg

    def get_credentials_by_tableau_url(self, tableau_url: str) -> Tuple[bool, Optional[Dict[str, Any]], Optional[str]]:
        """
        Fetch Tableau PAT credentials by parsing Tableau URL

        Args:
            tableau_url: Full Tableau URL containing site_content_url

        Returns:
            Tuple of (success: bool, credentials_dict: dict, error_message: str)
        """
        try:
            # Extract site_content_url from URL
            site_content_url = self.extract_site_content_url_from_url(tableau_url)

            if not site_content_url:
                return False, None, f"Could not extract site_content_url from URL: {tableau_url}"

            # Fetch credentials by site_content_url
            return self.get_credentials_by_site_content_url(site_content_url)

        except Exception as e:
            error_msg = f"Error fetching credentials by URL: {str(e)}"
            master_logger.error(error_msg)
            return False, None, error_msg

    def get_all_available_sites(self) -> Tuple[bool, Optional[list], Optional[str]]:
        """
        Get list of all available site_content_urls in the sheet

        Returns:
            Tuple of (success: bool, sites_list: list, error_message: str)
        """
        try:
            df = self._get_credentials_df()

            if df.empty:
                return False, None, "No data found in Google Sheet"

            if 'site_content_url' not in df.columns:
                return False, None, f"Column 'site_content_url' not found. Available columns: {list(df.columns)}"

            sites = df['site_content_url'].unique().tolist()
            master_logger.info(f"Found {len(sites)} available sites: {sites}")

            return True, sites, None

        except Exception as e:
            error_msg = f"Error fetching available sites: {str(e)}"
            master_logger.error(error_msg)
            return False, None, error_msg

    def clear_cache(self):
        """Clear the in-memory credentials cache"""
        self._credentials_cache = None
        self._cache_timestamp = None
        master_logger.info("Credentials cache cleared")


# Global instance for easy import
tableau_pat_service = TableauPATService()


def get_tableau_pat_credentials(site_content_url: str) -> Tuple[bool, Optional[Dict[str, Any]], Optional[str]]:
    """
    Convenience function to get Tableau PAT credentials by site_content_url

    Args:
        site_content_url: The site_content_url to lookup

    Returns:
        Tuple of (success: bool, credentials: dict, error_message: str)
    """
    return tableau_pat_service.get_credentials_by_site_content_url(site_content_url)


def get_tableau_pat_credentials_by_url(tableau_url: str) -> Tuple[bool, Optional[Dict[str, Any]], Optional[str]]:
    """
    Convenience function to get Tableau PAT credentials by parsing Tableau URL

    Args:
        tableau_url: Full Tableau URL

    Returns:
        Tuple of (success: bool, credentials: dict, error_message: str)
    """
    return tableau_pat_service.get_credentials_by_tableau_url(tableau_url)


# Test function
if __name__ == "__main__":
    print("Testing TableauPATService...")
    print("=" * 60)

    # Test 1: Get all available sites
    print("\n[TEST 1] Getting all available sites...")
    success, sites, error = tableau_pat_service.get_all_available_sites()
    if success:
        print(f"[OK] Found {len(sites)} sites:")
        for site in sites:
            print(f"  - {site}")
    else:
        print(f"[ERROR] {error}")

    # Test 2: Get credentials by site_content_url
    print("\n[TEST 2] Getting credentials by site_content_url...")
    test_site = "uMetricAnalytics"
    success, credentials, error = get_tableau_pat_credentials(test_site)
    if success:
        print(f"[OK] Retrieved credentials for {test_site}")
        print(f"     PAT Name: {credentials.get('personal_access_token_name')}")
        print(f"     Server URL: {credentials.get('tableau_server_url')}")
        print(f"     API Version: {credentials.get('api_version')}")
        print(f"     Site Content URL: {credentials.get('site_content_url')}")
    else:
        print(f"[ERROR] {error}")

    # Test 3: Get credentials by URL
    print("\n[TEST 3] Getting credentials by Tableau URL...")
    test_url = "https://tableau-aws.uberinternal.com/#/site/uMetricAnalytics/views/Dashboard"
    success, credentials, error = get_tableau_pat_credentials_by_url(test_url)
    if success:
        print(f"[OK] Retrieved credentials from URL: {test_url}")
        print(f"     PAT Name: {credentials.get('personal_access_token_name')}")
        print(f"     Site Content URL: {credentials.get('site_content_url')}")
    else:
        print(f"[ERROR] {error}")

    # Test 4: Extract site_content_url from various URL patterns
    print("\n[TEST 4] Testing URL parsing...")
    test_urls = [
        "https://tableau-aws.uberinternal.com/#/site/uMetricAnalytics/views/Dashboard",
        "https://tableau-aws.uberinternal.com/t/uMetricAnalytics/views/Dashboard",
        "https://tableau-aws.uberinternal.com/site/uMetricAnalytics/views/Dashboard"
    ]
    for url in test_urls:
        site = tableau_pat_service.extract_site_content_url_from_url(url)
        print(f"  URL: {url}")
        print(f"  Extracted: {site}")
        print()