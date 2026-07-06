import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

url = "https://yokatlas.yok.gov.tr/server_side/server_processing-atlas2016-TS-t4.php"

headers = {
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
    "Origin": "https://yokatlas.yok.gov.tr",
    "Referer": "https://yokatlas.yok.gov.tr/lisans-tercih-sihirbazi.php"
}

# Minimal payload to trigger a response
payload = {
    "draw": "1",
    "start": "0",
    "length": "10",
    "puan_turu": "say",
    "yeniler": "1"
}

print(f"Testing connection to: {url}")

try:
    response = requests.post(url, data=payload, headers=headers, verify=False, timeout=10)
    print(f"Status Code: {response.status_code}")
    
    if response.status_code == 200:
        print("Success! Response snippet:")
        print(response.text[:200])
        try:
            json_data = response.json()
            print("JSON decoding successful.")
            print(f"Data count: {len(json_data.get('data', []))}")
        except Exception as e:
            print(f"JSON decoding failed: {e}")
    else:
        print("Failed to get 200 OK.")
        print(response.text[:500])

except Exception as e:
    print(f"Connection failed: {e}")
