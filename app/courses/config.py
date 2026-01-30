"""
Course System Configuration
Judge service URLs and settings
"""

import os

# Software Judge (Python, C, C++)
SOFTWARE_JUDGE_URL = os.getenv("JUDGE_API_URL")
SOFTWARE_JUDGE_KEY = os.getenv("JUDGE_API_KEY")

# Hardware Judge (Verilog, VHDL, SystemVerilog)
HARDWARE_JUDGE_URL = os.getenv("HDL_JUDGE_URL")

# Judge timeout settings
JUDGE_TIMEOUT_SECONDS = 120
JUDGE_POLL_INTERVAL_SECONDS = 1
JUDGE_MAX_POLL_ATTEMPTS = 60
