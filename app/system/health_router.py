import httpx
import asyncio
from datetime import datetime, timedelta
from motor.motor_asyncio import AsyncIOMotorDatabase

# Configuration for the target servers
SERVERS = {
    "auth": "http://auth.sidhi.xyz/health",
    "softjudge": "https://lumterix-judge.onrender.com/health",
    "hardjudge": "https://hdl-engine.onrender.com/health"
}

async def monitor_heartbeat(db: AsyncIOMotorDatabase):
    """
    Background worker that pings dependencies every 5 minutes.
    """
    while True:
        record = {
            "timestamp": datetime.utcnow(),
            "status": {},
            "latency_ms": {}
        }
        
        async with httpx.AsyncClient() as client:
            # 1. Ping External Servers
            for name, url in SERVERS.items():
                try:
                    start = datetime.utcnow()
                    resp = await client.get(url, timeout=5.0)
                    is_up = resp.status_code == 200
                    record["status"][f"{name}_server"] = "UP" if is_up else "DOWN"
                    record["latency_ms"][f"{name}_server"] = (datetime.utcnow() - start).total_seconds() * 1000
                except Exception:
                    record["status"][f"{name}_server"] = "DOWN"

            # 2. Log Internal Service Status
            # If this logic is executing, the Main Server is alive
            internal_status = "UP" 
            record["status"]["internal_services"] = {
                "courses": internal_status,
                "classrooms": internal_status,
                "plagiarism": internal_status,
                "cli_server": internal_status,
                "cert_server": internal_status
            }

        # Store the snap in MongoDB
        await db.system_health_records.insert_one(record)
        
        # Wait 5 minutes before next pulse
        await asyncio.sleep(300)