# 🚀 FINAL INTEGRATION GUIDE

## ✅ FILES READY (13 total)

All files are corrected to match your main.py auth patterns!

## 📁 FILE PLACEMENT

```
your-backend/
└── app/
    └── courses/
        ├── __init__.py
        ├── app.py                  (✅ Fixed auth)
        ├── config.py               (✅ NEW - Judge URLs)
        ├── models.py
        ├── database.py
        ├── schemas.py
        ├── course_router.py        (✅ Fixed deps)
        ├── enrollment_router.py    (✅ Fixed deps)
        ├── submission_router.py    (✅ Fixed deps + BOTH judges)
        ├── leaderboard_router.py   (✅ Fixed deps)
        ├── certificate_router.py   (✅ Fixed deps)
        └── practice_router.py      (✅ Fixed deps)
```

## 🔧 CHANGES MADE

### 1. **AUTH FIXED** (Matches your main.py)
```python
# OLD (won't work):
from app.admin.hardened_firebase_auth import get_current_user

# NEW (matches your system):
from app.ai.client_bound_guard import verify_client_bound_request

# Usage:
async def get_current_user_id(user: str = Depends(verify_client_bound_request)):
    # user is the public_key from your signature verification
    return user
```

### 2. **DATABASE ACCESS** (No more lambda)
```python
# Gets db from main module
def get_db_instance():
    from main import db
    return db
```

### 3. **BOTH JUDGES INTEGRATED**
- **Software Judge**: Python, C, C++ (existing judge with async polling)
- **Hardware Judge**: Verilog, VHDL, SystemVerilog (Go service, sync)

Language routing in `submission_router.py`:
```python
if language in ["c", "cpp", "python"]:
    await judge_software(...)  # Your existing judge
elif language in ["verilog", "vhdl", "systemverilog"]:
    await judge_hardware(...)  # Go HDL evaluator
```

## 🌐 ENVIRONMENT SETUP

Add to your `.env`:
```bash
# Software Judge
JUDGE_API_URL=http://your-judge-url:8000
JUDGE_API_KEY=your_api_key

# Hardware Judge
HDL_JUDGE_URL=http://your-hdl-judge-url:8080
```

## 📝 UPDATE main.py

Your main.py already has these lines (good!):
```python
from app.courses.app import setup_course_routes, startup_course_system
from app.courses.practice_router import router as practice_router

# In startup:
await startup_course_system()

# Router registration:
setup_course_routes(app)  # Registers all 5 course routers
app.include_router(practice_router)
```

✅ **NO CHANGES NEEDED** - Your main.py is already correct!

## 🎯 ENDPOINT ROUTES

All endpoints will be under `/courses/` prefix:

- `/courses/api/courses/*` - Course management (7 endpoints)
- `/courses/api/enrollments/*` - Enrollment (4 endpoints)
- `/courses/api/submissions/*` - Submissions (3 endpoints)
- `/courses/api/leaderboards/*` - Leaderboards (7 endpoints)
- `/courses/api/certificates/*` - Certificates (4 endpoints)
- `/api/practice/*` - Practice samples (4 endpoints)

**Total: 29 endpoints**

## 🔌 JUDGE INTEGRATION DETAILS

### Software Judge (Existing)
```
Endpoint: POST {JUDGE_API_URL}/judge
Method: Async (poll for results)
Header: X-API-Key: {JUDGE_API_KEY}
Languages: c, cpp, python, java, javascript
```

### Hardware Judge (Go Service)
```
Endpoint: POST {HDL_JUDGE_URL}/evaluate
Method: Sync (immediate response)
Languages: verilog, vhdl, systemverilog
```

## ✅ VERIFICATION CHECKLIST

- [ ] Place all 13 files in `app/courses/`
- [ ] Add env vars to `.env`
- [ ] Restart server
- [ ] Test endpoint: `GET /courses/api/courses/`
- [ ] Test submission (software): Submit C code
- [ ] Test submission (hardware): Submit Verilog code

## 🐛 DEBUGGING

If issues occur:

1. **Import errors**: Check `app/courses/__init__.py` exists
2. **Auth errors**: Verify signature headers are sent
3. **Judge errors**: Check JUDGE_API_URL and HDL_JUDGE_URL in logs
4. **DB errors**: Verify MongoDB connection

## 📊 MONITORING

Watch logs for:
```
✅ Course system initialized
✅ Course routes registered
✅ Course system indexes created
```

## 🎉 READY TO DEPLOY!

All files match your existing patterns. No breaking changes!
