# ============================================================================
#  AMADEUS - ONE-SHOT INSTALLER FOR WINDOWS   v2  (resumable / safe to re-run)
#  Installs everything and connects Amadeus to your LOCAL (Unsloth) model.
#
#  This is the official installer for this fork. It is also attached to the
#  latest release on GitHub, if you prefer downloading it from there.
#
#  WHAT'S NEW IN v2 (September 13, 2026):
#   - Installs YOUR fork's feature branch (local LLM support, multi-session
#     conversations, voice replay, connection test button, ...) instead of
#     the original project.
#   - Existing installs get gently updated from the fork when it is safe
#     (never touches a folder that has unsaved local changes).
#   - No longer overwrites backend/llm.py - the project now ships a smarter
#     one that re-finds your model server after Unsloth restarts.
#   - If your model server is running during install, its address is saved
#     automatically (and its model list is shown).
#   - Updated package list + a startup smoke test for the backend.
#
#  HOW TO RUN (same every time - it picks up where it left off):
#   1) Save this file somewhere you remember, e.g. your Downloads folder.
#      (If you moved it elsewhere, put its real path in step 3.)
#   2) Windows key -> type "PowerShell" -> right-click -> "Run as administrator".
#   3) Paste this ONE line and press Enter:
#        Set-ExecutionPolicy -Scope Process Bypass; & "$HOME\Downloads\install_windows.ps1"
#   4) Wait. If it ever stops, send your helper the install_log.txt in your Amadeus folder.
# ============================================================================

$ErrorActionPreference = "Continue"
$ProgressPreference    = "SilentlyContinue"   # makes downloads noticeably faster

$InstallDir = Join-Path $env:USERPROFILE "Amadeus"
$LogPath    = Join-Path $InstallDir "install_log.txt"
# Your fork's main branch holds all the features; this is the branch to install.
$ForkUrl   = "https://github.com/cmh95209/Amadeus-Project.git"
$Branch    = "main"

function Log($msg, $color="Gray"){
    Write-Host $msg -ForegroundColor $color
    try { Add-Content -Path $LogPath -Value ("{0}  {1}" -f (Get-Date -Format 'HH:mm:ss'), $msg) } catch {}
}
function Step($n,$title){ Log ""; Log ("==== STEP $n : $title ====") "Cyan" }
function Die($msg){
    Log "" "Red"; Log ("INSTALL STOPPED: " + $msg) "Red"
    Log ("Please open and send this file to your helper:  " + $LogPath) "Yellow"
    Read-Host "Press Enter to close this window" | Out-Null
    exit 1
}
function Refresh-Path {
    $m = [Environment]::GetEnvironmentVariable("Path","Machine")
    $u = [Environment]::GetEnvironmentVariable("Path","User")
    $env:Path = "$m;$u"
}

# Prove a folder is a COMPLETE clone by checking that these files exist inside it.
function Test-Complete($dest, $markers){
    foreach ($m in $markers) { if (-not (Test-Path (Join-Path $dest $m))) { return $false } }
    return $true
}

# Download a git repo, but skip it if already complete, and clean up any
# half-finished folder from an interrupted previous attempt before retrying.
function Ensure-Clone($url, $dest, $branch, $markers){
    $leaf = Split-Path $dest -Leaf
    if (Test-Complete $dest $markers) { Log ("  Already have " + $leaf + " - skipping download.") "Yellow"; return }
    if (Test-Path $dest) {
        Log ("  Found an incomplete " + $leaf + " folder from a previous attempt - removing it.") "Yellow"
        Remove-Item -Recurse -Force $dest -ErrorAction SilentlyContinue
        if (Test-Path $dest) { throw ("Could not remove the old " + $leaf + " folder. Close any program using it and run the installer again.") }
    }
    if ($branch) { & git clone -b $branch $url $dest } else { & git clone $url $dest }
    if ($LASTEXITCODE -ne 0) { throw ("Could not download " + $leaf + ". Check your internet connection, then run the installer again.") }
    if (-not (Test-Complete $dest $markers)) { throw ("Downloaded " + $leaf + " but it looks incomplete. Run the installer again to retry.") }
    Log ("  Downloaded " + $leaf + ".") "Green"
}

# Create a conda environment only if it does not already exist (safe to re-run).
function Ensure-Env($name, $py="3.10"){
    $names = & $conda env list | ForEach-Object { ($_ -split '\s+')[0] }
    if ($names -contains $name) { Log ("  Python environment '" + $name + "' already exists.") "Yellow"; return }
    & $conda create -n $name python=$py -y | Out-Null
    if ($LASTEXITCODE -ne 0) { throw ("Could not create the '" + $name + "' Python environment.") }
}

# --- v2 helpers: is a model server actually answering? -----------------------
function Test-TcpPort($host, $port){
    try {
        $c = New-Object System.Net.Sockets.TcpClient
        $ar = $c.BeginConnect($host, $port, $null, $null)
        if (-not $ar.WaitOne(1500)) { $c.Close(); return $false }
        $ok = $c.Connected
        $c.Close()
        return $ok
    } catch { return $false }
}
function Test-UrlAlive($url){
    try { $u = [Uri]$url; return (Test-TcpPort $u.Host $u.Port) } catch { return $false }
}
# Ask an OpenAI-compatible server for its model list (empty if it does not answer).
function Get-LlmModels($baseUrl){
    try {
        $r = Invoke-RestMethod -Uri ($baseUrl.TrimEnd('/') + '/models') -TimeoutSec 5 -ErrorAction Stop
        if ($r.data) { return @($r.data | ForEach-Object { $_.id }) }
    } catch {}
    return @()
}

New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
Set-Content -Path $LogPath -Value ("Amadeus install (installer v2) started " + (Get-Date))

try {

    Log "============================================================" "Green"
    Log "  AMADEUS INSTALLER v2  (safe to re-run - it resumes where it stopped)" "Green"
    Log "============================================================" "Green"
    Log "It downloads several gigabytes, so give it time. Keep this window open." "Yellow"
    Read-Host "Press Enter to begin" | Out-Null

    # ---------- STEP 1: winget ----------
    Step 1 "Checking the Windows installer tool (winget)"
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Die "winget was not found. It is built into Windows 10/11. Update Windows, or install 'App Installer' from the Microsoft Store, then run this script again."
    }
    Log ("winget version: " + ((winget --version | Select-Object -First 1))) "Green"

    # ---------- STEP 2: prerequisites ----------
    Step 2 "Installing required programs (only the ones you don't already have)"

    function Install-Pkg($name, $id){
        Log ("  Installing " + $name + " ... (can take a few minutes)")
        winget install -e --id $id --silent --accept-source-agreements --accept-package-agreements | Out-Null
        if ($LASTEXITCODE -eq 0) { Log ("  Installed " + $name + ".") "Green" }
        else { Log ("  WARNING: " + $name + " install returned code " + $LASTEXITCODE + ". We will double-check below.") "Yellow" }
    }

    if (Get-Command git -ErrorAction SilentlyContinue) { Log "  Git already present." "Green" }
    else { Install-Pkg "Git" "Git.Git"; Refresh-Path }

    $condaHome = Join-Path $env:USERPROFILE "miniconda3"
    if (Test-Path (Join-Path $condaHome "Scripts\conda.exe")) { Log "  Miniconda already present." "Green" }
    else {
        Install-Pkg "Miniconda" "ContinuumAnalytics.Miniconda3"
        if (-not (Test-Path (Join-Path $condaHome "Scripts\conda.exe"))) {
            foreach ($a in @((Join-Path $env:LOCALAPPDATA "Continuum\miniconda3"), "C:\ProgramData\miniconda3")) {
                if (Test-Path (Join-Path $a "Scripts\conda.exe")) { $condaHome = $a; Log ("  Found Miniconda at " + $a) "Yellow"; break }
            }
        }
        if (-not (Test-Path (Join-Path $condaHome "Scripts\conda.exe"))) { Die "Could not find Miniconda after installing it. Look at this window and the log." }
    }
    $conda = Join-Path $condaHome "Scripts\conda.exe"
    Log ("  Using conda: " + $conda)

    if (Get-Command node -ErrorAction SilentlyContinue) { Log "  Node.js already present." "Green" }
    else { Install-Pkg "Node.js" "OpenJS.NodeJS.LTS"; Refresh-Path }

    if (Get-Command ffmpeg -ErrorAction SilentlyContinue) { Log "  FFmpeg already present." "Green" }
    else { Install-Pkg "FFmpeg" "Gyan.FFmpeg"; Refresh-Path }

    winget install -e --id Git.LFS --silent --accept-source-agreements --accept-package-agreements | Out-Null
    & git lfs install 2>$null
    Log "  Git + LFS ready." "Green"

    # ---------- STEP 3: Amadeus project (your fork, main branch) ----------
    Step 3 "Downloading the Amadeus project (your fork, with all new features)"
    $proj = Join-Path $InstallDir "Amadeus-Project"
    if (Test-Complete $proj @("backend\main.py","start_windows.bat")) {
        Log "  Already have Amadeus-Project - checking for updates from your fork." "Yellow"
        if (Test-Path (Join-Path $proj ".git")) {
            Push-Location $proj
            try {
                & git remote add fork $ForkUrl 2>$null | Out-Null   # no-op if already present
                & git fetch fork $Branch 2>$null | Out-Null
                $current = (& git rev-parse --abbrev-ref HEAD).Trim()
                $status  = & git status --porcelain
                if ($current -eq $Branch -and -not $status) {
                    & git merge --ff-only "fork/$Branch" 2>$null | Out-Null
                    if ($LASTEXITCODE -eq 0) { Log "  Updated to the latest version of your fork's main branch." "Green" }
                    else { Log "  Could not fast-forward (local commits present?) - leaving it as is." "Yellow" }
                } elseif ($current -ne $Branch) {
                    Log ("  This folder is on branch '" + $current + "', so I did NOT touch it.") "Yellow"
                    Log ("  The new features live on branch '" + $Branch + "'.") "Yellow"
                } else {
                    Log "  It has unsaved local changes - not updating, so nothing of yours is lost." "Yellow"
                }
            } finally { Pop-Location }
        } else {
            Log "  (Not a git folder - cannot update it; using it as is.)" "Yellow"
        }
    } else {
        Ensure-Clone $ForkUrl $proj $Branch @("backend\main.py","start_windows.bat")
    }

    # ---------- STEP 4: voice engine ----------
    Step 4 "Downloading the voice engine (GPT-SoVITS)"
    $gpt = Join-Path $proj "GPT-SoVITS"
    Ensure-Clone "https://github.com/RVC-Boss/GPT-SoVITS.git" $gpt $null @("requirements.txt","extra-req.txt")

    # ---------- STEP 5: backend env ----------
    Step 5 "Setting up the Amadeus backend (Python environment 'amadeus')"
    Ensure-Env "amadeus"
    & $conda run -n amadeus pip install --upgrade pip | Out-Null
    & $conda run -n amadeus pip install Flask flask-cors requests tqdm langchain langchain-openai pydantic
    if ($LASTEXITCODE -ne 0) { throw "Could not install the backend packages. Send me the log." }
    # Smoke test: the whole backend must import cleanly in this environment.
    $backend = Join-Path $proj "backend"
    Push-Location $backend
    $smoke = & $conda run -n amadeus python -c "import api; print('SMOKE_OK')" 2>&1 | Out-String
    Pop-Location
    if ($smoke -match "SMOKE_OK") { Log "  Backend ready (startup smoke test passed)." "Green" }
    else { Log "  WARNING: the startup smoke test did not pass. If Amadeus fails to start later, send the log." "Yellow"; Log $smoke }

    # ---------- STEP 6: voice env (PyTorch first, then the rest) ----------
    Step 6 "Setting up the voice engine environment (big download, be patient)"
    Ensure-Env "GPTSoVits"
    Push-Location $gpt
    $pipLog = Join-Path $InstallDir "voice_pip.log"

    # --- 6a. Install PyTorch FIRST, with the build that matches your hardware.
    #         Doing it before requirements.txt means the big download happens once
    #         and the rest of the packages see torch as already installed.
    $hasGpu = $false
    try { $nv = & nvidia-smi 2>&1 | Out-String; if ($nv -match "NVIDIA") { $hasGpu = $true } } catch { $hasGpu = $false }
    & $conda run -n GPTSoVits pip uninstall -y torch torchvision torchaudio 2>&1 | Out-Null   # clear any stale/CPU copy
    if ($hasGpu) {
        Log "  NVIDIA GPU found - installing PyTorch CUDA 12.8 (required for RTX 50-series / Blackwell)." "Green"
        & $conda run -n GPTSoVits pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128 2>&1 | Tee-Object -FilePath $pipLog
    } else {
        Log "  No working NVIDIA GPU found - installing the CPU version (works, but voice is slower)." "Yellow"
        & $conda run -n GPTSoVits pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu 2>&1 | Tee-Object -FilePath $pipLog
    }
    if ($LASTEXITCODE -ne 0) {
        Pop-Location
        $tail = (Get-Content $pipLog -Tail 15) -join "`n"
        throw "PyTorch failed to install. The last lines of the error are in voice_pip.log (your Amadeus folder):`n$tail"
    }

    # --- 6b. Install the remaining voice packages (any error is now captured).
    & $conda run -n GPTSoVits pip install -r extra-req.txt --no-deps 2>&1 | Tee-Object -FilePath $pipLog -Append | Out-Null
    & $conda run -n GPTSoVits pip install -r requirements.txt 2>&1 | Tee-Object -FilePath $pipLog -Append | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Pop-Location
        $tail = (Get-Content $pipLog -Tail 15) -join "`n"
        throw "The voice engine packages failed to install. The last lines of the error are in voice_pip.log (your Amadeus folder):`n$tail"
    }
    Pop-Location

    # ---------- STEP 7: confirm PyTorch can actually use your GPU ----------
    Step 7 "Checking that PyTorch can use your GPU"
    $gpuCheck = & $conda run -n GPTSoVits python -c "import torch;print('GPU_OK' if torch.cuda.is_available() else 'GPU_NO')" 2>&1 | Out-String
    if ($hasGpu) {
        if ($gpuCheck -match "GPU_OK") { Log "  PyTorch can see your GPU - voice synthesis will run on it." "Green" }
        else { Log "  NOTE: PyTorch is installed but could not see the GPU yet. Update your NVIDIA driver, then re-run this installer once." "Yellow" }
    } else {
        Log "  Running in CPU mode (no GPU detected)." "Yellow"
    }

    # ---------- STEP 8: pretrained voice models ----------
    Step 8 "Downloading the voice model files (several GB, be patient)"
    $hf  = Join-Path $env:TEMP "GPT-SoVITS-hf"
    # NOTE: this HuggingFace repo keeps every model file at its TOP level -
    # there is no 'pretrained_models' subfolder. So we treat the whole cloned
    # folder as the set of files to copy, and use one marker file (s2G488k.pth)
    # to know the download finished, so a re-run does NOT download again.
    if (-not (Test-Path (Join-Path $hf "s2G488k.pth"))) {
        if (Test-Path $hf) { Remove-Item -Recurse -Force $hf -ErrorAction SilentlyContinue }
        & git clone https://huggingface.co/lj1995/GPT-SoVITS $hf
        if ($LASTEXITCODE -ne 0) { throw "Could not download the voice model files. Check your internet connection, then run the installer again." }
    } else { Log "  Voice model files already downloaded - reusing them." "Yellow" }
    $dst = Join-Path $gpt "GPT_SoVITS\pretrained_models"
    New-Item -ItemType Directory -Force -Path $dst | Out-Null
    # Copy everything from the cloned repo (but skip its hidden .git folder).
    Get-ChildItem -Path $hf -Force | Where-Object { $_.Name -ne ".git" } | ForEach-Object {
        Copy-Item -Path $_.FullName -Destination $dst -Recurse -Force
    }
    if (-not (Test-Path (Join-Path $dst "s2G488k.pth"))) { throw "The voice model files copied but look incomplete (missing s2G488k.pth). Run the installer again to retry." }
    Log "  Voice model files copied into place." "Green"

    # ---------- STEP 9: frontend ----------
    Step 9 "Setting up the web interface (frontend)"
    Push-Location (Join-Path $proj "frontend")
    & npm install | Out-Null
    if ($LASTEXITCODE -ne 0) { Log "  npm install reported an issue, but this is often harmless. Continuing." "Yellow" } else { Log "  Frontend ready." "Green" }
    Pop-Location

    # ---------- STEP 10: wire in the local (Unsloth) model ----------
    # v2 NOTE: we do NOT overwrite backend/llm.py any more - the project ships
    # a smarter one that re-finds your server after Unsloth restarts. We only
    # make sure llm_server.txt points at a live server when we can find one.
    Step 10 "Connecting Amadeus to your local model server"
    $serverFile = Join-Path $backend "llm_server.txt"
    if (-not (Test-Path $serverFile)) { [System.IO.File]::WriteAllText($serverFile, "http://localhost:8000/v1") }
    $configured = ((Get-Content $serverFile -ErrorAction SilentlyContinue) | Select-Object -First 1).Trim()
    $connected = $false
    if ($configured -and (Test-UrlAlive $configured)) {
        Log ("  Your saved server (" + $configured + ") is answering right now.") "Green"
        $models = Get-LlmModels $configured
        if ($models.Count -gt 0) { Log ("  Models it offers: " + ($models -join ", ")) "Green" }
        $connected = $true
    } else {
        if ($configured) { Log ("  Your saved server (" + $configured + ") is not answering yet.") "Yellow" }
        else             { Log "  No server address saved yet." "Yellow" }
        # Probe the ports local model servers usually listen on.
        foreach ($p in @(8080, 8888, 8000)) {
            if (Test-TcpPort "localhost" $p) {
                $candidate = "http://localhost:$p/v1"
                $models = Get-LlmModels $candidate
                if ($models.Count -gt 0) {
                    [System.IO.File]::WriteAllText($serverFile, $candidate)
                    Log ("  Found a live model server on port " + $p + " (models: " + ($models -join ", ") + ").") "Green"
                    Log ("  Saved it to backend\llm_server.txt.") "Green"
                    $connected = $true
                    break
                } else {
                    Log ("  Port " + $p + " is open but does not answer like a model API - leaving your settings alone.") "Yellow"
                }
            }
        }
        if (-not $connected) {
            Log "  No live model server found right now - that is normal if Unsloth is not open."
            Log "  Amadeus will keep looking on its own; you can also set the address manually later."
        }
    }
    $modelFile = Join-Path $backend "data\llm_model.txt"
    if (Test-Path $modelFile) {
        $defaultModel = ((Get-Content $modelFile | Select-Object -First 1).Trim())
        if ($defaultModel) {
            Log ("  Default model name Amadeus will ask for: " + $defaultModel + "   (in backend\data\llm_model.txt)")
        } else {
            Log "  No default model is set - you will choose yours in Settings -> Connection."
        }
    }

    # ---------- DONE ----------
    Step 11 "ALL DONE!"
    Log "" "Green"
    Log "Everything is installed. Here is what to do next:" "Green"
    Log "  1) If you don't have Unsloth Desktop yet, get it from https://unsloth.ai"
    Log "     and load a model in it (e.g. Qwen3). Amadeus needs a model running."
    Log "  2) Double-click this file to START Amadeus:"
    Log ("       " + (Join-Path $proj "start_windows.bat"))
    Log "  3) When it opens in your browser, open Settings -> Connection and"
    Log "     click 'Test connection'. The dot turns green when she can reach"
    Log "     your model. Pick your model from the chips (or type its name),"
    Log "     then paste your API key (Unsloth > Settings > API, starts with sk-unsloth-...)."
    Log "  4) If Amadeus cannot find your model server, open backend\llm_server.txt"
    Log "     and make sure it says http://localhost:<your Unsloth port>/v1"
    Log ""
    Log ("(Install log saved to: " + $LogPath + ")")
    Read-Host "Press Enter to close this window" | Out-Null

} catch {
    Log ("UNEXPECTED ERROR: " + $_.Exception.Message) "Red"
    Log ("At line: " + $_.InvocationInfo.ScriptLineNumber) "Red"
    Die "Something unexpected went wrong."
}
