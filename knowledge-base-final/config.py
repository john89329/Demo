import os

# ── 安全警告 ──
# 请勿在此文件中硬编码 API Key！
# Key 应通过下列方式之一提供（优先级从高到低）：
#   1. 前端设置页面输入（推荐）
#   2. 环境变量 DEEPSEEK_API_KEY / EMBEDDING_API_KEY
#   3. 此处的空字符串默认值（服务端不存储，每次请求由前端传入）

CURRENT_KB = "default"
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
EMBEDDING_API_KEY = os.environ.get("EMBEDDING_API_KEY", "")
EMBEDDING_BASE_URL = "https://api.siliconflow.cn/v1"
EMBEDDING_MODEL = "BAAI/bge-m3"
CHAT_MODEL = "deepseek-v4-flash"
CHUNK_SIZE = 512
CHUNK_OVERLAP = 64
TOP_K_RESULTS = 10

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INDEX_DIR = os.path.join(BASE_DIR, "index")

# FAISS index tuning
FAISS_NLIST = 10000       # IVF centroids
FAISS_PQ_M = 32           # PQ sub-vectors (32 bytes/vector after compression)
FAISS_PQ_BITS = 8         # bits per sub-vector
FAISS_TRAIN_THRESHOLD = 50000  # min vectors before PQ training
FAISS_NPROBE = 100        # IVF search probes

# Parallel indexing / search / generation
INDEX_WORKERS = 4  # 并行解析文件的线程数
PARSE_TIMEOUT = 300  # 单个文件解析超时秒数（OCR 可能较慢）
PIPELINE_QUEUE_SIZE = 200  # 解析→嵌入管道缓冲队列大小（控制内存）
EMBED_WORKERS = 30  # 并行嵌入向量的线程数（10个key × 3并发）
MAX_RETRY_FILES = 3  # 每个文件解析失败时的最大重试次数
EVIDENCE_MATRIX_WORKERS = 4  # 证据矩阵并行检索线程数
MATERIAL_GEN_WORKERS = 2  # 材料生成并行 LLM 线程数（避免 API 限流）
SUMMARIZER_WORKERS = 2  # 多文件总结并行线程数

SUPPORTED_EXTENSIONS = {
    ".docx", ".xlsx", ".vsdx", ".pdf", ".zip", ".rar",
    ".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif",
    ".dxf",
    ".doc", ".xls", ".ppt", ".pptx",
}
