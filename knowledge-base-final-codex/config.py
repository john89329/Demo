import os
import sys
import json
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

        self._ocr_api_key = os.environ.get("OCR_API_KEY", os.environ.get("EMBEDDING_API_KEY", ""))
        self._ocr_base_url = "https://api.siliconflow.cn/v1"
        self._ocr_model = "deepseek-ai/DeepSeek-OCR"

        # ── Chunking / retrieval ──
        self._chunk_size = 1024
        self._chunk_overlap = 64
        self._top_k_results = 50

        # ── Paths (immutable) ──
        self._base_dir = os.path.dirname(os.path.abspath(__file__))
        self._index_dir = os.path.join(self._base_dir, "index")
        self._settings_path = os.path.join(self._base_dir, "settings.json")

        # ── Load persisted settings (overrides env defaults) ──
        self._load_settings()

        # ── FAISS tuning (immutable) ──
        self._faiss_nlist = 10000
        self._faiss_pq_m = 32
        self._faiss_pq_bits = 8
        self._faiss_train_threshold = 50000
        self._faiss_nprobe = 100

        # ── Thread / worker counts ──
        self._index_workers = 30
        self._parse_timeout = 60
        self._ocr_parse_timeout = 300
        self._pipeline_queue_size = 200
        self._embed_workers = 30
        self._max_retry_files = 3
        self._evidence_matrix_workers = 4
        self._material_gen_workers = 2
        self._summarizer_workers = 2

        # ── MapReduce / token budget ──
        self._max_input_tokens = 400_000           # safe input token ceiling per LLM call
        self._focus_analysis_batch_files = 30      # files per batch in focus_analysis Map phase
        self._focus_analysis_workers = 4           # parallel batch workers for focus_analysis
        self._summarizer_reduce_batch = 30         # file summaries per reduce batch
        self._summarizer_reduce_workers = 2        # parallel reduce workers for summarizer

        # ── Supported file types ──
        self._supported_extensions = frozenset({
            ".docx", ".xlsx", ".vsdx", ".pdf", ".zip", ".rar",
            ".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif",
            ".dxf",
            ".csv",
            ".doc", ".xls", ".ppt", ".pptx",
        })

    # ── Settings persistence ──────────────────────────────

    def _load_settings(self):
        """Load persisted API keys from disk, overriding env defaults."""
        try:
            if os.path.exists(self._settings_path):
                with open(self._settings_path, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                if saved.get("deepseek_api_key"):
                    self._deepseek_api_key = saved["deepseek_api_key"]
                if saved.get("embedding_api_key"):
                    self._embedding_api_key = saved["embedding_api_key"]
                if saved.get("ocr_api_key"):
                    self._ocr_api_key = saved["ocr_api_key"]
        except Exception:
            pass

    def save_settings(self):
        """Persist current API keys to disk."""
        try:
            with open(self._settings_path, "w", encoding="utf-8") as f:
                json.dump({
                    "deepseek_api_key": self._deepseek_api_key,
                    "embedding_api_key": self._embedding_api_key,
                    "ocr_api_key": self._ocr_api_key,
                }, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

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
    def OCR_API_KEY(self):
        return self._get("_ocr_api_key") or self._get("_embedding_api_key")

    @OCR_API_KEY.setter
    def OCR_API_KEY(self, value):
        self._set("_ocr_api_key", value)

    @property
    def OCR_BASE_URL(self):
        return self._ocr_base_url

    @property
    def OCR_MODEL(self):
        return self._get("_ocr_model")

    @OCR_MODEL.setter
    def OCR_MODEL(self, value):
        self._set("_ocr_model", value)

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
    def MAX_INPUT_TOKENS(self):
        return self._max_input_tokens

    @property
    def FOCUS_ANALYSIS_BATCH_FILES(self):
        return self._focus_analysis_batch_files

    @property
    def FOCUS_ANALYSIS_WORKERS(self):
        return self._focus_analysis_workers

    @property
    def SUMMARIZER_REDUCE_BATCH(self):
        return self._summarizer_reduce_batch

    @property
    def SUMMARIZER_REDUCE_WORKERS(self):
        return self._summarizer_reduce_workers

    @property
    def SUPPORTED_EXTENSIONS(self):
        return self._supported_extensions


# Replace this module with a Config instance so that ``import config`` and
# ``config.FOO = bar`` work transparently with the property descriptors.
_config_instance = Config()
# Preserve __name__ etc so the object quacks like a module enough for most code.
_config_instance.__name__ = __name__
_config_instance.__file__ = __file__
sys.modules[__name__] = _config_instance
