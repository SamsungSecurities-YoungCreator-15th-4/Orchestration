"""IPS 추출·RAG 인용·Judge가 공유하는 AzureChatOpenAI 팩토리."""
import os


def get_llm(temperature: float = 0.0, *, seed: int | None = None):
    """AzureChatOpenAI 인스턴스 생성 (temperature=0 고정 기본값).

    호출 시점에 import하여, 키가 없는 오프라인 실행 경로에서는
    어떤 외부 의존도 초기화되지 않도록 한다.
    """
    required = ["AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_DEPLOYMENT"]
    missing = [k for k in required if not os.environ.get(k, "").strip()]
    if missing:
        raise RuntimeError(
            f"필수 Azure OpenAI 환경 변수가 누락되었거나 비어 있습니다: {', '.join(missing)}"
        )

    from langchain_openai import AzureChatOpenAI

    return AzureChatOpenAI(
        azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
        api_key=os.environ["AZURE_OPENAI_API_KEY"],
        api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-10-21"),
        azure_deployment=os.environ["AZURE_OPENAI_DEPLOYMENT"],
        temperature=temperature,
        seed=seed,
    )
