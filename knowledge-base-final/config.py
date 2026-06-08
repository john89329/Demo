import os
import sys
import threading


class Config:
    """Thread-safe configuration singleton.

    Replaces the module in sys.modules so that ``import config`` returns this
    instance directly.  All mutable attributes are protected by a reentrant lock;
    ``config.FOO = bar`` works at runtime and is serialized.
    """

    def __init__(self):
        object.__setattr__(self, "_lock", threading.RLock())

        # ── Knowledge base ──
        self._current_kb = "default"

        # ── API keys (default from env, overridable at runtime) ──
        self._deepseek_api_key = os.environ.get("DEEPSEEK_API_KEY", "")
        self._deepseek_base_url = "https://api.deepseek.com"
        self._embedding_api_key = os.environ.get("EMBEDDING_API_KEY", "")
        self._embedding_base_url = "https://api.siliconflow.cn/v1"
        self._embedding_model = "BAAI/bge-m3"
        self._chat_model = "deepseek-v4-flash"

        # ── Chunking / retrieval ──
        self._chunk_size = 1024
        self._chunk_overlap = 64
        self._top_k_results = 30

        # ── Paths (immutable) ──
        self._base_dir = os.path.dirname(os.path.abspath(__file__))
        self._index_dir = os.path.join(self._base_dir, "index")

        # ── FAISS tuning (immutable) ──
        self._faiss_nlist = 10000
        self._faiss_pq_m = 32
        self._faiss_pq_bits = 8
        self._faiss_train_threshold = 50000
        self._faiss_nprobe = 100

        # ── Thread / worker counts ──
        self._index_workers = 4
        self._parse_timeout = 300
        self._ocr_parse_timeout = 600
        self._pipeline_queue_size = 200
        self._embed_workers = 30
        self._max_retry_files = 3
        self._evidence_matrix_workers = 4
        self._material_gen_workers = 2
        self._summarizer_workers = 2

        # ── Supported file types ──
        self._supported_extensions = frozenset({
            ".docx", ".xlsx", ".vsdx", ".pdf", ".zip", ".rar",
            ".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif",
            ".dxf",
            ".doc", ".xls", ".ppt", ".pptx",
        })

    # ── Thread-safe property accessors ──────────────────────

    def _get(self, attr):
        with self._lock:
            return object.__getattribute__(self, attr)

    def _set(self, attr, value):
        with self._lock:
            object.__setattr__(self, attr, value)

    @property
    def CURRENT_KB(self):
        return self._get("_current_kb")

    @CURRENT_KB.setter
    def CURRENT_KB(self, value):
        self._set("_current_kb", value)

    @property
    def DEEPSEEK_API_KEY(self):
        return self._get("_deepseek_api_key")

    @DEEPSEEK_API_KEY.setter
    def DEEPSEEK_API_KEY(self, value):
        self._set("_deepseek_api_key", value)

    @property
    def DEEPSEEK_BASE_URL(self):
        return self._deepseek_base_url

    @property
    def EMBEDDING_API_KEY(self):
        return self._get("_embedding_api_key")

    @EMBEDDING_API_KEY.setter
    def EMBEDDING_API_KEY(self, value):
        self._set("_embedding_api_key", value)

    @property
    def EMBEDDING_BASE_URL(self):
        return self._embedding_base_url

    @property
    def EMBEDDING_MODEL(self):
        return self._embedding_model

    @property
    def CHAT_MODEL(self):
        return self._chat_model

    @property
    def CHUNK_SIZE(self):
        return self._get("_chunk_size")

    @CHUNK_SIZE.setter
    def CHUNK_SIZE(self, value):
        self._set("_chunk_size", int(value))

    @property
    def CHUNK_OVERLAP(self):
        return self._chunk_overlap

    @property
    def TOP_K_RESULTS(self):
        return self._get("_top_k_results")

    @TOP_K_RESULTS.setter
    def TOP_K_RESULTS(self, value):
        self._set("_top_k_results", int(value))

    @property
    def BASE_DIR(self):
        return self._base_dir

    @property
    def INDEX_DIR(self):
        return self._index_dir

    @property
    def FAISS_NLIST(self):
        return self._faiss_nlist

    @property
    def FAISS_PQ_M(self):
        return self._faiss_pq_m

    @property
    def FAISS_PQ_BITS(self):
        return self._faiss_pq_bits

    @property
    def FAISS_TRAIN_THRESHOLD(self):
        return self._faiss_train_threshold

    @property
    def FAISS_NPROBE(self):
        return self._faiss_nprobe

    @property
    def INDEX_WORKERS(self):
        return self._index_workers

    @property
    def PARSE_TIMEOUT(self):
        return self._parse_timeout

    @property
    def OCR_PARSE_TIMEOUT(self):
        return self._ocr_parse_timeout

    @property
    def PIPELINE_QUEUE_SIZE(self):
        return self._pipeline_queue_size

    @property
    def EMBED_WORKERS(self):
        return self._embed_workers

    @property
    def MAX_RETRY_FILES(self):
        return self._max_retry_files

    @property
    def EVIDENCE_MATRIX_WORKERS(self):
        return self._evidence_matrix_workers

    @property
    def MATERIAL_GEN_WORKERS(self):
        return self._material_gen_workers

    @property
    def SUMMARIZER_WORKERS(self):
        return self._summarizer_workers

    @property
    def SUPPORTED_EXTENSIONS(self):
        return self._supported_extensions

    # ── Bulk update for the settings page ───────────────────

    def bulk_update(self, **kwargs):
        """Thread-safe bulk update. Only mutates known keys."""
        with self._lock:
            for key, value in kwargs.items():
                priv = f"_{key.lower()}"
                if hasattr(self, priv):
                    object.__setattr__(self, priv, value)


# Replace this module with a Config instance so that ``import config`` and
# ``config.FOO = bar`` work transparently with the property descriptors.
_config_instance = Config()
# Preserve __name__ etc so the object quacks like a module enough for most code.
_config_instance.__name__ = __name__
_config_instance.__file__ = __file__
sys.modules[__name__] = _config_instance
