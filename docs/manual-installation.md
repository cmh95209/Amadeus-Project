# Manual Installation

What the installer scripts do, step by step — only needed if you'd rather install by hand.


## 0. Requirements

Before installing Amadeus, make sure you have:

- **Git** and **Git LFS** (her voice reference clips and reaction sounds in this repository are stored via LFS)
- **Miniconda or Anaconda** (three Python environments are created: `amadeus`
  on Python 3.10, `amadeus-cm` on Python 3.13 for the memory sidecar, and
  `GPTSoVits` on Python 3.10 - on Linux name it `GPTSoVITS`, exactly the way
  the launcher spells it, because Linux tells upper and lower case apart)
- **Node.js (LTS) + npm** for the web interface
- **macOS**: the Xcode Command Line Tools (`xcode-select --install` in a
  terminal if you don't have them)
- **Windows**: nothing else — the voice patches shipped with this project
  remove the old C-compiler requirement. (An NVIDIA GPU is strongly
  recommended for faster voice synthesis; CPU operation is possible.)
- **Linux**: nothing else on most desktops. If a voice package has to be
  built on your PC, a C compiler is needed (`sudo apt install build-essential`
  on Ubuntu/Debian, or `conda install -c conda-forge c-compiler cxx-compiler`
  inside the `GPTSoVITS` environment). For an NVIDIA card, install NVIDIA's
  own driver first.

Note: **FFmpeg (with `ffprobe`) is needed only for videos.** Her voice is
served as WAV through Python audio libraries and never invokes FFmpeg; sending
her a video does. The installers put a copy in `~/.amadeus-tools/ffmpeg/bin`
when the system has none; one on your `PATH` works too.

To actually chat she needs a language model: either an OpenAI-compatible
server (local or cloud), configured in the app after installation, or her
own built-in model (see [step 10](#10-optional-her-built-in-local-model)).

**Linux** has its own one-shot installer since November 2026
(`scripts/install_linux.sh`, see the README), made for Ubuntu 22.04 / 24.04
and Debian 12. By hand, this guide works on Linux too: wherever a step shows
a macOS command, use the same one on Linux.

## 1. Clone Amadeus

```bash
git clone https://github.com/cmh95209/Amadeus-Project.git
cd Amadeus-Project
```

Clone GPT-SoVITS (the voice engine) into the project directory:

```bash
git clone https://github.com/RVC-Boss/GPT-SoVITS.git
```

Then switch it to the version Amadeus is tested with (the installers do the
same). The newest GPT-SoVITS may have changes that break her voice:

```bash
git -C GPT-SoVITS checkout 48b1a0169a28582a8984402f82cf438d3bfa6aca
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

(Her voice needs no FFmpeg; videos do — see step 0.)

### Initialize fast-langdetect

GPT-SoVITS uses `fast-langdetect` for language detection. Create its model
cache directory so the very first speech line can never stall:

#### Windows

```bat
mkdir GPT_SoVITS\pretrained_models\fast_langdetect
```

#### macOS / Linux

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

Her voice needs **8 model files (about 1.2 GB)** from the voice engine's
Hugging Face page - the same files, from the same pinned version, as the
installers. Do not clone the whole page: it holds every engine version
(~6.5 GB, and a Git LFS clone needs about twice that on disk).

From the Amadeus project root, go into the voice engine's model folder:

```bash
cd GPT-SoVITS/GPT_SoVITS/pretrained_models
```

Then download the 8 files (the same commands work on Windows 10/11, macOS and
Linux - `curl` is built in):

```bash
curl -L --fail --create-dirs -o "chinese-roberta-wwm-ext-large/config.json" "https://huggingface.co/lj1995/GPT-SoVITS/resolve/8f8857563c040d542de108a2c18b0425abd6c9c5/chinese-roberta-wwm-ext-large/config.json"
curl -L --fail --create-dirs -o "chinese-roberta-wwm-ext-large/tokenizer.json" "https://huggingface.co/lj1995/GPT-SoVITS/resolve/8f8857563c040d542de108a2c18b0425abd6c9c5/chinese-roberta-wwm-ext-large/tokenizer.json"
curl -L --fail --create-dirs -o "chinese-roberta-wwm-ext-large/pytorch_model.bin" "https://huggingface.co/lj1995/GPT-SoVITS/resolve/8f8857563c040d542de108a2c18b0425abd6c9c5/chinese-roberta-wwm-ext-large/pytorch_model.bin"
curl -L --fail --create-dirs -o "chinese-hubert-base/config.json" "https://huggingface.co/lj1995/GPT-SoVITS/resolve/8f8857563c040d542de108a2c18b0425abd6c9c5/chinese-hubert-base/config.json"
curl -L --fail --create-dirs -o "chinese-hubert-base/preprocessor_config.json" "https://huggingface.co/lj1995/GPT-SoVITS/resolve/8f8857563c040d542de108a2c18b0425abd6c9c5/chinese-hubert-base/preprocessor_config.json"
curl -L --fail --create-dirs -o "chinese-hubert-base/pytorch_model.bin" "https://huggingface.co/lj1995/GPT-SoVITS/resolve/8f8857563c040d542de108a2c18b0425abd6c9c5/chinese-hubert-base/pytorch_model.bin"
curl -L --fail --create-dirs -o "gsv-v2final-pretrained/s1bert25hz-5kh-longer-epoch=12-step=369668.ckpt" "https://huggingface.co/lj1995/GPT-SoVITS/resolve/8f8857563c040d542de108a2c18b0425abd6c9c5/gsv-v2final-pretrained/s1bert25hz-5kh-longer-epoch=12-step=369668.ckpt"
curl -L --fail --create-dirs -o "gsv-v2final-pretrained/s2G2333k.pth" "https://huggingface.co/lj1995/GPT-SoVITS/resolve/8f8857563c040d542de108a2c18b0425abd6c9c5/gsv-v2final-pretrained/s2G2333k.pth"
```

If one is interrupted, run its line again. To make sure a file is complete,
compare its size and SHA-256 with the table below (`sha256sum <file>` on
Linux, `shasum -a 256 <file>` on macOS, `certutil -hashfile <file> SHA256` on
Windows):

| File | Bytes | SHA-256 |
|---|---:|---|
| `chinese-roberta-wwm-ext-large/config.json` | 963 | `3d57de2fd7e80d0e5c8ff194f0bbb6baa10df7e43fc262a0cc71298a78b0a3e5` |
| `chinese-roberta-wwm-ext-large/tokenizer.json` | 268,962 | `173796956820ea27bd14f76bf28162607ff4254807e2948253eb5b46f5bb643b` |
| `chinese-roberta-wwm-ext-large/pytorch_model.bin` | 651,225,145 | `e53a693acc59ace251d143d068096ae0d7b79e4b1b503fa84c9dcf576448c1d8` |
| `chinese-hubert-base/config.json` | 1,449 | `c3e5060a1277e0f078cc6be9da4528a605dba6ece93018981fe2c820e5c7b103` |
| `chinese-hubert-base/preprocessor_config.json` | 212 | `dcd684124d06722947939d41ea6ae58dbf10968c60a11a29f23ddc602c64a29b` |
| `chinese-hubert-base/pytorch_model.bin` | 188,811,417 | `24164f129c66499d1346e2aa55f183250c223161ec2770c0da3d3b08cf432d3c` |
| `gsv-v2final-pretrained/s1bert25hz-5kh-longer-epoch=12-step=369668.ckpt` | 155,315,150 | `732f94e63b148066e24c7f9d2637f3374083e637635f07fbdb695dee20ddbe1f` |
| `gsv-v2final-pretrained/s2G2333k.pth` | 106,035,259 | `924fdccaa3c574bf139c25c9759aa1ed3b3f99e19a7c529ee996c2bc17663695` |

Also pre-download the language-detection model so the first voice line is
instant:

```bash
cd fast_langdetect
curl -L --fail -o lid.176.bin https://dl.fbaipublicfiles.com/fasttext/supervised-models/lid.176.bin
cd ../../../..
```

(That last line takes you back to the Amadeus project root.)

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

Amadeus is paired with the upstream GPT-SoVITS project (tested as of v3.0
against upstream commit `48b1a01`, the version from step 1, plus the voice
patches from step 2). Do not update it ahead of Amadeus (see [Updating Amadeus](using-amadeus.md#updating-amadeus)).

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

Two things download by themselves the first time they are needed, so the
first time can take a little longer: her Japanese pronunciation dictionary
(her first voice line) and the speech model for videos (about 0.5 GB, the
first video you send her).

## 10. Optional: her built-in local model

The installers can set up her own model (Gemma 4, run by llama.cpp on your
graphics card) so she needs no server of yours and no cloud account. By
hand there are two ways. Both need a graphics card with **8 GB** or more
(6-7 GB works, slowly).

These values match installer **v4.13** (Windows) / **v1.7** (Mac) /
**v1.1** (Linux). On a Mac with an Apple chip, the Mac installer is the
easier route.

### Download the files

**llama.cpp**, release **`b11400`**, from
<https://github.com/ggml-org/llama.cpp/releases/tag/b11400>. Pick the build
for your system: the CUDA build for an NVIDIA card (also its `cudart-…`
file, unpacked into the same folder as `llama-server` - on Linux copy the
files out of the `cudart-…` folder next to `llama-server`), otherwise the
Vulkan build. Linux uses the `…-bin-ubuntu-…` files (CUDA 13.4 needs NVIDIA
driver 580 or newer, CUDA 12.8 needs 570 or newer). Unpack it into a
`models/llama.cpp/` folder next to `Amadeus-Project/`.

**Her model**, one of these two, plus its vision file (so she can see
pictures). Put both in the `models/` folder:

| Your graphics card | Model file | Vision file (save it under this name) |
|---|---|---|
| 12 GB or more | [gemma-4-12B-it-qat-UD-Q4_K_XL.gguf](https://huggingface.co/unsloth/gemma-4-12b-it-qat-GGUF/resolve/980b060c40a8539ac159e0501a3e0f66a6365af3/gemma-4-12B-it-qat-UD-Q4_K_XL.gguf?download=true) (6.7 GB) | [mmproj-F16.gguf](https://huggingface.co/unsloth/gemma-4-12b-it-qat-GGUF/resolve/980b060c40a8539ac159e0501a3e0f66a6365af3/mmproj-F16.gguf?download=true) → `gemma-4-12B-it-qat-mmproj-F16.gguf` |
| 6-11 GB | [gemma-4-12b-it-Q3_K_M.gguf](https://huggingface.co/unsloth/gemma-4-12b-it-GGUF/resolve/fc034cfff751157913579611efad8462ac1be606/gemma-4-12b-it-Q3_K_M.gguf?download=true) (5.7 GB) | [mmproj-F16.gguf](https://huggingface.co/unsloth/gemma-4-12b-it-GGUF/resolve/fc034cfff751157913579611efad8462ac1be606/mmproj-F16.gguf?download=true) → `gemma-4-12b-it-mmproj-F16.gguf` |

The two vision files have the same download name but are different files:
rename each one as shown, or her model can't find it.

You should end up with:

```text
Amadeus/                 # any folder name
├── Amadeus-Project/
└── models/
    ├── gemma-4-12B-it-qat-UD-Q4_K_XL.gguf
    ├── gemma-4-12B-it-qat-mmproj-F16.gguf
    └── llama.cpp/       # llama-server(.exe) is somewhere inside
```

### Way 1: run it yourself (simplest)

Start llama.cpp's server yourself, then connect her to it like any other
server (Settings → Connection, address `http://127.0.0.1:8080/v1`):

```bash
llama-server -m models/gemma-4-12B-it-qat-UD-Q4_K_XL.gguf --mmproj models/gemma-4-12B-it-qat-mmproj-F16.gguf -c 24576 -ngl 999 --jinja --port 8080
```

(For the 6-11 GB file use `-c 16384`.) You start and stop it yourself.

### Way 2: let Amadeus run it (like the installers do)

Create the file `Amadeus-Project/backend/data/local_llm.json`. The app then
starts and stops her model by itself, and Settings → Connection shows it as
**Built-in** (with load/unload and model switching). Replace the two paths
with the real ones on your PC (forward slashes `/` work on Windows too):

```json
{
  "mode": "builtin",
  "model": "12B",
  "model_label": "Gemma 4 12B (QAT)",
  "model_file": "C:/Users/YOU/Amadeus/models/gemma-4-12B-it-qat-UD-Q4_K_XL.gguf",
  "model_ok": true,
  "server_exe": "C:/Users/YOU/Amadeus/models/llama.cpp/llama-server.exe",
  "server_ok": true,
  "llama_release": "b11400",
  "build": "cuda-12.4",
  "tier": "12-15",
  "context": 24576,
  "kv_cache": "q8_0",
  "voice_device": "cuda",
  "gpu_layers": 999,
  "port": 8091,
  "history_default": 14000,
  "vram_need_gb": 9.6
}
```

Then change these values to match your graphics card (the same table the
installer uses):

| Card | `model` | `model_label` | `tier` | `context` | `kv_cache` | `voice_device` | `history_default` | `vram_need_gb` |
|---|---|---|---|---|---|---|---|---|
| 6-7 GB | `12B-Q3` | Gemma 4 12B (3-bit, for 8 GB cards) | `6-7` | 16384 | `q4_0` | `cpu` | 6000 | 4.6 |
| 8-11 GB | `12B-Q3` | Gemma 4 12B (3-bit, for 8 GB cards) | `8-11` | 16384 | `q4_0` | `cpu` | 6000 | 6.5 |
| 12-15 GB | `12B` | Gemma 4 12B (QAT) | `12-15` | 24576 | `q8_0` | `cuda` | 14000 | 9.6 |
| 16-23 GB | `12B` | Gemma 4 12B (QAT) | `16-23` | 32768 | `f16` | `cuda` | 22000 | 11.0 |
| 24 GB+ | `12B` | Gemma 4 12B (QAT) | `24-plus` | 49152 | `f16` | `cuda` | 32000 | 11.5 |

- `build`: `cuda-12.4` (or `cuda-13.4`) for an NVIDIA card - on Linux
  `cuda-12.8` or `cuda-13.4` - and `vulkan` otherwise.
- `voice_device`: `cpu` runs her voice on the processor, leaving the graphics
  card to her model. Without an NVIDIA card, always use `cpu`.
- To go back to your own server or a cloud model, set `"mode": "custom"`.

Start Amadeus the normal way. The first start loads her model, which can take
a minute.
