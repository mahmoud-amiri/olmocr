#!/usr/bin/env python3
import argparse
import boto3
import json
import random
import re
import requests
import time
from tqdm import tqdm
from transformers import AutoTokenizer

# Allowed characters: alphanumeric, space, and basic punctuation ".,!?()"
ALLOWED_RE = re.compile(r'^[A-Za-z0-9\.,!?() ]+$')

def get_random_line_from_s3(bucket, key):
    """
    Reads an S3 object line-by-line and returns a random line using reservoir sampling.
    """
    s3 = boto3.client('s3')
    response = s3.get_object(Bucket=bucket, Key=key)
    random_line = None
    count = 0
    for line in response['Body'].iter_lines():
        if not line:
            continue
        line_str = line.decode('utf-8')
        count += 1
        if random.randint(1, count) == 1:
            random_line = line_str
    return random_line

def query_infinigram(ngram, index="v4_rpj_llama_s4", retries=3):
    """
    Sends a count query to the infini-gram API for the given n-gram.
    Retries a few times in case of network issues.
    """
    url = "https://api.infini-gram.io/"
    payload = {
        "index": index,
        "query_type": "count",
        "query": ngram,
    }
    for i in range(retries):
        try:
            response = requests.post(url, json=payload, timeout=10)
            if response.status_code == 200:
                result = response.json()
                if "count" in result:
                    return result["count"]
        except Exception as e:
            time.sleep(1)
    return 0

def process_document(doc, tokenizer, ngram_size, num_samples, index="v4_rpj_llama_s4"):
    """
    Tokenizes the document using the Llama2 tokenizer and samples random n-grams.
    Each n-gram is chosen such that:
      1. It starts on a word-split boundary (using the offset mapping and a check on the preceding character).
      2. Its decoded string contains only alphanumeric characters, spaces, and the punctuation marks ".,!?()".
    
    Each valid n-gram is then queried using the infini-gram API.
    The function returns the document id, the number of matching n-grams (i.e. API count > 0),
    the total number of valid n-grams sampled, and a list of tuples (flag, ngram_string).
    """
    text = doc.get("text", "")
    doc_id = doc.get("id", "Unknown")
    # Get tokenized representation with offset mapping to determine word boundaries.
    tokenized = tokenizer(text, add_special_tokens=False, return_offsets_mapping=True)
    token_ids = tokenized["input_ids"]
    offsets = tokenized["offset_mapping"]

    if len(token_ids) < ngram_size:
        return doc_id, 0, 0, []

    # Determine valid starting indices based on word-split boundaries.
    valid_positions = []
    for i in range(len(token_ids) - ngram_size + 1):
        start_offset = offsets[i][0]
        if start_offset == 0 or (start_offset > 0 and text[start_offset - 1] == " "):
            valid_positions.append(i)
    
    if not valid_positions:
        # Fallback: if no valid positions are found, use all possible positions.
        valid_positions = list(range(len(token_ids) - ngram_size + 1))
    
    valid_ngram_details = []
    attempts = 0
    max_attempts = num_samples * 10  # Limit to prevent infinite loops.
    while len(valid_ngram_details) < num_samples and attempts < max_attempts:
        idx = random.choice(valid_positions)
        ngram_token_ids = token_ids[idx: idx+ngram_size]
        ngram_str = tokenizer.decode(ngram_token_ids, clean_up_tokenization_spaces=True)
        # Only accept n-grams that contain only allowed characters.
        if ALLOWED_RE.fullmatch(ngram_str):
            count = query_infinigram(ngram_str, index=index)
            flag = "YES" if count > 0 else "NO"
            valid_ngram_details.append((flag, ngram_str))
        attempts += 1

    match_count = sum(1 for flag, _ in valid_ngram_details if flag == "YES")
    sample_count = len(valid_ngram_details)
    return doc_id, match_count, sample_count, valid_ngram_details

def main():
    parser = argparse.ArgumentParser(
        description="Infini-gram n-gram matching script with Llama2 tokenization."
    )
    parser.add_argument("N", type=int, help="Number of random .jsonl files to process")
    parser.add_argument("s3_path", type=str, help="S3 path to a prefix containing .jsonl files (e.g., s3://my-bucket/my-prefix/)")
    parser.add_argument("--index", type=str, default="v4_rpj_llama_s4", help="Infini-gram index to use (default: v4_rpj_llama_s4)")
    parser.add_argument("--ngram_size", type=int, default=10, help="Size of the n-gram to sample (default: 10)")
    parser.add_argument("--num_ngrams", type=int, default=100, help="Number of random n-grams to sample from each document (default: 100)")
    args = parser.parse_args()

    if not args.s3_path.startswith("s3://"):
        print("Error: s3_path must start with 's3://'")
        return
    path_without_scheme = args.s3_path[5:]
    parts = path_without_scheme.split("/", 1)
    bucket = parts[0]
    prefix = parts[1] if len(parts) > 1 else ""

    print("Listing .jsonl files from S3...")
    s3 = boto3.client("s3")
    response = s3.list_objects_v2(Bucket=bucket, Prefix=prefix)
    files = [obj["Key"] for obj in response.get("Contents", []) if obj["Key"].endswith(".jsonl")]
    if not files:
        print("No .jsonl files found in the given prefix.")
        return

    if args.N > len(files):
        print(f"Requested {args.N} files, but only found {len(files)}. Processing all available files.")
        args.N = len(files)
    random_files = random.sample(files, args.N)

    print("Loading Llama2 tokenizer...")
    tokenizer = AutoTokenizer.from_pretrained("meta-llama/Llama-2-7b-chat-hf")

    total_matches = 0
    total_ngrams_sampled = 0

    for key in tqdm(random_files, desc="Processing files"):
        line = get_random_line_from_s3(bucket, key)
        if not line:
            print(f"Skipping {key}: No valid lines found.")
            continue
        try:
            doc = json.loads(line)
        except Exception as e:
            print(f"Error parsing JSON in {key}: {e}")
            continue
        doc_id, match_count, sample_count, details = process_document(
            doc, tokenizer, args.ngram_size, args.num_ngrams, index=args.index
        )
        
        # Print per-document n-gram summary
        print(f"\nDocument ID: {doc_id}")
        for flag, ngram in details:
            # Print the flag in a fixed-width field (4 characters) followed by the n-gram representation.
            print(f"{flag:4} {repr(ngram)}")
        percentage = (match_count / sample_count * 100) if sample_count else 0
        print(f"Matched n-grams: {match_count}/{sample_count} ({percentage:.2f}%)")
        
        total_matches += match_count
        total_ngrams_sampled += sample_count

    overall_percentage = (total_matches / total_ngrams_sampled * 100) if total_ngrams_sampled else 0
    print(f"\nTotal matched n-grams: {total_matches}/{total_ngrams_sampled} ({overall_percentage:.2f}%)")

if __name__ == "__main__":
    main()
