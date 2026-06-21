#!/usr/bin/env python3
"""
Digitized Diary Pipeline Automation Script

Usage:

    # Run Full Pipeline: Process and OCR handwritten journal images, transcribe and clean up voice memos, and assemble markdown entries into volumes.
    python3 digitized_diary_pipeline.py --all

    To run individual pipelines:
    # Run Pipeline A only: Process and OCR handwritten journal images
    python3 digitized_diary_pipeline.py --journal

    # Run Pipeline B only: Transcribe and clean up voice memos
    python3 digitized_diary_pipeline.py --voice

    # Run Pipeline C only: Assemble individual markdown entries into chronological volumes
    python3 digitized_diary_pipeline.py --assemble

Key Features:
1. Unified YYYY-MM-DD hyphenated date structure across all filenames and headers.
2. Pipeline A: Ingests standalone PDFs or loose journal images, extracts pages losslessly at 300 DPI via pdf2image, runs precise OCR, and moves files to processed archive.
3. Pipeline B: Ingests standalone JSON transcript batches, polishes prose, and archives the JSON file.
4. Pipeline C: Rebuilds a single master volume book completely from scratch on every run.
"""

import os
import sys
import re
import json
import logging
import argparse
from pathlib import Path
from collections import defaultdict
from PIL import Image
from datetime import datetime

# =====================================================================
# SYSTEM & MODEL CONFIGURATION AREA
# =====================================================================
# Models
PRIMARY_VISION_MODEL = "gemini-2.5-flash"
PRIMARY_TEXT_MODEL = "gemini-2.5-flash"

# Centralized File Names
PRICING_CONFIG_FILE = "model_pricing.json"
MASTER_COMPILATION_FILE = "master_diary_compilation.md"
EXECUTION_LOG_FILE = "digitized_diary_pipeline.log"

# Core Directory Layout Paths
INBOX_DIR = Path("inbox")
PDF_INBOX = INBOX_DIR / "pdf_scans"
IMAGE_INBOX = INBOX_DIR / "journal_images"
VOICE_INBOX = INBOX_DIR / "voice_memos"

ENTRIES_DIR = Path("entries")
VOLUMES_DIR = Path("volumes")

PROCESSED_DIR = Path("processed")
PROCESSED_PDF = PROCESSED_DIR / "pdf_scans"
PROCESSED_IMAGE = PROCESSED_DIR / "journal_images"
PROCESSED_VOICE = PROCESSED_DIR / "voice_memos"


# =====================================================================
# LOGGING ENGINE INITIALIZATION & NOISE REDUCTION
# =====================================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(EXECUTION_LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("DigitizedDiaryPipeline")

# Suppress internal SDK info alerts ("AFC is enabled with max remote calls...")
logging.getLogger("google").setLevel(logging.WARNING)


# =====================================================================
# GLOBAL STATE TRACKER FOR TOTAL RUN COST
# =====================================================================
TOTAL_RUN_METRICS = {
    "input_tokens": 0,
    "output_tokens": 0,
    "thinking_tokens": 0, 
    "cached_tokens": 0,
    "total_cost": 0.0
}


def get_genai_client():
    """Initializes and returns the modern GenAI Client."""
    from google import genai
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        logger.error("Environment variable GEMINI_API_KEY is missing from execution space.")
        raise ValueError("GEMINI_API_KEY environment variable not set.")
    return genai.Client(api_key=api_key)


def init_directory_tree():
    """Validates and enforces persistent layout structure across the workspace."""
    for directory in [PDF_INBOX, IMAGE_INBOX, VOICE_INBOX, ENTRIES_DIR, VOLUMES_DIR, PROCESSED_PDF, PROCESSED_IMAGE, PROCESSED_VOICE]:
        directory.mkdir(parents=True, exist_ok=True)


def sanitize_filename(name_str: str) -> str:
    """Converts a user-defined title string into a safe, cross-platform filename token."""
    sanitized = re.sub(r'[\\/*?:"<>|]', "", name_str)
    sanitized = re.sub(r'[\s_]+', "-", sanitized)
    return sanitized.strip("-")


def migrate_file(source_file: Path, target_dir: Path):
    """Safely relocates an asset to its processed archive target directory."""
    target_path = target_dir / source_file.name
    if target_path.exists():
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        target_path = target_dir / f"{source_file.stem}_{timestamp}{source_file.suffix}"
    try:
        os.rename(source_file, target_path)
        logger.info(f"Relocated processed asset: {source_file.name} -> {target_path.name}")
    except Exception as e:
        logger.error(f"[{source_file.name}] Failed to migrate asset: {str(e)}")


def write_markdown_entry(date_str: str, suffix_type: str, content: str):
    """Serializes transcript data into individual chronological Markdown files, overwriting existing files."""
    clean_date = date_str.strip()
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", clean_date):
        clean_date = datetime.now().strftime("%Y-%m-%d")
        logger.warning(f"Invalid date format '{date_str}'. Defaulting to current run timestamp.")

    target_filename = f"{clean_date}_{suffix_type}.md"
    target_path = ENTRIES_DIR / target_filename

    try:
        target_path.write_text(content.strip() + "\n", encoding="utf-8")
        logger.info(f"Saved entry record: {target_path.name}")
    except Exception as e:
        logger.error(f"Failed writing markdown file output to disk: {str(e)}")


def log_api_usage_and_cost(model_name: str, usage_metadata) -> dict:
    """Parses GenAI response metadata, calculates costs using a decoupled JSON pricing index, and updates global ledger totals."""
    if not usage_metadata:
        return {"input": 0, "output": 0, "cost": 0.0}

    input_tokens = getattr(usage_metadata, 'prompt_token_count', 0) or 0
    output_tokens = getattr(usage_metadata, 'candidates_token_count', 0) or 0
    thinking_tokens = getattr(usage_metadata, 'thinking_token_count', 0) or 0
    cached_tokens = getattr(usage_metadata, 'cached_content_token_count', 0) or 0
    # Combined output pool for total_token_count fallback calculation
    combined_outputs = output_tokens + thinking_tokens
    total_tokens = getattr(usage_metadata, 'total_token_count', 0) or (input_tokens + combined_outputs)

    input_rate = 0.0
    output_rate = 0.0
    cached_rate = 0.0

    try:
        pricing_file = Path(PRICING_CONFIG_FILE)
        if pricing_file.exists():
            pricing_matrix = json.loads(pricing_file.read_text(encoding="utf-8"))
            if model_name in pricing_matrix:
                rates = pricing_matrix[model_name]
                input_rate = rates.get("input_cost_per_million", 0.0)
                output_rate = rates.get("output_cost_per_million", 0.0)
                cached_rate = rates.get("cached_cost_per_million", 0.0)
    except Exception as e:
        logger.warning(f"Failed loading external pricing file, defaulting to zero-cost calculation: {str(e)}")

    non_cached_input = max(0, input_tokens - cached_tokens)
    
    # Calculate costs strictly separated by their generation type
    input_cost = non_cached_input * (input_rate / 1_000_000)
    cached_cost = cached_tokens * (cached_rate / 1_000_000)
    output_text_cost = output_tokens * (output_rate / 1_000_000)
    thinking_cost = thinking_tokens * (output_rate / 1_000_000) # Charged at output rate
    
    estimated_cost = input_cost + cached_cost + output_text_cost + thinking_cost

    logger.info(
        f"API USAGE METRICS [{model_name}] -> Total Tokens: {total_tokens} "
        f"(Prompt: {input_tokens}, Candidates: {output_tokens}, Thinking: {thinking_tokens}, Cached: {cached_tokens}) | "
        f"Cost Breakdown -> Text Output: ${output_text_cost:.6f}, Thinking: ${thinking_cost:.6f} | "
        f"Total Step Cost: ${estimated_cost:.6f}"
    )

    # Accumulate metrics into global session memory counters
    TOTAL_RUN_METRICS["input_tokens"] += input_tokens
    TOTAL_RUN_METRICS["output_tokens"] += output_tokens
    TOTAL_RUN_METRICS["thinking_tokens"] += thinking_tokens
    TOTAL_RUN_METRICS["cached_tokens"] += cached_tokens
    TOTAL_RUN_METRICS["total_cost"] += estimated_cost

    return {"input": input_tokens, "output": output_tokens, "cost": estimated_cost}


def log_global_run_summary():
    """Prints a distinct cumulative financial statement at the absolute conclusion of execution loops."""
    logger.info("=" * 60)
    logger.info("FINAL PIPELINE RUN TRANSACTION SUMMARY")
    logger.info("=" * 60)
    logger.info(f" Total Cumulative Input Tokens Generated:     {TOTAL_RUN_METRICS['input_tokens']}")
    logger.info(f" Total Cumulative Visible Output Tokens:      {TOTAL_RUN_METRICS['output_tokens']}")
    logger.info(f" Total Cumulative Internal Thinking Tokens:   {TOTAL_RUN_METRICS['thinking_tokens']}")
    logger.info(f" Total Smart Context Cache Tokens Read:       {TOTAL_RUN_METRICS['cached_tokens']}")
    logger.info(f" TOTAL AGGREGATED API RUN COST BILLABLE:       ${TOTAL_RUN_METRICS['total_cost']:.6f}")
    logger.info("=" * 60)


# =====================================================================
# PIPELINE A: JOURNAL TRANSCRIPTION ENGINE (OCR)
# =====================================================================
def process_journal_pipeline():
    logger.info(f"Initializing Pipeline A: Journal Processing Engine ({PRIMARY_VISION_MODEL})")
    
    # Pre-flight Check: Convert any incoming TurboScan PDFs into unboxed loss-free JPEGs
    pdf_files = list(PDF_INBOX.glob("*.pdf"))
    if pdf_files:
        try:
            from pdf2image import convert_from_path
            logger.info(f"Detected {len(pdf_files)} PDF source document bundle(s) inside inbox. Unboxing pages...")
            for pdf_path in pdf_files:
                logger.info(f"Extracting frames from: {pdf_path.name} at 300 DPI resolution...")
                images = convert_from_path(pdf_path, dpi=300)
                for index, image in enumerate(images):
                    # Export page frame matching expected structural file indexing patterns
                    extracted_image_name = f"{pdf_path.stem}-p{index + 1}.jpg"
                    image.save(IMAGE_INBOX / extracted_image_name, "JPEG", quality=95)
                
                # Move original source PDF archive safely away out of inbox area
                migrate_file(pdf_path, PROCESSED_PDF)
        except ImportError:
            logger.error("Dependency 'pdf2image' is missing inside active virtual environment shell. Skipping PDF conversion steps.")
        except Exception as e:
            logger.error(f"Failed processing source PDF pipeline extraction steps: {str(e)}")

    client = get_genai_client()
    from google.genai import types
    
    all_images = list(IMAGE_INBOX.glob("*.jpeg")) + list(IMAGE_INBOX.glob("*.jpg")) + list(IMAGE_INBOX.glob("*.png"))
    if not all_images:
        logger.info("No journal images found inside the workspace inbox zone.")
        return

    grouped_documents = defaultdict(list)
    for img_path in all_images:
        match = re.match(r"^(.*?)[-_\s][pP](\d+)\.", img_path.name)
        base_name = match.group(1) if match else img_path.stem
        page_num = int(match.group(2)) if match else 1
        grouped_documents[base_name].append((img_path, page_num))

    for base_name, page_tuples in grouped_documents.items():
        ordered_paths = [path for path, num in sorted(page_tuples, key=lambda x: x[1])]
        logger.info(f"Dispatching payload bundle [{base_name}] to {PRIMARY_VISION_MODEL}...")
        
        sys_instruction = f"""
        You are a specialized handwriting transcriber. Your lone task is to look at the user's handwritten imagery and type out the written words exactly as they appear.
        
        TEXT FLOW INSTRUCTION:
        Do NOT mirror the physical line breaks of the journal page where text simply runs out of space at the margin. 
        Allow sentences to flow continuously on a single line until a natural paragraph break or an explicit blank line is reached in the source text. 
        Preserve original structural paragraph divisions, but reconstruct the text into smooth, continuous prose.
        
        LIST FORMATTING PROTOCOL:
        If the text contains a numbered list (1., 2., etc.) or a bulleted list, you MUST separate each distinct list item with a complete blank line. Do not bunch them together on consecutive lines.

        UNCLEAR HANDWRITING PROTOCOL:
        If a word or phrase is smudged, illegible, or difficult to read with absolute confidence, make your best context-aware guess and wrap it strictly in HTML underline tags (e.g., <u>guessed word</u>).

        VISUAL DIAGRAM & SKETCH PROTOCOL:
        If you encounter a hand-drawn sketch, diagram, map, or visual doodle on the page, isolate it from the running prose and provide an inline, objective, descriptive alt-text summary enclosed in bold brackets, describing its contents and layout contextually.
        Example format: **[Hand-Drawn Diagram: Detailed description of the sketch contents and structural elements]**

        CRITICAL INSTRUCTION FOR MULTI-PAGE CONTINUATIONS: 
        Only open a brand-new START/END TEMPLATE block when you encounter an explicitly written new date header on a page. 
        If text flows across pages without a header, append it seamlessly to the current template entry text block.

        Output precisely matching this layout format using standard YYYY-MM-DD hyphenated dates:
        
        --- START TEMPLATE ---
        ## YYYY-MM-DD
        *Journal * File: {base_name}*

        [Transcription content]
        --- END TEMPLATE ---
        """
        
        try:
            contents_payload = [Image.open(p) for p in ordered_paths]
            contents_payload.append(f"Isolate, split, and transcribe all entry dates found across these sequential journal frames for: {base_name}")
            
            response = client.models.generate_content(
                model=PRIMARY_VISION_MODEL,
                contents=contents_payload,
                config=types.GenerateContentConfig(system_instruction=sys_instruction, temperature=0.2)
            )
            # --- START DEBUG - useful when troubleshooting API responses, uncomment if needed ---
            #print("\n" + "#" * 50)
            #print("DIAGNOSTIC: EXAMINING GEMINI RESPONSE HEAD AND TAIL")
            #print("#" * 50)
            #if not response.text:
            #    print("🚨 CRITICAL: The response text is COMPLETELY EMPTY.")
            #    if hasattr(response, 'candidates') and response.candidates:
            #        print(f"Finish Reason: {response.candidates[0].finish_reason}")
            #else:
            #    print(f"Total Text Length Character Count: {len(response.text)}")
            #    print(f"Contains '--- START TEMPLATE ---': { '--- START TEMPLATE ---' in response.text }")
            #    print("\n--- FIRST 100 CHARACTERS ---")
            #    print(response.text[:100])
            #    print("\n--- LAST 100 CHARACTERS ---")
            #    print(response.text[-100:])
            #print("#" * 50 + "\n")
            # --- END DEBUG ---
            # ------------------------------------------
            log_api_usage_and_cost(PRIMARY_VISION_MODEL, response.usage_metadata)
            
            bundle_was_processed = False
            for block in re.findall(r"--- START TEMPLATE ---(.*?)--- END TEMPLATE ---", response.text or "", re.DOTALL):
                output_content = block.strip()
                date_match = re.search(r"##\s*(\d{4}-\d{2}-\d{2})", output_content)
                if date_match:
                    extracted_date = date_match.group(1)
                    try:
                        date_obj = datetime.strptime(extracted_date, "%Y-%m-%d")
                        day_name = date_obj.strftime("%A")
                        output_content = re.sub(r"##\s*" + extracted_date, f"## {extracted_date}, {day_name}", output_content)
                    except ValueError:
                        pass
                else:
                    extracted_date = "unknown-date"
                
                write_markdown_entry(date_str=extracted_date, suffix_type="journal", content=output_content)
                bundle_was_processed = True

            if bundle_was_processed:
                for img_path in ordered_paths:
                    migrate_file(img_path, PROCESSED_IMAGE)
        except Exception as e:
            logger.error(f"Error processing image bundle {base_name}: {str(e)}")


# =====================================================================
# PIPELINE B: VOICE MEMO TRANSCRIPTION ENGINE (JSON METADATA MODE)
# =====================================================================
def process_voice_pipeline():
    logger.info(f"Initializing Pipeline B: Voice Processing Engine (JSON Batch Mode using {PRIMARY_TEXT_MODEL})")
    client = get_genai_client()
    
    json_files = list(VOICE_INBOX.glob("*.json"))
    if not json_files:
        logger.info("No transcription batch JSON files found inside the inbox zone.")
        return

    for json_path in json_files:
        logger.info(f"Opening batch transcription file: {json_path.name}")
        try:
            voice_records = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.error(f"Failed parsing target JSON file {json_path.name}: {str(e)}")
            continue

        for record in voice_records:
            filename = record.get("filename", "unknown_file.m4a")
            raw_transcript = record.get("transcript", "").strip()
            if not raw_transcript:
                continue

            try:
                recorded_at_str = record.get("recorded_at", "")
                final_entry_date = recorded_at_str.split(" ")[0] if recorded_at_str else datetime.now().strftime("%Y-%m-%d")
                duration_human = record.get("duration_human", "0m 0s")
                display_title = record.get("name", "Untitled Recording")

                try:
                    date_obj = datetime.strptime(final_entry_date, "%Y-%m-%d")
                    day_name = date_obj.strftime("%A")
                except ValueError:
                    day_name = "Unknown Day"

                cleanup_prompt_schema = f"""
                You are an elite archival linguistic editor processing an unformatted voice-to-text transcript.
                
                Task:
                1. Clean up transcription formatting variations, strip verbal fillers (uhs, ums, repetitions).
                2. Repair incomplete grammatical split fragments into smooth, running prose.
                3. Map ambiguous or contextually unclear phrases using HTML underline tags (e.g. <u>unclear word</u>).
                4. Retain any descriptive cues where the speaker outlines instructions or requests structures (like graphs, links, or specific numbered points).

                Raw Input Transcript to Process:
                \"\"\"{raw_transcript}\"\"\"

                Output format layout matching this exact markdown standard structure:
                --- START TEMPLATE ---
                ## {final_entry_date}, {day_name}
                *Voice Memo * {duration_human} * Memo Name: {display_title} * File: {filename}*

                [Polished Content]
                --- END TEMPLATE ---
                """

                response = client.models.generate_content(
                    model=PRIMARY_TEXT_MODEL,
                    contents=cleanup_prompt_schema,
                )
                log_api_usage_and_cost(PRIMARY_TEXT_MODEL, response.usage_metadata)

                blocks = re.findall(r"--- START TEMPLATE ---(.*?)--- END TEMPLATE ---", response.text or "", re.DOTALL)
                output_content = blocks[0].strip() if blocks else response.text.strip()

                # Track entries cleanly by visible name instead of file string hashes
                file_stem = sanitize_filename(display_title)
                write_markdown_entry(date_str=final_entry_date, suffix_type=f"{file_stem}_voice", content=output_content)

            except Exception as e:
                logger.error(f"Error processing voice entry log for {filename}: {str(e)}")
        
        migrate_file(json_path, PROCESSED_VOICE)


# =====================================================================
# PIPELINE C: MASTER VOLUME ASSEMBLY ENGINE
# =====================================================================
def repair_markdown_paragraph_spacing(text: str) -> str:
    """
    Standardizes Markdown spacing by ensuring exactly one blank line separates 
    all consecutive lines of content, effectively converting single newlines 
    into double newlines while preventing double-stacking if spacing is already fine.
    """
    # Split text into individual lines regardless of original line ending types
    raw_lines = text.replace("\r\n", "\n").split("\n")
    processed_lines = []
    
    for line in raw_lines:
        stripped = line.strip()
        
        # If the line is empty, skip it. This completely eliminates existing 
        # double/triple spacing so we can reconstruct it uniformly.
        if not stripped:
            continue
            
        # If we already have lines in our processed array, insert a blank spacer 
        # string before adding the next line of content. 
        if processed_lines:
            processed_lines.append("")
            
        processed_lines.append(stripped)
        
    return "\n".join(processed_lines)

def assemble_volumes_pipeline():
    logger.info("Initializing Pipeline C: Volume Assembly")
    md_files = sorted(list(ENTRIES_DIR.glob("*.md")), key=lambda p: p.name)
    if not md_files:
        logger.info("No modular entries found to bundle.")
        return

    master_book_path = VOLUMES_DIR / MASTER_COMPILATION_FILE
    logger.info(f"Binding all entries chronologically into: {master_book_path.name}")

    # Initialize basic tracking counters
    unique_dates = set()
    total_words = 0

    try:
        # Pre-pass loop to gather stats before we open the file for writing
        for md_path in md_files:
            total_words += len(md_path.read_text(encoding="utf-8").split())
            date_prefix = md_path.name.split('_')[0]
            if re.match(r"^\d{4}-\d{2}-\d{2}$", date_prefix):
                unique_dates.add(date_prefix)
        
        estimated_pages = max(1, int(round(total_words / 500, 0)))
        with master_book_path.open("w", encoding="utf-8") as f:
            f.write("# MY DIGITIZED DIARY: COMPLETE COMPILATION\n\n")
            f.write(f"Generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            # Inject the stats block at the top of the master compilation for easy reference
            f.write("**MASTER DIARY STATISTICS**\n\n")
            f.write(f" Unique Dates Recorded:  {len(unique_dates)} days | ")
            f.write(f" Total Entries:  {len(md_files)} | ")
            f.write(f" Word Count:     {total_words:,} words | ")
            f.write(f" Estimated Pages:  ~{estimated_pages} pages\n\n")
            f.write("=" * 60 + "\n\n")
            
            for md_path in md_files:
                raw_content = md_path.read_text(encoding="utf-8")
                # Clean up formatting spacing anomalies before compilation rendering
                formatted_content = repair_markdown_paragraph_spacing(raw_content)
                f.write(formatted_content + "\n\n---\n\n")
                                
        logger.info(
            f"Master book compilation completed successfully. "
            f"[Stats -> Days: {len(unique_dates)} | Entries: {len(md_files)} | Words: {total_words:,} | Est. Pages: {estimated_pages}]"
        )
    except Exception as e:
        logger.error(f"Failed writing master book compilation to volume folder: {str(e)}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Digitized Diary Automation CLI Suite")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--all", action="store_true", help="Run the full end-to-end pipeline suite.")
    group.add_argument("--journal", action="store_true", help="Execute Pipeline A (Journal OCR processing).")
    group.add_argument("--voice", action="store_true", help="Execute Pipeline B (Voice transcription cleanup).")
    group.add_argument("--assemble", action="store_true", help="Execute Pipeline C (Volume bundle engine).")

    args = parser.parse_args()
    init_directory_tree()

    # -----------------------------------------------------------------
    # ROBUST LOG MUTING PROTOCOL
    # Intercept package tree initialization signals to drop SDK noise
    # -----------------------------------------------------------------
    for logger_name in logging.root.manager.loggerDict:
        if logger_name.startswith("google.genai"):
            logging.getLogger(logger_name).setLevel(logging.WARNING)
    logging.getLogger("google.genai").setLevel(logging.WARNING)
    # -----------------------------------------------------------------

    if args.all or args.journal: process_journal_pipeline()
    if args.all or args.voice: process_voice_pipeline()
    if args.all or args.assemble: assemble_volumes_pipeline()
    
    # Run the comprehensive global financial audit report output
    log_global_run_summary()
    logger.info("Automation pipeline run executed cleanly.")
