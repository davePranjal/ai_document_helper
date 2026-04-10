import structlog
from sentence_transformers import SentenceTransformer

from app.config import settings

logger = structlog.get_logger(__name__)

_model: SentenceTransformer | None = None


def _get_model() -> SentenceTransformer:
    global _model
    if _model is None:
        logger.info("Loading embedding model", model=settings.embedding_model)
        # Force CPU: macOS MPS + Celery prefork (fork after Metal init) crashes with SIGABRT.
        _model = SentenceTransformer(settings.embedding_model, device="cpu")
    return _model


def count_tokens(text: str) -> int:
    """Approximate token count using the model's tokenizer."""
    model = _get_model()
    return len(model.tokenizer.encode(text))


def generate_embeddings(texts: list[str]) -> list[list[float]]:
    """Generate embeddings for a batch of texts using a local model."""
    model = _get_model()

    batch_size = 64
    all_embeddings: list[list[float]] = []

    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        embeddings = model.encode(batch, normalize_embeddings=True, show_progress_bar=False)
        all_embeddings.extend(embedding.tolist() for embedding in embeddings)
        logger.info(
            "Embedded batch",
            start=i,
            end=min(i + batch_size, len(texts)),
            total=len(texts),
        )

    return all_embeddings


def generate_single_embedding(text: str) -> list[float]:
    """Generate embedding for a single text."""
    model = _get_model()
    embedding = model.encode(text, normalize_embeddings=True, show_progress_bar=False)
    return embedding.tolist()
