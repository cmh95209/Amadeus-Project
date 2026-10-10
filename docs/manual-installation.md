# Manual Installation

What the installer scripts do, step by step — only needed if you'd rather install by hand.


## 0. Requirements

Before installing Amadeus, make sure you have:

- **Git** and **Git LFS** (the voice models are stored via LFS)
- **Miniconda or Anaconda** (three Python environments are created: `amadeus`
  on Python 3.10, `amadeus-cm` on Python 3.13 for the memory sidecar, and
  `GPTSoVits` on Python 3.10)
- **Node.js (LTS) + npm** for the web interface
- **macOS**: the Xcode Command Line Tools (`xcode-select --install` in a
  terminal if you don't have them)
- **Windows**: nothing else — the voice patches shipped with this project
  remove the old C-compiler requirement. (An NVIDIA GPU is strongly
  recommended for faster voice synthesis; CPU operation is possible.)

Note: **FFmpeg is NOT required** — Amadeus serves her voice as WAV through
Python audio libraries and never invokes the FFmpeg program.

An OpenAI-compatible LLM server (local or cloud) is needed to actually chat;
it is configured in the app after installation, not during it.

## 1. Clone Amadeus

```bash
git clone https://github.com/cmh95209/Amadeus-Project.git
cd Amadeus-Project
```

Clone GPT-SoVITS (the voice engine) into the project directory:

```bash
git clone https://github.com/RVC-Boss/GPT-SoVITS.git
```

Your local directory will then contain both:

```text
Amadeus-Project/
├── backend/
├── frontend/
├── memory_sidecar/
├── scripts/
└── GPT-SoVITS/      # external project, cloned locally
```

## 2. Apply the voice patches

This project ships five voice-engine patches (a prebuilt Japanese text
helper and pure-Python text segmentation) that make the voice engine work
without a C compiler. Copy them onto the GPT-SoVITS clone, preserving the
folder structure:

```bash
# macOS / Linux
cp -R scripts/voice-patch/* GPT-SoVITS/

# Windows (PowerShell)
Copy-Item -Recurse -Force scripts\voice-patch\* GPT-SoVITS\
```

## 3. Create the Amadeus backend environment

From the repository root:

```bash
cd backend
conda env create -f environment.yml
cd ..
```

This creates the `amadeus` environment (Python 3.10) with all backend
dependencies, including `ddgs` (the search library behind her web search).

## 4. Create the memory sidecar environment

The long-term memory engine needs a newer Python than the app (its pinned
packages require Python 3.12+), so it has its own environment:

```bash
conda create -n amadeus-cm python=3.13 -y
```

That is all you have to do manually: on its **first launch**, the launcher
finds this environment, builds the sidecar's own `memory_sidecar/venv` from
it, and installs the pinned package list (a few minutes of downloads). If
you start the services by hand instead (see [Manual Startup](using-amadeus.md#manual-startup)),
the sidecar venv is built the first time the launcher runs — or you can skip
long-term memory entirely; the app runs fine without it.

## 5. Create the GPT-SoVITS environment

```bash
conda create -n GPTSoVits python=3.10 -y
conda activate GPTSoVits
cd GPT-SoVITS
```

Install GPT-SoVITS dependencies:

```bash
pip install -r extra-req.txt --no-deps
pip install -r requirements.txt
```

(No FFmpeg installation is needed — see step 0.)

### Initialize fast-langdetect

GPT-SoVITS uses `fast-langdetect` for language detection. Create its model
cache directory so the very first speech line can never stall:

#### Windows

```bat
mkdir GPT_SoVITS\pretrained_models\fast_langdetect
```

#### macOS

```bash
mkdir -p GPT_SoVITS/pretrained_models/fast_langdetect
```

Return to the Amadeus project root:

```bash
cd ..
```

## 6. Configure PyTorch

The exact PyTorch installation depends on your hardware.

### NVIDIA GPU (Windows or Linux)

With a recent NVIDIA driver installed, install the CUDA 12.8 build (the same
build the installer uses):

```bash
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128
```

### CPU Only (Windows, Linux, or Intel Mac)

```bash
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu
```

### Apple Silicon Mac

Plain PyPI wheels work and enable MPS (Apple GPU) acceleration:

```bash
pip install torch torchvision torchaudio
```

## 7. Download GPT-SoVITS Pretrained Models

The voice quality depends on pretrained models, stored on Hugging Face via
Git LFS (that is why Git LFS is a requirement):

```bash
cd GPT-SoVITS
git clone https://huggingface.co/lj1995/GPT-SoVITS pretrained_models
```

If the downloaded files are tiny LFS *pointers* instead of real model files
(`git lfs install` missing or the files are a few hundred bytes), run
`git lfs pull` inside `pretrained_models/`.

Also pre-download the language-detection model so the first voice line is
instant:

```bash
cd GPT_SoVITS/pretrained_models/fast_langdetect
# Windows:
curl -L -o lid.176.bin https://dl.fbaipublicfiles.com/fasttext/supervised-models/lid.176.bin
# macOS:
curl -L -O https://dl.fbaipublicfiles.com/fasttext/supervised-models/lid.176.bin
cd ../../..
```

Optionally, pre-download the Western-script text data (pronunciation of
Latin-script names and words in her voice):

```bash
conda activate GPTSoVits
cd GPT-SoVITS
python -c "import nltk; nltk.download('cmudict'); nltk.download('averaged_perceptron_tagger'); nltk.download('averaged_perceptron_tagger_eng')"
cd ..
```

(Skip this and the voice engine will fetch what it needs on first use,
which is slower and can stall on flaky connections.)

Amadeus is paired with the upstream GPT-SoVITS project (validated as of v2.0
against upstream commit `9bbd80a`, plus the voice patches from step 2). Do
not update it ahead of Amadeus (see [Updating Amadeus](using-amadeus.md#updating-amadeus)).

## 8. Install Frontend Dependencies

```bash
cd frontend
npm install
cd ..
```

## 9. Your first meeting

Start the app the normal way ([Launching Amadeus](using-amadeus.md#launching-amadeus)) and
follow the [first-meeting steps](../README.md#your-first-meeting) above: open
Settings → Connection, point her at your model, pick the model, save — and
she speaks.
