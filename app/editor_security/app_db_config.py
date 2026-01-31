# app/db/config.py
"""
MongoDB configuration using mongoengine
PRODUCTION VERSION — URI ONLY
"""

import os
from mongoengine import connect, disconnect


class MongoDBConfig:
    """MongoDB configuration (URI only, production-safe)"""

    def __init__(self, uri: str):
        if not uri:
            raise RuntimeError(
                "❌ MONGO_URI not set. Production requires a MongoDB connection string."
            )

        self.uri = uri

    def connect(self):
        """Connect using MongoDB URI only"""
        try:
            connect(host=self.uri)
            print("✅ MongoDB connected using URI")
        except Exception as e:
            print(f"❌ MongoDB connection failed: {e}")
            raise

    @staticmethod
    def disconnect():
        disconnect()


def get_mongo_config_from_env() -> MongoDBConfig:
    """
    REQUIRED:
        MONGO_URI=mongodb+srv://user:pass@cluster/db
    """

    uri = os.getenv("MONGO_URI")

    if not uri:
        raise RuntimeError(
            "MONGO_URI environment variable is mandatory. "
            "Example: mongodb+srv://user:pass@cluster/db"
        )

    return MongoDBConfig(uri)
