import requests
import httpx
from urllib.parse import urlencode
from typing import Any, Optional, Union
from .utils import load_column_data, parse_lisans_results, format_array_parameter
from .models import SearchParams, ProgramInfo
import urllib3
import json

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class YOKATLASLisansTercihSihirbazi:
    """YOKATLAS Lisans Tercih Sihirbazı - Search interface for bachelor's degree programs."""

    def __init__(self, params: dict[str, Any]) -> None:
        self.params = params
        self.columns: dict[str, str] = load_column_data()
        self._set_defaults()
        self._apply_params(params)

    def _set_defaults(self) -> None:
        """Set default values for search parameters."""
        defaults: dict[str, Union[str, int]] = {
            "draw": 4,
            "start": 0,
            "length": 50,
            "search[value]": "",
            "search[regex]": "false",
            "puan_turu": "say",
            "ust_bs": "",
            "alt_bs": "",
            "yeniler": "1",
            "kilavuz_kodu": "",
            "universite": "[]",
            "program": "[]",
            "sehir": "[]",
            "universite_turu": "[]",
            "ucret": "[]",
            "ogretim_turu": "[]",
            "doluluk": "[]",
            "order[0][column]": "34",
            "order[0][dir]": "desc",
            "order[1][column]": "41",
            "order[1][dir]": "asc",
            "order[2][column]": "42",
            "order[2][dir]": "asc",
        }

        for key, value in defaults.items():
            self.columns[key] = str(value)

    def _apply_params(self, params: dict[str, Any]) -> None:
        """Apply user parameters to search configuration with new array format."""
        array_params = {
            "universite": "universite",
            "program": "program",
            "sehir": "sehir",
            "universite_turu": "universite_turu",
            "ucret": "ucret",
            "ogretim_turu": "ogretim_turu",
            "doluluk": "doluluk",
        }

        # --- BURAYI GÜNCELLİYORUZ ---
        string_params = {
            "puan_turu": "puan_turu",
            "length": "length",
            "start": "start",
            "page": None,
            # EKLENEN SATIRLAR:
            "ust_bs": "ust_bs",  # Başarı Sırası (Üst Sınır - Daha iyi derece)
            "alt_bs": "alt_bs",  # Başarı Sırası (Alt Sınır - Daha kötü derece)
        }
        # ---------------------------

        for user_key, api_key in array_params.items():
            if user_key in params and params[user_key]:
                formatted_value = format_array_parameter(params[user_key])
                self.columns[api_key] = formatted_value

        for user_key, api_key in string_params.items():
            if user_key in params:
                if user_key == "page":
                    page = int(params[user_key])
                    length = int(params.get("length", 50))
                    self.columns["start"] = str((page - 1) * length)
                elif api_key:
                    self.columns[api_key] = str(params[user_key])

    def search(self) -> list[dict[str, Any]]:
        # ... (Geri kalanı orijinal dosyadaki gibi aynen kalıyor)
        payload = urlencode(self.columns, safe="[]%")
        headers = {
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.5 Safari/605.1.15",
        }
        url = "https://yokatlas.yok.gov.tr/server_side/server_processing-atlas2016-TS-t4.php"

        try:
            response = requests.post(url, data=payload, headers=headers, verify=False, timeout=30)
            response = httpx.post(url, data=payload, headers=headers, verify=False, timeout=30)
            if response.status_code == 200:
                try:
                    json_data = response.json()
                    return parse_lisans_results(json_data)
                except Exception:
                    import re
                    json_match = re.search(r"\{.*\}", response.text, re.DOTALL)
                    if json_match:
                        try:
                            data = json.loads(json_match.group(0))
                            return parse_lisans_results(data)
                        except Exception as e:
                            print(f"JSON Parse Hatası: {str(e)}")
                            return []
                    print("JSON bulunamadı")
                    return []
            else:
                print(f"HTTP {response.status_code}")
                return []
        except requests.exceptions.RequestException as e:
            print(f"İstek Hatası: {str(e)}")
            return []
