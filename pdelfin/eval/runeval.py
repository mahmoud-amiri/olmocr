# This script will build a set of scores for the accuracy of a given pdf conversion tactic against a gold dataset
#
# You might need to pip install git+https://github.com/allenai/refine.git@soldni/eval-m
# in order to use some of the existing aligner scoring that was developed as part
# of the refiner pipeline
import boto3
import os
import json
import hashlib
import random
import zstandard
import sys
import argparse

from dataclasses import dataclass
from typing import Optional
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor, as_completed
from pathlib import Path
from smart_open import smart_open, register_compressor
from dolma_refine.evaluate.metrics import DocumentEditSimilarity
from dolma_refine.evaluate.segmenters import SpacySegmenter
from dolma_refine.evaluate.aligners import HirschbergAligner

from .evalhtml import create_review_html


CACHE_DIR = os.path.join(Path.home(), ".cache", "pdf_gold_data_cache")

s3_client = boto3.client('s3')


def _handle_zst(file_obj, mode):
    return zstandard.open(file_obj, mode)

register_compressor(".zstd", _handle_zst)
register_compressor(".zst", _handle_zst)

# Helper function to download files from S3
def download_from_s3(s3_path: str, local_path: str):
    bucket_name, key = s3_path.replace("s3://", "").split("/", 1)
    s3_client.download_file(bucket_name, key, local_path)

def is_debugging():
    return sys.gettrace() is not None

# Create a hash to store file contents and check for changes
def compute_file_hash(file_path: str) -> str:
    hash_md5 = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_md5.update(chunk)
    return hash_md5.hexdigest()


# A single method which can take in any format json entry (openai regular, openai structured, birr)
# and normalize it to a common structure for use later in the 
@dataclass(frozen=True)
class NormalizedEntry:
    s3_path: str
    pagenum: int
    text: Optional[str]
    finish_reason: Optional[str]
    error: Optional[str] = None

    @staticmethod
    def from_goldkey(goldkey: str, **kwargs):
        s3_path = goldkey[:goldkey.rindex("-")]
        page_num = int(goldkey[goldkey.rindex("-") + 1:])
        return NormalizedEntry(s3_path, page_num, **kwargs)

    @property
    def goldkey(self):
        return f"{self.s3_path}-{self.pagenum}"

def normalize_json_entry(data: dict) -> NormalizedEntry:
    if "outputs" in data:
        # Birr case
        if data["outputs"] is None:
            text = None
            finish_reason = None
        else:
            text = data["outputs"][0]["text"]
            finish_reason = data["outputs"][0]["finish_reason"]

        # Try to parse the structured output if possible
        try:
            if text is not None:
                parsed_content = json.loads(text)
                text = parsed_content["natural_text"]
        except json.JSONDecodeError:
            pass
        
        return NormalizedEntry.from_goldkey(
            goldkey=data["custom_id"],
            text=text,
            finish_reason=finish_reason,
            error=data.get("completion_error", None)
        )
    else:
        # OpenAI case
        try:
            # Attempt to parse the JSON content from OpenAI's response
            parsed_content = json.loads(data["response"]["body"]["choices"][0]["message"]["content"])
            return NormalizedEntry.from_goldkey(
                goldkey=data["custom_id"],
                text=parsed_content["natural_text"],
                finish_reason=data["response"]["body"]["choices"][0]["finish_reason"]
            )
        except json.JSONDecodeError:
            # Fallback if content is not valid JSON
            return NormalizedEntry.from_goldkey(
                goldkey=data["custom_id"],
                text=data["response"]["body"]["choices"][0]["message"]["content"],
                finish_reason=data["response"]["body"]["choices"][0]["finish_reason"]
            )

# Load every .json file from GOLD_DATA_S3_PATH (and saves it to some temp folder for quick loading next time)
# returns map from  "custom_id" ex. "s3://ai2-s2-pdfs/39ce/3db4516cd6e7d7f8e580a494c7a665a6a16a.pdf-4" (where the -4 means page 4)
# to the gold standard text
def load_gold_data(gold_data_path: str) -> dict:
    if not os.path.exists(CACHE_DIR):
        os.makedirs(CACHE_DIR)

    gold_data = {}
    
    gold_jsonl_files = list_jsonl_files(gold_data_path)

    for path in gold_jsonl_files:          
        # Load the JSON file
        with smart_open(path, 'r') as f:
            for line in f:
                data = json.loads(line)
                data = normalize_json_entry(data)

                gold_data[data.goldkey] = data.text

    print(f"Loaded {len(gold_data):,} gold data entries for comparison")

    return gold_data

# Helper function to list all .jsonl files from a directory or an S3 bucket
def list_jsonl_files(path: str) -> list:
    valid_endings = [".json", ".jsonl", ".json.zstd", ".jsonl.zstd"]
    jsonl_files = []
    
    if path.startswith("s3://"):
        bucket_name, prefix = path.replace("s3://", "").split("/", 1)
        paginator = s3_client.get_paginator('list_objects_v2')
        pages = paginator.paginate(Bucket=bucket_name, Prefix=prefix)
        
        for page in pages:
            for obj in page.get('Contents', []):
                if any(obj['Key'].endswith(ending) for ending in valid_endings):
                    jsonl_files.append(f"s3://{bucket_name}/{obj['Key']}")
    
    else:
        # If it's a local directory, list all .jsonl files
        for root, _, files in os.walk(path):
            for file in files:
                if any(file.endswith(ending) for ending in valid_endings):
                    jsonl_files.append(os.path.join(root, file))

    return jsonl_files

# Takes in a path to a local directory or s3://[bucket]/[prefix path] where your jsonl files are stored
# This is most likely the output location of the refiner
# Expecting each jsonl line to include {s3_path: [path to original pdf], page: [pagenum], text: [proper page text]}
# Returns the average Levenshtein distance match between the data
def process_jsonl_file(jsonl_file, gold_data, comparer):
    page_data = {}
    total_alignment_score = 0
    char_weighted_alignment_score = 0
    total_pages = 0
    total_chars = 0

    with smart_open(jsonl_file, 'r') as f:
        for line in f:
            data = json.loads(line)

            data = normalize_json_entry(data)

            if data.goldkey not in gold_data:
                continue

            gold_text = gold_data[data.goldkey]
            eval_text = data.text

            gold_text = gold_text or ""
            eval_text = eval_text or ""

            # If the eval text or gold text is empty, we skip this page and don't use it for comparison
            # It means that something was an OCR page, and the text-based pipeline just won't be able to handle that
            # if len(eval_text.strip()) < 10 or len(gold_text.strip()) < 10:
            #     continue

            alignment = comparer.compute(gold_text, eval_text)

            page_data[data.goldkey] = {
                "s3_path": data.s3_path,
                "page": data.pagenum,
                "gold_text": gold_text,
                "eval_text": eval_text,
                "alignment": alignment
            }

            total_alignment_score += alignment
            char_weighted_alignment_score += alignment * len(gold_text)
            total_chars += len(gold_text)
            total_pages += 1

    return total_alignment_score, char_weighted_alignment_score, total_chars, total_pages, page_data

def do_eval(gold_data_path: str, eval_data_path: str, review_page_name: str, review_page_size: int) -> tuple[float, list[dict]]:
    gold_data = load_gold_data(gold_data_path)
    
    total_alignment_score = 0
    total_weight = 0
    total_pages_compared = set()
    
    page_eval_data = []

    segmenter = SpacySegmenter("spacy")
    aligner = HirschbergAligner(match_score=1,
                                mismatch_score=-1,
                                indel_score=-1)
    comparer = DocumentEditSimilarity(segmenter=segmenter, aligner=aligner)

    # List all .jsonl files in the directory or S3 bucket
    jsonl_files = list_jsonl_files(eval_data_path)

    if not jsonl_files:
        raise ValueError("No .jsonl files found in the specified path.")
    
    print(f"Found {len(jsonl_files):,} files to evaluate")

    with ProcessPoolExecutor() if not is_debugging() else ThreadPoolExecutor() as executor:
        # Prepare the future tasks
        futures = [executor.submit(process_jsonl_file, jsonl_file, gold_data, comparer) for jsonl_file in jsonl_files]

        # Process each future as it completes
        for future in tqdm(as_completed(futures), total=len(jsonl_files)):
            alignment_score, char_weighted_score, chars, pages, page_data = future.result()  # Get the result of the completed task
            
            # Aggregate statistics
            total_alignment_score += char_weighted_score
            total_weight += chars
            total_pages_compared |= page_data.keys()

            # Generate the eval data
            for pd_key, pd in page_data.items():
                # if pd["alignment"] > 0.97:
                #     continue

                # if len(pd["gold_text"]) < 200 and len(pd["eval_text"]) < 200:
                #     continue

                page_eval_data.append(pd)

    print(f"Compared {len(total_pages_compared):,} pages")
    print(f"Total corpus alignment: {total_alignment_score:.2f}")
    print(f"Mean alignment: {total_alignment_score / total_weight:.3f}")
    print("")
    print("...creating review page")

    # TODO Temporary filter to see other stuff
    #page_eval_data = [x for x in page_eval_data if "NO ENGLISH TEXT" not in x["gold_text"]]

    # Select the top 20 lowest alignments
    page_eval_data.sort(key=lambda x: x["alignment"])
    create_review_html(page_eval_data[:review_page_size], filename=review_page_name + "_worst.html")

    # Select random entries to return in the page_eval_data
    page_eval_data = random.sample(page_eval_data, review_page_size)
    create_review_html(page_eval_data, filename=review_page_name + "_sample.html")


    return total_alignment_score / total_weight, page_eval_data


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Transform JSONL files by extracting and renaming specific fields."
    )
    parser.add_argument(
        '--name',
        default="review_page",
        help="What name to give to this evaluation/comparison"
    )
    parser.add_argument(
        '--review_size',
        default=20,
        type=int,
        help="Number of entries to show on the generated review page",
    )
    parser.add_argument(
        'gold_data_path',
        type=str,
        help='Path to the gold data directory containing JSONL files. Can be a local path or S3 URL. Can be openai "done" data, or birr "done" data'
    )
    parser.add_argument(
        'eval_data_path',
        type=str,
        help='Path to the eval data directory containing JSONL files. Can be a local path or S3 URL. Can be openai "done" data, or birr "done" data'
    )

    args = parser.parse_args()

    result = do_eval(gold_data_path=args.gold_data_path, eval_data_path=args.eval_data_path, review_page_name=args.name, review_page_size=args.review_size)