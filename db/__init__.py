"""
Database __init__ for convenience imports
"""

from .supabase_client import SupabaseClient, get_supabase
from . import queries

__all__ = ['SupabaseClient', 'get_supabase', 'queries']
