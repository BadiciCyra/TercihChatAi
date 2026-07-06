# gate.py — Backward compatibility shim
# Gerçek uygulama kodu: app/gate.py
# uvicorn gate:app komutu bu dosyayı başlatır, app/gate.py'ye yönlendirir.
from app.gate import *  # noqa: F401, F403
from app.gate import app  # app objesini doğrudan export et (uvicorn için)
