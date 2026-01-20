KNOWLEDGE_BASE = {
    "payments": """
    Payment System:
    - Users can purchase tiers using Razorpay
    -If they face any payments related issue let them buy a ticket and contact the admins
    """,
    
    "cloud_sync": """
    Cloud Sync Features:
    - Users can push code files to cloud vault
    - Supports: .py, .ipynb, .c, .cpp, .h, .java, .js
    - Max payload: It is more than enough
    - For maintaining the users privacy they must run lum sync in their terminals after they made a chnange
    -Auto push may not work perfect on all systems so we recommend manual pushes for doing manual pushes visit the CLI docs page
    """,
    
    "personalization": """
    Coding Style Personalization:
    - 20-question quiz to customize AI coding style
    - Affects: naming, comments, error handling, etc.
    - Can update/delete preferences anytime
    """,
    
    "quotas": """
    {
    "free": {
        "commands": {
            "ask": 5,
            "explain": 0,
            "write": 0,
            "fix": 2,
            "trace": 1,
            "diff": 1,
            "algo": 1,
            "format": 2
        },
        "inject": 0,
        "cells": 0,
        "pdf": 1,
        "convo": 0
    },
    "hero": {
        "commands": {
            "ask": 30,
            "explain": 20,
            "write": 15,
            "fix": 20,
            "trace": 3,
            "diff": 20,
            "algo": 25,
            "format": 30
        },
        "inject": 8,
        "cells": 8,
        "pdf": 8,
        "convo": 0
    },
    "dominator": {
        "commands": {
            "ask": 50,
            "explain": 40,
            "write": 30,
            "fix": 30,
            "trace": 10,
            "diff": 40,
            "algo": 50,
            "format": 60
        },
        "inject": 13,
        "cells": 13,
        "pdf": 13,
        "convo": 0
    }
}
For quota usage check go to history page in the dashbord
    """
}