from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

HTTP_REQUESTS = Counter(
    "contexthub_http_requests_total", "HTTP requests", ["method", "route", "status"]
)
HTTP_LATENCY = Histogram(
    "contexthub_http_request_seconds", "HTTP request latency", ["method", "route"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60),
)
RAG_QUERIES = Counter("contexthub_rag_queries_total", "RAG queries", ["outcome"])
RAG_LATENCY = Histogram(
    "contexthub_rag_seconds", "End-to-end RAG latency", buckets=(0.5, 1, 2, 3, 5, 8, 13, 21, 34, 60)
)
RAG_RETAINED_CHUNKS = Histogram(
    "contexthub_rag_retained_chunks", "Chunks kept after the distance threshold", buckets=(0, 1, 2, 3, 4, 6, 8, 12)
)
UPLOADS = Counter("contexthub_uploads_total", "Accepted uploads", ["mime_type"])


def render() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST
