import os
from dotenv import load_dotenv

# .env dosyasını yükle
load_dotenv()

class Config:
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
    REDIS_URL = os.getenv("REDIS_URL")
    ROUTER_URL = os.getenv("ROUTER_URL")
    RETRIEVER_URL = os.getenv("RETRIEVER_URL")
    RERANKER_URL = os.getenv("RERANKER_URL")
    INTENT_MODEL_PATH = os.getenv("INTENT_MODEL_PATH")

    if not GEMINI_API_KEY:
        print("UYARI: GEMINI_API_KEY bulunamadı! (Docker env kontrol ediliyor...)")
        GEMINI_API_KEY = ""

settings = Config()
