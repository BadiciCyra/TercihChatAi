from transformers import AutoTokenizer, AutoModel
import argparse
import os

MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

def main(cache_dir: str):
    os.makedirs(cache_dir, exist_ok=True)
    print(f"Downloading tokenizer to cache: {cache_dir}")
    AutoTokenizer.from_pretrained(MODEL, cache_dir=cache_dir)
    print("Tokenizer done.")
    print(f"Downloading model to cache: {cache_dir}")
    AutoModel.from_pretrained(MODEL, cache_dir=cache_dir)
    print("Model download complete.")

if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument("--cache", default="./hf_cache", help="cache dir to store model files")
    args = p.parse_args()
    main(args.cache)
