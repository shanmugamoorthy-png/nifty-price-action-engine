import os
from dotenv import load_dotenv
load_dotenv()

DHAN_BASE_URL = "https://api.dhan.co/v2"
SECURITY_ID = os.getenv("SECURITY_ID", "13")
EXCHANGE_SEGMENT = os.getenv("EXCHANGE_SEGMENT", "IDX_I")
INSTRUMENT = os.getenv("INSTRUMENT", "INDEX")
DATA_DIR = os.getenv("DATA_DIR", "/var/data" if os.getenv("RENDER", "") else "data")
TIMEZONE = "Asia/Kolkata"
