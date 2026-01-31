# app/db/config.py
"""
MongoDB configuration using mongoengine
"""

import os
from mongoengine import connect, disconnect
from typing import Optional


class MongoDBConfig:
    """MongoDB configuration"""
    
    def __init__(
        self,
        db_name: str = "secure_editor",
        host: str = "localhost",
        port: int = 27017,
        username: Optional[str] = None,
        password: Optional[str] = None,
        replica_set: Optional[str] = None,
        tls: bool = False,
    ):
        self.db_name = db_name
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.replica_set = replica_set
        self.tls = tls
    
    def get_connection_string(self) -> str:
        """Build MongoDB connection string"""
        if self.username and self.password:
            return (
                f"mongodb://{self.username}:{self.password}@"
                f"{self.host}:{self.port}/{self.db_name}"
                f"?tls={str(self.tls).lower()}"
                f"{f'&replicaSet={self.replica_set}' if self.replica_set else ''}"
            )
        else:
            return f"mongodb://{self.host}:{self.port}/{self.db_name}"
    
    def connect(self):
        """Connect to MongoDB"""
        try:
            connection_string = self.get_connection_string()
            
            if self.username and self.password:
                connect(
                    self.db_name,
                    host=self.host,
                    port=self.port,
                    username=self.username,
                    password=self.password,
                    authentication_source='admin',
                    tls=self.tls,
                    replica_set=self.replica_set
                )
            else:
                connect(
                    self.db_name,
                    host=self.host,
                    port=self.port
                )
            
            print(f"✅ Connected to MongoDB: {self.db_name}")
        except Exception as e:
            print(f"❌ Failed to connect to MongoDB: {e}")
            raise
    
    @staticmethod
    def disconnect():
        """Disconnect from MongoDB"""
        disconnect()


def get_mongo_config_from_env() -> MongoDBConfig:
    """
    Create MongoDB config from environment variables
    
    Environment variables:
        MONGODB_URI: Full connection URI (overrides other settings)
        MONGODB_DB: Database name (default: secure_editor)
        MONGODB_HOST: Host (default: localhost)
        MONGODB_PORT: Port (default: 27017)
        MONGODB_USERNAME: Username (optional)
        MONGODB_PASSWORD: Password (optional)
        MONGODB_REPLICA_SET: Replica set name (optional)
        MONGODB_TLS: Enable TLS (default: false)
    """
    
    # Check for full URI first
    uri = os.getenv("MONGO_URI")
    if uri:
        # Parse connection string
        from urllib.parse import urlparse
        parsed = urlparse(uri)
        
        return MongoDBConfig(
            db_name=parsed.path.lstrip('/') or "secure_editor",
            host=parsed.hostname or "localhost",
            port=parsed.port or 27017,
            username=parsed.username,
            password=parsed.password,
            tls="tls" in uri.lower()
        )
    
    # Build from individual env vars
    return MongoDBConfig(
        db_name=os.getenv("MONGODB_DB", "secure_editor"),
        host=os.getenv("MONGODB_HOST", "localhost"),
        port=int(os.getenv("MONGODB_PORT", "27017")),
        username=os.getenv("MONGODB_USERNAME"),
        password=os.getenv("MONGODB_PASSWORD"),
        replica_set=os.getenv("MONGODB_REPLICA_SET"),
        tls=os.getenv("MONGODB_TLS", "false").lower() == "true"
    )
