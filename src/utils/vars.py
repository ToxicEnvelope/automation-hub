import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# -------------------- Blob config (ENV VARS) --------------------
# Use these in your AutomationHub container/app settings:
#   REPORTS_STORAGE_ACCOUNT=allureautotests     (optional; default "allureautotests")
#   REPORTS_CONTAINER=reports                   (optional; default "reports")
#   REPORTS_PREFIX=runs                         (optional; default "runs")
REPORTS_STORAGE_ACCOUNT = os.getenv("REPORTS_STORAGE_ACCOUNT", "allureautotests").strip()
REPORTS_CONTAINER = os.getenv("REPORTS_CONTAINER", "reports").strip()
REPORTS_PREFIX = os.getenv("REPORTS_PREFIX", "runs").strip().strip("/")  # "reports" or "runs" etc.

# -------------------- AI config (ENV VARS) --------------------
# FAILED_STATUSES = {"failed", "broken", "error"}
# MAX_TEXT_CHARS = int(os.getenv("AI_SUMMARY_MAX_TEXT_CHARS", "1800"))
# MAX_FAILURES = int(os.getenv("AI_SUMMARY_MAX_FAILURES", "40"))
# MAX_BLOBS_TO_SCAN = int(os.getenv("AI_SUMMARY_MAX_TEST_CASE_BLOBS", "350"))
# AI_MODEL_PATH = int(os.getenv("AI_MODEL_PATH", "/app/models/qwen2.5-7b-instruct-q5_k_m-00001-of-00002.gguf"))
# AI_MODEL_NAME = int(os.getenv("AI_MODEL_NAME", "qwen2.5-7b-instruct-q5_k_m"))
# AI_MODEL_CONTEXT_TOKENS = int(os.getenv("AI_MODEL_CONTEXT_TOKENS", "32768"))
FAILED_STATUSES = {"failed", "broken", "error"}
MAX_TEXT_CHARS = int(os.getenv("AI_SUMMARY_MAX_TEXT_CHARS", "1800"))
MAX_FAILURES = int(os.getenv("AI_SUMMARY_MAX_FAILURES", "40"))
MAX_BLOBS_TO_SCAN = int(os.getenv("AI_SUMMARY_MAX_TEST_CASE_BLOBS", "350"))
AI_MODEL_PATH = os.getenv("AI_MODEL_PATH", "/app/models/qwen2.5-7b-instruct-q5_k_m-00001-of-00002.gguf")
AI_MODEL_NAME = os.getenv("AI_MODEL_NAME", "qwen2.5-7b-instruct-q5_k_m")
AI_MODEL_CONTEXT_TOKENS = int(os.getenv("AI_MODEL_CONTEXT_TOKENS", "32768"))
