"""
Production-Ready Gemini API Key Manager with MongoDB Persistence
Database: lumetrics_db
Collection: gemini_key_stats
"""

import os
import time
import threading
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from collections import deque
import google.generativeai as genai
from pymongo import MongoClient, ASCENDING
from pymongo.errors import ConnectionFailure, OperationFailure
import json


@dataclass
class TokenUsage:
    """Track token usage for cost calculation"""
    input_tokens: int = 0
    output_tokens: int = 0
    
    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens
    
    def cost_usd(self, is_paid: bool = False) -> float:
        """Calculate cost in USD for 2.5 Flash-Lite"""
        # Paid tier has same pricing as free tier for 2.5 Flash-Lite
        input_cost = (self.input_tokens / 1_000_000) * 0.10
        output_cost = (self.output_tokens / 1_000_000) * 0.40
        return input_cost + output_cost
    
    def cost_inr(self, is_paid: bool = False) -> float:
        """Calculate cost in INR (1 USD = 90 INR)"""
        return self.cost_usd(is_paid) * 90


@dataclass
class KeyStats:
    """Track usage stats for each API key with MongoDB persistence"""
    key_name: str
    is_paid: bool
    requests_this_minute: deque = field(default_factory=lambda: deque(maxlen=300))
    requests_today: int = 0
    total_requests_lifetime: int = 0
    last_reset: datetime = field(default_factory=datetime.now)
    last_429_time: Optional[float] = None
    consecutive_429s: int = 0
    
    # Token tracking
    tokens_today: TokenUsage = field(default_factory=TokenUsage)
    tokens_lifetime: TokenUsage = field(default_factory=TokenUsage)
    
    @property
    def rpm_limit(self) -> int:
        """Requests per minute limit"""
        return 300 if self.is_paid else 15
    
    @property
    def rpd_limit(self) -> int:
        """Requests per day limit"""
        return 1000  # Same for both tiers on Flash-Lite
    
    def can_make_request(self) -> bool:
        """Check if this key can make a request right now"""
        now = time.time()
        
        # Remove requests older than 60 seconds
        while self.requests_this_minute and now - self.requests_this_minute[0] > 60:
            self.requests_this_minute.popleft()
        
        # Check RPM limit
        if len(self.requests_this_minute) >= self.rpm_limit:
            return False
        
        # Reset daily counter if new day
        current_date = datetime.now().date()
        if self.last_reset.date() != current_date:
            self.requests_today = 0
            self.tokens_today = TokenUsage()
            self.last_reset = datetime.now()
        
        # Check RPD limit
        if self.requests_today >= self.rpd_limit:
            return False
        
        # Exponential backoff for 429s
        if self.last_429_time and now - self.last_429_time < min(60 * (2 ** self.consecutive_429s), 300):
            return False
        
        return True
    
    def record_request(self, input_tokens: int, output_tokens: int):
        """Record a successful request with token usage"""
        self.requests_this_minute.append(time.time())
        self.requests_today += 1
        self.total_requests_lifetime += 1
        self.consecutive_429s = 0
        
        # Update token stats
        self.tokens_today.input_tokens += input_tokens
        self.tokens_today.output_tokens += output_tokens
        self.tokens_lifetime.input_tokens += input_tokens
        self.tokens_lifetime.output_tokens += output_tokens
    
    def record_429(self):
        """Record a 429 error"""
        self.last_429_time = time.time()
        self.consecutive_429s += 1
    
    def get_wait_time(self) -> float:
        """Get seconds to wait before this key can be used"""
        now = time.time()
        
        # 429 backoff
        if self.last_429_time:
            wait = min(60 * (2 ** self.consecutive_429s), 300)
            elapsed = now - self.last_429_time
            if elapsed < wait:
                return wait - elapsed
        
        # RPM limit wait
        if len(self.requests_this_minute) >= self.rpm_limit and self.requests_this_minute:
            oldest = self.requests_this_minute[0]
            wait = 60 - (now - oldest)
            if wait > 0:
                return wait
        
        return 0
    
    def to_dict(self) -> dict:
        """Convert to dict for MongoDB storage"""
        return {
            'key_name': self.key_name,
            'is_paid': self.is_paid,
            'requests_today': self.requests_today,
            'total_requests_lifetime': self.total_requests_lifetime,
            'last_reset': self.last_reset,
            'last_429_time': self.last_429_time,
            'consecutive_429s': self.consecutive_429s,
            'tokens_today': asdict(self.tokens_today),
            'tokens_lifetime': asdict(self.tokens_lifetime),
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> 'KeyStats':
        """Create from MongoDB document"""
        stats = cls(
            key_name=data['key_name'],
            is_paid=data['is_paid']
        )
        stats.requests_today = data.get('requests_today', 0)
        stats.total_requests_lifetime = data.get('total_requests_lifetime', 0)
        stats.last_reset = data.get('last_reset', datetime.now())
        stats.last_429_time = data.get('last_429_time')
        stats.consecutive_429s = data.get('consecutive_429s', 0)
        
        # Load token stats
        if 'tokens_today' in data:
            stats.tokens_today = TokenUsage(**data['tokens_today'])
        if 'tokens_lifetime' in data:
            stats.tokens_lifetime = TokenUsage(**data['tokens_lifetime'])
        
        return stats


class SmartGeminiManager:
    """
    Production-ready Gemini API key manager with:
    - MongoDB persistence
    - Thread-safe operations
    - Token counting & cost tracking
    - Intelligent key rotation
    - Zero wasteful 429s
    """
    
    def __init__(
        self,
        free_keys: List[str],
        paid_key: str,
        model_name: str = "gemini-2.5-flash-lite",
        mongo_uri: str = "mongodb://localhost:27017/",
        db_name: str = "lumetrics_db",
        auto_persist_interval: int = 60
    ):
        """
        Initialize the manager
        
        Args:
            free_keys: List of free tier API keys
            paid_key: Paid tier API key
            model_name: Gemini model to use
            mongo_uri: MongoDB connection URI
            db_name: Database name (default: lumetrics_db)
            auto_persist_interval: Auto-save interval in seconds (0 to disable)
        """
        self.free_keys = free_keys
        self.paid_key = paid_key
        self.model_name = model_name
        self.lock = threading.RLock()  # Thread safety
        self.free_key_index = 0
        
        # Initialize MongoDB
        self.mongo_client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
        self.db = self.mongo_client[db_name]
        self.collection = self.db['gemini_key_stats']
        
        # Create indexes for better query performance
        self.collection.create_index([('key_name', ASCENDING)], unique=True)
        
        # Test MongoDB connection
        try:
            self.mongo_client.admin.command('ping')
            print("✓ MongoDB connected successfully")
        except ConnectionFailure:
            print("⚠️  MongoDB connection failed - running without persistence")
            self.collection = None
        
        # Load or create stats
        self.free_keys_stats = []
        for i, key in enumerate(free_keys):
            key_name = f"free_{i+1}"
            stats = self._load_or_create_stats(key_name, is_paid=False)
            self.free_keys_stats.append(stats)
        
        self.paid_key_stats = self._load_or_create_stats("paid", is_paid=True)
        
        # Initialize models with SEPARATE configurations
        self.models: Dict[str, genai.GenerativeModel] = {}
        self._initialize_models()
        
        # Auto-persistence thread
        self.auto_persist = auto_persist_interval > 0
        if self.auto_persist:
            self._start_persistence_thread(auto_persist_interval)
    
    def _initialize_models(self):
        """Initialize each model with its own API key"""
        for i, key in enumerate(self.free_keys):
            key_name = f"free_{i+1}"
            # Each model needs to be created with its specific key
            self.models[key_name] = (key, self.model_name)
        
        self.models["paid"] = (self.paid_key, self.model_name)
    
    def _load_or_create_stats(self, key_name: str, is_paid: bool) -> KeyStats:
        """Load stats from MongoDB or create new"""
        if self.collection is None:
            return KeyStats(key_name=key_name, is_paid=is_paid)
        
        try:
            doc = self.collection.find_one({'key_name': key_name})
            if doc:
                return KeyStats.from_dict(doc)
        except Exception as e:
            print(f"⚠️  Error loading stats for {key_name}: {e}")
        
        return KeyStats(key_name=key_name, is_paid=is_paid)
    
    def _save_stats(self, stats: KeyStats):
        """Save stats to MongoDB"""
        if self.collection is None:
            return
        
        try:
            self.collection.update_one(
                {'key_name': stats.key_name},
                {'$set': stats.to_dict()},
                upsert=True
            )
        except Exception as e:
            print(f"⚠️  Error saving stats for {stats.key_name}: {e}")
    
    def _persist_all_stats(self):
        """Persist all stats to MongoDB"""
        with self.lock:
            for stats in self.free_keys_stats:
                self._save_stats(stats)
            self._save_stats(self.paid_key_stats)
    
    def _start_persistence_thread(self, interval: int):
        """Start background thread for auto-persistence"""
        def persist_loop():
            while self.auto_persist:
                time.sleep(interval)
                self._persist_all_stats()
        
        thread = threading.Thread(target=persist_loop, daemon=True)
        thread.start()
        print(f"✓ Auto-persistence enabled (every {interval}s)")
    
    def _get_next_available_key(self) -> Optional[Tuple[str, KeyStats, Tuple[str, str]]]:
        """
        Smart key selection with thread safety
        Returns: (api_key, stats, (api_key, model_name)) or None
        """
        with self.lock:
            # Try free keys in round-robin
            checked_free = 0
            while checked_free < len(self.free_keys_stats):
                stats = self.free_keys_stats[self.free_key_index]
                if stats.can_make_request():
                    key = self.free_keys[self.free_key_index]
                    model_info = self.models[stats.key_name]
                    self.free_key_index = (self.free_key_index + 1) % len(self.free_keys)
                    return key, stats, model_info
                
                self.free_key_index = (self.free_key_index + 1) % len(self.free_keys)
                checked_free += 1
            
            # Try paid key
            if self.paid_key_stats.can_make_request():
                return self.paid_key, self.paid_key_stats, self.models["paid"]
            
            return None
    
    def _get_min_wait_time(self) -> float:
        """Get minimum wait time across all keys"""
        with self.lock:
            wait_times = [stats.get_wait_time() for stats in self.free_keys_stats]
            wait_times.append(self.paid_key_stats.get_wait_time())
            return min(wait_times)
    
    def _estimate_tokens(self, text: str) -> int:
        """Rough token estimation (4 chars ≈ 1 token)"""
        return len(text) // 4
    
    def run_gemini(self, prompt: str, max_retries: int = 3) -> str:
        """
        Execute prompt with intelligent key management
        
        Args:
            prompt: The prompt to send to Gemini
            max_retries: Maximum retry attempts
            
        Returns:
            Generated text response
            
        Raises:
            Exception: If all retries fail
        """
        for attempt in range(max_retries):
            result = self._get_next_available_key()
            
            if result is None:
                wait_time = self._get_min_wait_time()
                if wait_time > 0:
                    print(f"⏳ All keys rate-limited. Waiting {wait_time:.1f}s...")
                    time.sleep(wait_time + 0.1)
                    continue
                else:
                    raise Exception("All API keys exhausted with no recovery time")
            
            api_key, stats, (key_to_use, model_name) = result
            
            try:
                # Configure with the specific key and create model instance
                genai.configure(api_key=key_to_use)
                model = genai.GenerativeModel(model_name)
                
                # Make request
                response = model.generate_content(prompt)
                
                # Extract token usage (Gemini API provides this)
                input_tokens = self._estimate_tokens(prompt)
                output_tokens = self._estimate_tokens(response.text)
                
                # Try to get actual token counts if available
                try:
                    if hasattr(response, 'usage_metadata'):
                        input_tokens = response.usage_metadata.prompt_token_count
                        output_tokens = response.usage_metadata.candidates_token_count
                except:
                    pass
                
                # Record success
                with self.lock:
                    stats.record_request(input_tokens, output_tokens)
                
                # Log usage
                key_type = "💰 PAID" if stats.is_paid else f"🆓 FREE-{stats.key_name.split('_')[1]}"
                cost = stats.tokens_today.cost_inr(stats.is_paid)
                print(f"✓ {key_type} | RPM: {len(stats.requests_this_minute)}/{stats.rpm_limit} | "
                      f"Daily: {stats.requests_today}/{stats.rpd_limit} | "
                      f"Tokens: {input_tokens}+{output_tokens} | Cost Today: ₹{cost:.4f}")
                
                return response.text.strip()
                
            except Exception as e:
                error_str = str(e)
                
                # Handle 429 errors
                if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str or "quota" in error_str.lower():
                    with self.lock:
                        stats.record_429()
                    print(f"⚠️  {stats.key_name} hit rate limit (429), trying next key...")
                    continue
                
                # Other errors
                print(f"❌ Error with {stats.key_name}: {error_str}")
                raise
        
        raise Exception(f"Failed after {max_retries} attempts across all keys")
    
    def get_stats(self, show_costs: bool = True) -> str:
        """Get current usage statistics"""
        with self.lock:
            lines = ["📊 API Key Usage Stats:", ""]
            
            total_today = TokenUsage()
            total_lifetime = TokenUsage()
            
            for i, stats in enumerate(self.free_keys_stats, 1):
                status = "✓" if stats.can_make_request() else "✗"
                cost_today = stats.tokens_today.cost_inr()
                cost_lifetime = stats.tokens_lifetime.cost_inr()
                
                total_today.input_tokens += stats.tokens_today.input_tokens
                total_today.output_tokens += stats.tokens_today.output_tokens
                total_lifetime.input_tokens += stats.tokens_lifetime.input_tokens
                total_lifetime.output_tokens += stats.tokens_lifetime.output_tokens
                
                lines.append(
                    f"  Free Key {i}: {status} | "
                    f"RPM: {len(stats.requests_this_minute)}/{stats.rpm_limit} | "
                    f"Daily: {stats.requests_today}/{stats.rpd_limit} | "
                    f"Lifetime: {stats.total_requests_lifetime:,}"
                )
                if show_costs:
                    lines.append(
                        f"    Tokens Today: {stats.tokens_today.total_tokens:,} "
                        f"(₹{cost_today:.4f}) | "
                        f"Lifetime: {stats.tokens_lifetime.total_tokens:,} "
                        f"(₹{cost_lifetime:.2f})"
                    )
            
            # Paid key stats
            stats = self.paid_key_stats
            status = "✓" if stats.can_make_request() else "✗"
            cost_today = stats.tokens_today.cost_inr(True)
            cost_lifetime = stats.tokens_lifetime.cost_inr(True)
            
            total_today.input_tokens += stats.tokens_today.input_tokens
            total_today.output_tokens += stats.tokens_today.output_tokens
            total_lifetime.input_tokens += stats.tokens_lifetime.input_tokens
            total_lifetime.output_tokens += stats.tokens_lifetime.output_tokens
            
            lines.append("")
            lines.append(
                f"  Paid Key:   {status} | "
                f"RPM: {len(stats.requests_this_minute)}/{stats.rpm_limit} | "
                f"Daily: {stats.requests_today}/{stats.rpd_limit} | "
                f"Lifetime: {stats.total_requests_lifetime:,}"
            )
            if show_costs:
                lines.append(
                    f"    Tokens Today: {stats.tokens_today.total_tokens:,} "
                    f"(₹{cost_today:.4f}) | "
                    f"Lifetime: {stats.tokens_lifetime.total_tokens:,} "
                    f"(₹{cost_lifetime:.2f})"
                )
            
            # Total stats
            if show_costs:
                lines.append("")
                lines.append("💰 Total Costs:")
                lines.append(f"  Today: ₹{total_today.cost_inr():.2f} ({total_today.total_tokens:,} tokens)")
                lines.append(f"  Lifetime: ₹{total_lifetime.cost_inr():.2f} ({total_lifetime.total_tokens:,} tokens)")
            
            return "\n".join(lines)
    
    def close(self):
        """Cleanup - persist stats and close connections"""
        self.auto_persist = False
        self._persist_all_stats()
        if self.mongo_client:
            self.mongo_client.close()
        print("✓ Manager closed, stats persisted")


# ============= USAGE EXAMPLE =============

def create_manager(num_free_keys: int = 7) -> SmartGeminiManager:
    """
    Create the manager with your API keys
    
    Args:
        num_free_keys: Number of free tier keys (default: 7)
    """
    free_keys = [os.getenv(f"GEMINI_FREE_{i+1}") for i in range(num_free_keys)]
    paid_key = os.getenv("GEMINI_PAID")
    mongo_uri = os.getenv("MONGODB_URI", "mongodb://localhost:27017/")
    
    # Validate keys
    if not all(free_keys):
        raise ValueError(f"Set GEMINI_FREE_1 through GEMINI_FREE_{num_free_keys} environment variables")
    if not paid_key:
        raise ValueError("Set GEMINI_PAID environment variable")
    
    return SmartGeminiManager(
        free_keys=free_keys,
        paid_key=paid_key,
        model_name="gemini-2.5-flash-lite",
        mongo_uri=mongo_uri,
        db_name="lumetrics_db",
        auto_persist_interval=60  # Auto-save every 60 seconds
    )


# Global manager instance
_manager: Optional[SmartGeminiManager] = None

def run_gemini(prompt: str) -> str:
    """Drop-in replacement for your original function"""
    global _manager
    if _manager is None:
        _manager = create_manager()
    return _manager.run_gemini(prompt)

