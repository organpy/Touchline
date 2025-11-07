"""
Supabase singleton client wrapper
Provides a single, reusable connection instance across the entire bot
"""

import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)

try:
    from supabase import create_client, Client
except ImportError:
    logger.error("❌ supabase-py not installed. Run: pip install supabase")
    raise

class SupabaseClient:
    """Singleton Supabase client"""
    _instance: Optional['SupabaseClient'] = None
    _client: Optional[Client] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialize()
        return cls._instance

    def _initialize(self):
        """Initialize Supabase client"""
        url = os.getenv('SUPABASE_URL')
        key = os.getenv('SUPABASE_KEY')

        if not url or not key:
            logger.error("❌ SUPABASE_URL or SUPABASE_KEY not found in .env")
            raise ValueError("Missing Supabase credentials in .env")

        try:
            self._client = create_client(url, key)
            logger.info("✅ Supabase client initialized")
        except Exception as e:
            logger.error(f"❌ Failed to initialize Supabase client: {e}")
            raise

    def get_client(self) -> Client:
        """Get the Supabase client instance"""
        if self._client is None:
            raise RuntimeError("Supabase client not initialized")
        return self._client

    @classmethod
    def reset(cls):
        """Reset singleton (for testing)"""
        cls._instance = None
        cls._client = None


def get_supabase() -> Client:
    """Convenience function to get Supabase client"""
    return SupabaseClient().get_client()
