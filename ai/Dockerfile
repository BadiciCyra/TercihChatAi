# Dockerfile (DÜZELTİLMİŞ)
FROM python:3.10-slim

WORKDIR /app

# Sistem bağımlılıkları
# software-properties-common KALDIRILDI, hatayı o veriyordu.
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Playwright ve tarayıcı bağımlılıkları
RUN pip install playwright && playwright install --with-deps chromium

# Requirements dosyasını kopyala
COPY requirements.txt .

# Upgrade pip and install PyTorch (CPU) from official wheels before other requirements
RUN pip install --upgrade pip
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch

# Kütüphaneleri yükle
RUN pip install --no-cache-dir -r requirements.txt

# Kaynak kodları kopyala
COPY . .

# Başlatma komutu
CMD ["uvicorn", "gate:app", "--host", "0.0.0.0", "--port", "8003"]