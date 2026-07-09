"""RAG 계층 — 인덱싱(ingest) · 검색(retriever) · 인용 검증(citations).

citations.py는 순수 결정론 계층으로 langchain/openai import를 포함하지 않는다.
임베딩·LLM은 반드시 LangChain 표준부품(AzureOpenAIEmbeddings / Chroma / AzureChatOpenAI)을
경유하며, 원시 API 직접호출은 금지한다.
"""
