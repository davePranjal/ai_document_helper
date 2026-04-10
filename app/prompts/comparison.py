COMPARISON_PROMPT = """You are comparing multiple documents. For each document, you are given its AI-generated summary and key topics.

{document_sections}

Provide a comparative analysis in JSON format:
{{
  "overview": "A 2-3 sentence high-level comparison of all documents.",
  "similarities": ["Shared theme or topic 1", "Shared theme or topic 2"],
  "differences": ["Key difference 1", "Key difference 2"],
  "unique_insights": [
    {{"document": "Document name", "insight": "Something unique to this document"}},
    {{"document": "Document name", "insight": "Something unique to this document"}}
  ],
  "relationships": "A paragraph describing how these documents relate to each other — do they complement, contradict, or build upon each other?"
}}

Rules:
- Focus on substantive content differences, not formatting
- Identify both thematic overlaps and contradictions
- Return ONLY the JSON object, no other text"""


def build_comparison_prompt(documents: list[dict]) -> str:
    """Build a comparison prompt from document summaries.

    Each dict should have: name, summary, key_topics, category, sentiment
    """
    sections = []
    for i, doc in enumerate(documents, 1):
        topics = ", ".join(doc.get("key_topics", []))
        sections.append(
            f"### Document {i}: {doc['name']}\n"
            f"**Category**: {doc.get('category', 'unknown')}\n"
            f"**Sentiment**: {doc.get('sentiment', 'unknown')}\n"
            f"**Key Topics**: {topics}\n"
            f"**Summary**: {doc['summary']}"
        )

    return COMPARISON_PROMPT.format(document_sections="\n\n".join(sections))
