from langchain_text_splitters import RecursiveCharacterTextSplitter


def _is_meaningful(text: str, min_alpha_ratio: float = 0.4, min_length: int = 20) -> bool:
    """Return False if the chunk is too short or mostly non-alphabetic (e.g. 2D-DOC noise)."""
    text = text.strip()
    if len(text) < min_length:
        return False
    alpha = sum(c.isalpha() for c in text)
    return (alpha / len(text)) >= min_alpha_ratio


def split_text(data):
    text_splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=50)
    all_splits = text_splitter.split_documents(data)
    return [doc for doc in all_splits if _is_meaningful(doc.page_content)]