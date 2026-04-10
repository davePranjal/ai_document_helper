SUMMARY_LENGTH_INSTRUCTIONS = {
    "brief": "Write a 2-3 sentence summary capturing the main point.",
    "standard": "Write a comprehensive summary of 1-2 paragraphs covering the key points.",
    "detailed": "Write a detailed summary of 3-4 paragraphs covering all major points, arguments, and conclusions.",
}

TONE_INSTRUCTIONS = {
    "professional": "Use a formal, professional tone suitable for business reports.",
    "academic": "Use an academic tone with precise terminology and objective language.",
    "casual": "Use a conversational, easy-to-understand tone for a general audience.",
    "technical": "Use a technical tone with domain-specific terminology, assuming expert readers.",
}

DOCUMENT_ANALYSIS_PROMPT = """Analyze the following document and provide a structured analysis.

<document>
{document_text}
</document>

{summary_instruction}
{tone_instruction}
{focus_instruction}

Respond with a JSON object containing exactly these fields:
{{
  "summary": "Your summary here",
  "key_topics": ["topic1", "topic2", ...],
  "entities": {{
    "people": ["name1", "name2"],
    "organizations": ["org1", "org2"],
    "locations": ["loc1", "loc2"],
    "dates": ["date1", "date2"],
    "other": ["entity1", "entity2"]
  }},
  "category": "One of: report, article, legal, technical, academic, financial, correspondence, manual, other",
  "tags": ["tag1", "tag2", "tag3"],
  "sentiment": "One of: positive, negative, neutral, mixed",
  "language": "The language the document is written in",
  "confidence_score": 0.95
}}

Rules:
- key_topics: 3-7 topics that capture the main themes
- entities: extract named entities into categories; use empty lists if none found
- tags: 3-10 descriptive tags useful for search and categorization
- confidence_score: 0.0-1.0 reflecting how confident you are in the analysis quality (lower if text was garbled or incomplete)
- Return ONLY the JSON object, no other text"""


def build_analysis_prompt(
    document_text: str,
    summary_length: str = "standard",
    tone: str | None = None,
    focus_area: str | None = None,
) -> str:
    summary_instruction = SUMMARY_LENGTH_INSTRUCTIONS.get(
        summary_length, SUMMARY_LENGTH_INSTRUCTIONS["standard"]
    )
    tone_instruction = TONE_INSTRUCTIONS.get(tone, "") if tone else ""
    focus_instruction = (
        f"Focus the summary and analysis particularly on: {focus_area}."
        if focus_area
        else ""
    )

    # Truncate very long documents to avoid token limits
    max_chars = 100_000
    if len(document_text) > max_chars:
        document_text = document_text[:max_chars] + "\n\n[Document truncated due to length]"

    return DOCUMENT_ANALYSIS_PROMPT.format(
        document_text=document_text,
        summary_instruction=summary_instruction,
        tone_instruction=tone_instruction,
        focus_instruction=focus_instruction,
    )
