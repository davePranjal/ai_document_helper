import structlog
from pathlib import Path

import pdfplumber
from docx import Document as DocxDocument
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.config import settings

logger = structlog.get_logger(__name__)


def extract_text(file_path: str, mime_type: str) -> tuple[str, int | None]:
    """Extract text from a document file. Returns (text, page_count)."""
    path = Path(file_path)

    if mime_type == "application/pdf":
        return _extract_pdf(path)
    elif mime_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        return _extract_docx(path)
    elif mime_type == "text/plain":
        return _extract_txt(path)
    else:
        raise ValueError(f"Unsupported mime type: {mime_type}")


def _extract_pdf(path: Path) -> tuple[str, int]:
    pages = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                pages.append(text)
    return "\n\n".join(pages), len(pages) if pages else 0


def _extract_docx(path: Path) -> tuple[str, None]:
    doc = DocxDocument(str(path))
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    return "\n\n".join(paragraphs), None


def _extract_txt(path: Path) -> tuple[str, None]:
    return path.read_text(encoding="utf-8"), None


def chunk_text(
    text: str, page_count: int | None = None
) -> list[dict]:
    """Split text into chunks with metadata. Returns list of {content, chunk_index, page_number}."""
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
        length_function=len,
        separators=["\n\n", "\n", ". ", " ", ""],
    )

    documents = splitter.create_documents([text])
    chunks = []
    for i, doc in enumerate(documents):
        # Estimate page number if page_count is known
        page_number = None
        if page_count and page_count > 0:
            # Rough estimation based on position in text
            position_ratio = text.find(doc.page_content[:50]) / max(len(text), 1)
            page_number = max(1, min(page_count, int(position_ratio * page_count) + 1))

        chunks.append({
            "content": doc.page_content,
            "chunk_index": i,
            "page_number": page_number,
        })

    return chunks
