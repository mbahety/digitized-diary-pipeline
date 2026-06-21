# digitized-diary-pipeline

Turn years of handwritten journal pages and voice memo transcripts into a single, chronological markdown diary.

This pipeline takes two very different kinds of input, scanned handwriting and voice memo transcripts, and produces one consistent output: clean markdown entries, organized by date, compiled into a master volume.

It is the second half of a two-part project. The first half, extracting transcripts and metadata from Apple Voice Memos, lives in a companion repo: [apple-voice-memo-exporter](https://github.com/mbahety/apple-voice-memo-exporter). This pipeline expects that script's JSON output as one of its inputs.

---

## What it does

The pipeline has three independent stages, runnable together or separately:

**Pipeline A: Journal OCR.** Takes scanned journal pages (PDF or image files) and uses a vision-capable LLM (Google Gemini) to transcribe handwriting into clean markdown prose. Unclear words are wrapped in `<u>` tags rather than guessed silently. Hand-drawn sketches or diagrams get a short descriptive caption instead of being dropped.

**Pipeline B: Voice memo cleanup.** Takes the JSON output from [apple-voice-memo-exporter](https://github.com/mbahety/apple-voice-memo-exporter) (recording name, date, duration, transcript) and uses Gemini to clean up the raw transcript: stripping filler words (ums/uhs), repairing fragmented sentences, and formatting it into the same markdown structure as the journal entries.

**Pipeline C: Volume assembly.** Takes every individual markdown entry produced by Pipelines A and B and compiles them, in chronological order, into one master markdown file, with a small statistics summary (entry count, word count, estimated page count) at the top.

Each pipeline can run on its own, or all three can run in sequence in a single command.

---

## Requirements

| Requirement | Notes |
|---|---|
| **Python 3.9+** | Check with `python3 --version` |
| **Google Gemini API key** | Required for Pipelines A and B. Set as the `GEMINI_API_KEY` environment variable |
| **pdf2image** | Required only if feeding in scanned PDFs (Pipeline A). Install with `pip install pdf2image` |
| **Pillow (PIL)** | Used to open and process journal images |
| **poppler** | System dependency required by `pdf2image`. On Mac: `brew install poppler` |

Installation:
Clone the repository and install dependencies:
```bash
git clone https://github.com/mbahety/digitized-diary-pipeline.git
cd digitized-diary-pipeline
pip install pdf2image pillow google-genai
```

Set your API key:
```bash
export GEMINI_API_KEY="your-key-here"
```

---

## Setup

### 1. Directory structure

The pipeline expects (and will create automatically on first run) this folder layout in your working directory:

```
.
├── inbox/
│   ├── pdf_scans/        # Place scanned multi-page journal PDFs here
│   ├── journal_images/   # Extracted Or standalone journal page images (JPG/PNG)
│   └── voice_memos/      # JSON output from apple-voice-memo-exporter goes here
├── entries/              # Output directory for individual markdown entries
├── volumes/              # OUtput directory for compiled master diary
├── processed/
│   ├── pdf_scans/        # Processed PDFs are archived here after Pipeline A runs
│   ├── journal_images/   # Processed images are archived here
│   └── voice_memos/      # Processed JSON batches are archived here
├── model_pricing.json    # Model costs - your own copy, see below
└── digitized_diary_pipeline.py
```

### 2. Scanning your journal pages

Any scanning app that outputs PDF or JPEG works. I used [TurboScan](https://www.turboscan.app/) on iOS, but any scanner app, or even a flatbed scanner, producing standard PDF or image files (.jpg) is compatible.

Place scanned PDFs in `inbox/pdf_scans/`, or individual page images directly in `inbox/journal_images/`.

### 3. Pricing configuration

I am price sensitive when it comes to token costs. The script tracks token usage and estimated API cost per run. It reads pricing rates from a `model_pricing.json` file in the working directory (not included in this repo, since pricing changes over time). Copy the example and fill in current rates from [Google's Gemini pricing page](https://ai.google.dev/gemini-api/docs/pricing):

```bash
cp model_pricing.json.example model_pricing.json
```

If this file is missing, the pipeline still runs, it will simply report zero-cost in its summary rather than failing.

---

## Usage

Run the full pipeline end to end:
```bash
python3 digitized_diary_pipeline.py --all
```

Or run a single stage:
```bash
python3 digitized_diary_pipeline.py --journal    # Pipeline A only - Process and OCR handwritten pages into individual markdown entries
python3 digitized_diary_pipeline.py --voice       # Pipeline B only - Clean and format voice memo JSON entries into individual markdown entries
python3 digitized_diary_pipeline.py --assemble    # Pipeline C only - compile master diary from individual entries
```

A typical workflow:
1. Drop scanned journal PDFs and/or jpg/png images into `inbox/`
2. Drop your voice memo JSON export into `inbox/voice_memos/`
3. Run `--all`
4. Find your compiled diary in `volumes/master_diary_compilation.md`

Every run produces a log file (`digitized_diary_pipeline.log`) and a cost summary printed at the end, showing total tokens used and estimated spend for that run.

---

## Output format

Every entry, whether from a scanned journal page or a voice memo, follows the same markdown template:

```markdown
## 2023-06-15, Thursday
*Journal * File: 20230615-p1*

[Transcribed or cleaned-up content here]
```

Voice memo entries additionally include duration and the original recording name:

```markdown
## 2023-06-15, Thursday
*Voice Memo * 18m 42s * Memo Name: Morning walk thoughts * File: 20230615 073201-A1B2C3D4.m4a*

[Cleaned-up transcript content here]
```

Unclear handwriting is wrapped like this: `<u>uncertain word</u>`. Hand-drawn sketches get a bracketed description: `**[Hand-Drawn Diagram: description of contents]**`.

The final compiled volume stacks every entry chronologically, separated by horizontal rules, with a statistics block at the top.

---

## Notes on cost

This pipeline calls the Gemini API once per journal image bundle and once per voice memo transcript. Costs scale with the number of pages and recordings you process, and with image resolution for Pipeline A. The script tracks and logs token usage and estimated cost per call and per run, but exact pricing depends on your `model_pricing.json` and current Gemini rates, which change over time. Check Google's current pricing before processing a large backlog. I also recommend doing trial run with a small batch first. 

Personally, I spent about $4 in Gemini 2.5 Flash costs across development, debugging, and the final run that produced a 330 page master diary from scanned journal pages and voice memo transcripts spanning over 10 years. I don't journal daily, so the actual page count was modest, your costs will scale up if you have a denser backlog.

---

## Privacy & Personal Data

This pipeline processes personal content: journal entries and voice memo transcripts. None of your actual journal content, scanned images, voice memo data, or compiled volumes should be committed to a public repo.

The `.gitignore` in this repo excludes `inbox/`, `entries/`, `volumes/`, `processed/`, and your `model_pricing.json`. Only the script, `model_pricing.json.example`, and documentation belong in version control.

---

## License

MIT. See [LICENSE](LICENSE).
