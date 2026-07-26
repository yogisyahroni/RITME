import requests

url = "http://localhost:8585/generate"
payload = {
    "url": "https://www.youtube.com/shorts/88c2H2rG82w"  # Bisa lo ganti sama URL YouTube Shorts lain
}

print(f"Mengirim request ke {url}...")
print("Catatan: Kalau ini pertama kali, server bakal download model AI XTTS (~2GB).")
print("Tunggu aja sampai selesai ya...\n")

try:
    response = requests.post(url, json=payload, timeout=3600)  # Timeout 1 jam buat jaga-jaga download model
    print(f"Status Code: {response.status_code}")
    
    if response.status_code == 200:
        print("BERHASIL! Ini output dari server:")
        print(response.json())
    else:
        print("Ada error dari server:")
        print(response.text)
except Exception as e:
    print(f"Gagal konek ke server: {e}")
