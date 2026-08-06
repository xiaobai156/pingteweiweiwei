from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
TARGETS_FILE = ROOT / "targets.json"
OUTPUT_DIR = Path(r"C:\Users\Administrator\Desktop\每天工具\爬虫合集\大围杀号生肖数据统一归纳")
RECENT_10_CACHE_FILE = ROOT / "recent_10_cache.json"
RECENT_10_WINDOW = 10
DIGIT_TRANSLATION = str.maketrans("０１２３４５６７８９", "0123456789")
STRUCTURED_JSON_BEGIN = "__TAIL_STRUCTURED_JSON_BEGIN__"
STRUCTURED_JSON_END = "__TAIL_STRUCTURED_JSON_END__"
DOCUMENT_BEGIN = "__TAIL_DOCUMENT_BEGIN__"
DOCUMENT_END = "__TAIL_DOCUMENT_END__"
