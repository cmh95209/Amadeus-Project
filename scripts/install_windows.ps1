# ============================================================================
#  AMADEUS - ONE-SHOT INSTALLER FOR WINDOWS   v4.13  (resumable / safe to re-run)
#  Installs everything Amadeus needs to run.
#
#  This is the official installer for this fork. It is also attached to the
#  latest release on GitHub, if you prefer downloading it from there.
#
#  WHAT'S NEW IN v4.13 (October 2026):
#   - Her own built-in local model, no account needed. The very first
#     question is how Amadeus should think: A) a local model set up
#     automatically, or B) your own model / a cloud service (as before).
#     For A the installer looks at your graphics card and memory, picks the
#     Gemma 4 model that fits this PC, and downloads it (and llama.cpp, the
#     program that runs it) at the end, resumable and checked against the
#     official checksums. Models live in <install folder>\models.
#   - A calm screen: one progress box per step (the current package and its
#     download percentage) instead of hundreds of scrolling lines.
#   - install_log.txt now records EVERYTHING the helper programs print (pip,
#     conda, git, winget, npm), with their result codes - one file to send
#     if something goes wrong. It is saved as UTF-8.
#   - At the end it offers to start Amadeus right away.
#   - The voice engine (GPT-SoVITS) is pinned to the version Amadeus is tested
#     with. Its October 2026 update made her voice take minutes per line;
#     an install that already got it is moved back on the next run.
#
#  WHAT'S NEW IN v4.12 (October 2026, app v2.1):
#   - Installs and updates the fork's main branch (it used to follow a
#     feature branch). An existing install from an older installer is moved
#     to main automatically when it holds nothing of your own.
#   - Installs e2k, the small offline model her voice uses to say English
#     words the Japanese way.
#   - An existing install updates even though the project's published
#     history was cleaned up once: a copy holding nothing of your own is
#     moved onto the new history, and your saved settings stay as they are.
#
#  WHAT'S NEW IN v4.11 (October 2026):
#   - The long package-download steps no longer run through a helper
#     program that could freeze on some Windows PCs without saying
#     anything. The installer now gives each job to the toolbox's own
#     Python directly - same packages, same results, and a stalled
#     download gives up after two minutes instead of freezing forever.
#     If a machine keeps its toolboxes in an unusual place, the old
#     helper route is still used as a fallback, so nothing can get worse
#     than before.
#   - If the right GPU engine is already installed, the installer now sees
#     it and skips the 2.5 GB download - re-runs on such machines move on
#     in seconds instead of minutes.
#   - The web interface setup skips a pointless background check, saving
#     a few seconds on fresh machines.
#
#  WHAT'S NEW IN v4.10 (October 2026):
#   - The installer can now put Amadeus in a folder of your choice: add
#     -InstallDir "C:\Some\Place" to the run command and everything lands
#     there instead. Without that, everything works exactly as before (your
#     user's Amadeus folder).
#
#  WHAT'S NEW IN v4.9 (October 2026):
#   - Her longest replies no longer get cut off: the built-in reply-size
#     limit is bigger now (this is what the "Output max tokens = 4096"
#     setting was working around - you can leave that box unticked).
#   - A rare glitch could leave her English text box showing a copy of
#     her Japanese line. The app now recognizes that (even when the line
#     contains a Western name, like yours) and falls back cleanly - and
#     writes what it saw to the log so the cause can be found. Teaching
#     you Japanese inside her English line is unaffected.
#   - If you pick a model the server does not know (for example a
#     ":batch" model, which only answers in bulk jobs hours later), the
#     error now says exactly that instead of a technical code - and the
#     Settings connection test now warns you about ":batch" names
#     before you save them.
#   - When a web search runs but finds nothing, she answers from her own
#     knowledge and says she could not verify it online (the same
#     honesty wording used when a search fails).
#
#  WHAT'S NEW IN v4.8 (October 2026):
#   - Her web search now works on fresh PCs. General web search runs through
#     a small search library (ddgs) that was missing from the install list,
#     so on a fresh machine she could only answer from her own knowledge -
#     and honestly said so. (Weather always worked: it uses its own data
#     feed and never needed this library.) Found by the fresh-VM test.
#     The install smoke test now also checks for it, so a missing search
#     library can never again hide silently.
#
#  WHAT'S NEW IN v4.7 (October 2026):
#   - If you were already using Amadeus, the installer now updates your
#     install properly. It no longer mistakes the files Amadeus writes on
#     your behalf (her saved connection, her personality notes) for
#     "unsaved work of yours" and refuses to update - found by the
#     fresh-VM test. Your saved values are kept either way: an update
#     only ever touches program files.
#
#  WHAT'S NEW IN v4.6 (October 2026):
#   - The fresh-install test found one more case where her first voice line
#     could come out silent: when a line contains Western-script words (the
#     name you typed in, "AI", ...), her voice engine needs a few small text
#     data files that a fresh PC does not have - and its automatic download
#     was looking for them under old file names, so it fetched the wrong
#     ones. The installer now pre-downloads those files (a few MB), and
#     Amadeus checks for them again every time she starts.
#
#  WHAT'S NEW IN v4.5 (October 2026):
#   - The fresh-install test found the voice engine crashing on launch on a
#     clean machine: its text processing needed jieba_fast, a package that
#     only ships as a source build (needs a C compiler most fresh PCs do
#     not have). The installer now applies the COMPLETE proven set of voice
#     patches (five files) to the GPT-SoVITS copy - the exact code the
#     voice engine is proven to run with, no compiler required.
#
#  WHAT'S NEW IN v4.4 (October 2026):
#   - Cleaner output: conda's "a newer version of conda exists" banners are
#     suppressed (harmless but confusing), and the steps no longer talk
#     about "your fork".
#   - The ALL DONE screen now says what Amadeus actually needs: a cloud
#     model service (e.g. OpenRouter or OpenAI) or a local model server
#     (e.g. KoboldCpp, Unsloth Desktop, NInfer, Ollama or vLLM) - and
#     points you to her introduction message, which walks you through
#     connecting it.
#
#  WHAT'S NEW IN v4.3 (October 2026):
#   - The installer no longer tries to connect Amadeus to a model server
#     while it installs (the fresh-install test showed that step could stop
#     the whole install). Connecting your model - local or cloud - happens
#     when Amadeus first opens, or any time in Settings -> Connection.
#
#  WHAT'S NEW IN v4.2 (October 2026):
#   - The voice engine no longer needs a C compiler on a fresh PC: its
#     Japanese text helper now installs as a prebuilt wheel (building the
#     original package from source fails on machines with modern CMake), and
#     if that one package is ever unavailable the install continues without
#     it - voice still works. Found by the fresh-install test.
#
#  WHAT'S NEW IN v4.1 (October 2026):
#   - Fixed the winget name for Miniconda: Anaconda renamed the package in
#     winget's catalog (the old "ContinuumAnalytics.Miniconda3" is gone,
#     which stopped fresh installs on machines with current winget). The
#     installer now tries the new name first, the old name second (for
#     machines whose winget still knows only the old one), and then its
#     official-download fallback - one of the three always delivers.
#
#  WHAT'S NEW IN v4 (October 2026):
#   - The Miniconda step is now bulletproof: if your PC's winget cannot
#     find or install Miniconda (seen on some fresh systems, e.g. Windows
#     Sandbox), the installer automatically falls back to Anaconda's own
#     official download - the same program, through a link that never
#     changes, with no extra steps for you.
#
#  WHAT'S NEW IN v3 (October 2026):
#   - Installs the long-term memory update: Amadeus remembers you across
#     conversations (her memory engine runs as its own helper program).
#   - The memory engine runs in its own newer Python setup, so it installs
#     cleanly on every machine.
#   - On a brand-new install she now "meets" you first: a short welcome,
#     then you connect her model, and she asks for your name.
#   - Newer Miniconda's terms-of-service check is handled automatically
#     (fixes installs that stopped at Step 5 on some machines).
#
#  WHAT'S NEW IN v2 (September 13, 2026):
#   - Installs YOUR fork (since v4.12 its main branch; local LLM support, multi-session
#     conversations, voice replay, connection test button, ...) instead of
#     the original project.
#   - Existing installs get gently updated from the fork when it is safe
#     (never touches a folder that has unsaved local changes).
#   - No longer overwrites backend/llm.py - the project now ships a smarter
#     one that re-finds your model server after Unsloth restarts.
#   - (Superseded in v4.3: the installer no longer auto-saves a model
#     server address - Amadeus asks for it when you first open her.)
#   - Updated package list + a startup smoke test for the backend.
#
#  HOW TO RUN (same every time - it picks up where it left off):
#   1) Save this file somewhere you remember, e.g. your Downloads folder.
#      (If you moved it elsewhere, put its real path in step 3.)
#   2) Windows key -> type "PowerShell" -> right-click -> "Run as administrator".
#   3) Paste this ONE line and press Enter:
#        Set-ExecutionPolicy -Scope Process Bypass; & "$HOME\Downloads\install_windows.ps1"
#      (Optional: add  -InstallDir "E:\Some\Place"  at the end to install
#       somewhere other than your user's Amadeus folder.)
#   4) Wait. If it ever stops, send your helper the install_log.txt in the
#      folder you installed into.
# ============================================================================

param(
    # Optional: where to install. Example:
    #   .\install_windows.ps1 -InstallDir "C:\Some\Place"
    # Without it, the default (your user profile's "Amadeus" folder) is used.
    [string]$InstallDir = "",
    # ---- Test options (for the project's own test installs; testers never
    #      need them). They change only WHERE the code comes from and the
    #      NAMES of the Python environments - every step below runs the same.
    # Clone from a git repo on this PC instead of GitHub (no push needed).
    [string]$Source = "",
    # Which branch to install (default: the released one).
    [string]$Branch = "main",
    # Give this install its own Python environments (e.g. "test" ->
    # amadeus-test, amadeus-cm-test, GPTSoVits-test), so a test install never
    # touches the environments of another install on the same PC. Remembered
    # in the install folder: a re-run without it keeps the same suffix.
    [string]$EnvSuffix = "",
    # Pretend the graphics card has this much memory (GB), to walk through
    # every local-model tier on one PC. -1 = use the real card.
    [int]$SimulateVramGB = -1,
    # Same for normal memory (RAM, GB). -1 = the real amount.
    [int]$SimulateRamGB = -1,
    # Only look at this PC (graphics card, memory, disk) and print which
    # local model it would get - installs nothing, creates no folder.
    # Add -Json for machine-readable output (used by the project's tests).
    [switch]$HardwareCheckOnly,
    [switch]$Json,
    # Test: go through every built-in-model screen and write its settings
    # file, but download no model (the app then sees the model as missing).
    [switch]$SkipLlmDownload,
    # Test: ONLY the built-in-model screens + settings file (implies
    # -SkipLlmDownload): no programs, environments or project download.
    [switch]$LlmScreensOnly
)
$InstallerVersion = "v4.13"

$ErrorActionPreference = "Continue"
$ProgressPreference    = "SilentlyContinue"   # makes downloads noticeably faster
# Newer Miniconda (25.1 and up - what this installer installs) ships a
# plugin that refuses to build environments unattended until Anaconda's
# channel Terms of Service are accepted (CondaToSNonInteractiveError).
# This variable tells that plugin to accept them automatically, so the
# install never stops waiting for a human. Older conda ignores it.
$env:CONDA_PLUGINS_AUTO_ACCEPT_TOS = "yes"

if (-not $InstallDir) { $InstallDir = Join-Path $env:USERPROFILE "Amadeus" }
$InstallDir = $InstallDir.TrimEnd('\')
$LogPath    = Join-Path $InstallDir "install_log.txt"
# Since v2.1 every install follows the fork's main branch. Installs made by
# older installers sit on the old feature branch; they are moved to main
# (Step 3) when they hold nothing of the user's own.
$ForkUrl       = "https://github.com/cmh95209/Amadeus-Project.git"
$LegacyBranch  = "feature/character-memory-spike"
# The voice engine is PINNED to the version Amadeus is tested with (2026-08-18).
# Its newest version (October 2026: "V5" + a CUDA-graph rework) made her voice
# take minutes per line instead of seconds; an upgrade happens only after it
# is tested here (see the V5 evaluation on the roadmap).
$GptSovitsUrl    = "https://github.com/RVC-Boss/GPT-SoVITS.git"
$GptSovitsCommit = "48b1a0169a28582a8984402f82cf438d3bfa6aca"
# -Source: a local repo stands in for GitHub everywhere (first clone AND the
# update check on a re-run), so a re-run picks up new local commits.
if ($Source) { $ForkUrl = (Resolve-Path -LiteralPath $Source -ErrorAction SilentlyContinue).Path }

# The install log is UTF-8 (Windows' old default encoding turned Japanese text
# and pip's symbols into "?"). Log = screen + log; Write-LogOnly = log only
# (the full output of every outside program lands there - see Invoke-Tool).
$Utf8NoBom = New-Object Text.UTF8Encoding $false
function Write-LogLines($lines, $file = $LogPath){
    if (-not $lines -or -not $file) { return }
    try { [IO.File]::AppendAllText($file, (($lines -join "`r`n") + "`r`n"), $Utf8NoBom) } catch {}
}
function Log($msg, $color="Gray"){
    Write-Host $msg -ForegroundColor $color
    Write-LogLines @(("{0}  {1}" -f (Get-Date -Format 'HH:mm:ss'), $msg))
}
function Write-LogOnly($msg){ Write-LogLines @(("{0}  {1}" -f (Get-Date -Format 'HH:mm:ss'), $msg)) }

# The whole install in one bar (progress box at the top of the window);
# Invoke-Tool shows the current program's progress under it.
$TotalSteps = 11
function Step($n,$title){
    Log ""; Log ("==== STEP $n of $TotalSteps : $title ====") "Cyan"
    $ProgressPreference = "Continue"
    Write-Progress -Id 1 -Activity "Installing Amadeus" -Status ("Step $n of $TotalSteps : $title") `
        -PercentComplete ([int]([math]::Min(100, [math]::Max(0, $n) * 100 / $TotalSteps)))
}

# Windows command-line quoting for one argument list (the rules every
# Microsoft C program uses to split its command line back into arguments).
function Join-ProcessArgs([string[]]$list){
    $parts = foreach ($a in $list) {
        $a = [string]$a
        if ($a -ne "" -and $a -notmatch '[\s"]') { $a; continue }
        $sb = New-Object Text.StringBuilder
        [void]$sb.Append('"'); $bs = 0
        foreach ($ch in $a.ToCharArray()) {
            if ($ch -eq [char]'\') { $bs++; continue }
            if ($ch -eq [char]'"') { [void]$sb.Append(('\' * ($bs * 2 + 1)) + '"'); $bs = 0; continue }
            if ($bs) { [void]$sb.Append('\' * $bs); $bs = 0 }
            [void]$sb.Append($ch)
        }
        [void]$sb.Append(('\' * ($bs * 2)) + '"')
        $sb.ToString()
    }
    return ($parts -join ' ')
}

# Run one outside program (pip, conda, git, winget, npm) the quiet way:
#  * EVERY line it prints goes into install_log.txt (and $ExtraLog), under a
#    ">>> label" heading, with its result code - so a tester's log always
#    shows WHY something failed, not only that it did;
#  * the screen gets one progress line instead of hundreds: pip's current
#    package + download percentage (its "raw" progress mode), git's own
#    percentages, else the label and the time so far;
#  * $LASTEXITCODE is the program's result code, as with a direct call, and
#    $script:LastToolTail holds its last 15 lines for error messages.
# Lines are read as they come (a carriage return counts as a line end, so
# git's in-place progress updates arrive live).
$script:LastToolTail = @()
$script:LastToolInstalled = 0
function Invoke-Tool {
    param([string]$Label, [string]$Exe, [string[]]$Arguments = @(), [string]$ExtraLog = "")
    $ProgressPreference = "Continue"
    $started = Get-Date
    $tail = New-Object Collections.Generic.Queue[string]
    $buffer = New-Object Collections.Generic.List[string]
    $buffer.Add(("{0}  >>> {1}: {2} {3}" -f (Get-Date -Format 'HH:mm:ss'), $Label, $Exe, (Join-ProcessArgs $Arguments)))
    $psi = New-Object Diagnostics.ProcessStartInfo
    $psi.FileName = $Exe
    $psi.Arguments = Join-ProcessArgs $Arguments
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.CreateNoWindow = $true
    $psi.StandardOutputEncoding = [Text.Encoding]::UTF8
    $psi.StandardErrorEncoding = [Text.Encoding]::UTF8
    $psi.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8"
    $psi.WorkingDirectory = (Get-Location).ProviderPath   # honours Push-Location
    try { $proc = [Diagnostics.Process]::Start($psi) }
    catch {
        $buffer.Add("      | could not start: " + $_.Exception.Message)
        Write-LogLines $buffer; Write-LogLines $buffer $ExtraLog
        $script:LastToolTail = @("could not start " + $Exe + ": " + $_.Exception.Message)
        $global:LASTEXITCODE = 9009
        return
    }
    $count = 0; $current = ""; $pct = -1; $installed = 0; $lastDraw = [datetime]::MinValue
    $handle = {
        param($line)
        $t = $line.TrimEnd()
        if ($t -eq "") { return }
        if ($t -match '^Progress (\d+) of (\d+)$') {          # pip --progress-bar raw
            if ([double]$Matches[2] -gt 0) { $pct = [int](100 * [double]$Matches[1] / [double]$Matches[2]) }
            return                                            # not worth a log line
        }
        if ($t -match '^\s*(?<what>[A-Za-z][A-Za-z ]{2,30}):\s+(?<p>\d{1,3})%') {   # git / git-lfs
            $pct = [int]$Matches.p; $current = $Matches.what.Trim()
            if ($pct -lt 100) { return }                      # log each phase once, when done
        }
        elseif ($t -match '^Collecting (\S+)') { $count++; $current = $Matches[1]; $pct = -1 }
        elseif ($t -match '^\s*Downloading (\S+) \((.+)\)') { $current = "downloading " + $Matches[1] + " (" + $Matches[2] + ")"; $pct = 0 }
        elseif ($t -match '^Installing collected packages: (.+)$') { $current = "installing " + (($Matches[1] -split ',').Count) + " packages"; $pct = -1 }
        elseif ($t -match '^Successfully installed (.+)$') { $installed = (($Matches[1].Trim()) -split '\s+').Count }
        $buffer.Add("      | " + $t)
        $tail.Enqueue($t); while ($tail.Count -gt 15) { [void]$tail.Dequeue() }
    }
    $outTask = $proc.StandardOutput.ReadLineAsync(); $outDone = $false
    $errTask = $proc.StandardError.ReadLineAsync(); $errDone = $false
    while (-not ($outDone -and $errDone)) {
        $pending = @()
        if (-not $outDone) { $pending += $outTask }
        if (-not $errDone) { $pending += $errTask }
        [void][Threading.Tasks.Task]::WaitAny([Threading.Tasks.Task[]]$pending, 250)
        if (-not $outDone -and $outTask.IsCompleted) {
            $l = $outTask.Result
            if ($null -eq $l) { $outDone = $true } else { . $handle $l; $outTask = $proc.StandardOutput.ReadLineAsync() }
        }
        if (-not $errDone -and $errTask.IsCompleted) {
            $l = $errTask.Result
            if ($null -eq $l) { $errDone = $true } else { . $handle $l; $errTask = $proc.StandardError.ReadLineAsync() }
        }
        $now = Get-Date
        if (($now - $lastDraw).TotalMilliseconds -ge 250) {
            $lastDraw = $now
            $secs = [int]($now - $started).TotalSeconds
            $elapsed = if ($secs -ge 60) { "{0} min {1} s" -f [int][math]::Floor($secs / 60), ($secs % 60) } else { "$secs s" }
            if ($count -gt 0) { $status = "Package " + $count + ": " + $current }
            elseif ($current) { $status = $current }
            else { $status = "working..." }
            $status = $status + "   (" + $elapsed + ")"
            if ($pct -ge 0) { Write-Progress -Id 2 -ParentId 1 -Activity $Label -Status $status -PercentComplete ([math]::Min(100, $pct)) }
            else { Write-Progress -Id 2 -ParentId 1 -Activity $Label -Status $status }
        }
        if ($buffer.Count -ge 50) { Write-LogLines $buffer; Write-LogLines $buffer $ExtraLog; $buffer.Clear() }
    }
    $proc.WaitForExit()
    $code = $proc.ExitCode
    Write-Progress -Id 2 -ParentId 1 -Activity $Label -Completed
    $buffer.Add(("{0}  <<< {1}: exit {2} ({3} s)" -f (Get-Date -Format 'HH:mm:ss'), $Label, $code, [int]((Get-Date) - $started).TotalSeconds))
    Write-LogLines $buffer; Write-LogLines $buffer $ExtraLog
    $script:LastToolTail = $tail.ToArray()
    $script:LastToolInstalled = $installed
    $global:LASTEXITCODE = $code
}

# The last lines a failed program printed, for an error message.
function Get-ToolTail { return ($script:LastToolTail -join "`n") }

# Full path of a program on PATH (Invoke-Tool starts programs directly).
function Resolve-Exe($name){
    $c = Get-Command $name -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($c) { return $c.Source }
    return $name
}
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
# v2.1 (2026-10): the project's published history was rewritten once (to
# take personal details out of old commits), so an existing install can no
# longer fast-forward. Move it onto the new history ONLY when the copy holds
# nothing of the user's own: every commit it has that the new branch lacks
# was published by this project, and the two still share the original
# project's history. The personal settings files (pattern $personalRx) keep
# the user's version. Returns $true when moved. Run inside the project folder.
function Move-ToRewrittenHistory($target, $personalRx){
    & git merge-base HEAD $target 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) { return $false }
    $authors = & git log --format=%an "$target..HEAD" 2>$null
    $own = $authors | Where-Object { $_ -notin @("cmh95209", "Amadeus Local Build") }
    if ($own) { return $false }
    $saved = @{}
    foreach ($f in (& git diff --name-only HEAD 2>$null | Where-Object { $_ -match $personalRx })) {
        $saved[$f] = [IO.File]::ReadAllBytes((Join-Path (Get-Location) $f))
        & git checkout -- $f 2>$null | Out-Null
    }
    & git reset --keep $target 2>$null | Out-Null
    $ok = ($LASTEXITCODE -eq 0)
    foreach ($f in $saved.Keys) { [IO.File]::WriteAllBytes((Join-Path (Get-Location) $f), $saved[$f]) }
    return $ok
}

function Ensure-Clone($url, $dest, $branch, $markers){
    $leaf = Split-Path $dest -Leaf
    if (Test-Complete $dest $markers) { Log ("  Already have " + $leaf + " - skipping download.") "Yellow"; return }
    if (Test-Path $dest) {
        Log ("  Found an incomplete " + $leaf + " folder from a previous attempt - removing it.") "Yellow"
        Remove-Item -Recurse -Force $dest -ErrorAction SilentlyContinue
        if (Test-Path $dest) { throw ("Could not remove the old " + $leaf + " folder. Close any program using it and run the installer again.") }
    }
    $gitArgs = @("clone", "--progress")
    if ($branch) { $gitArgs += @("-b", $branch) }
    Invoke-Tool ("Downloading " + $leaf) (Resolve-Exe "git") ($gitArgs + @($url, $dest))
    if ($LASTEXITCODE -ne 0) { throw ("Could not download " + $leaf + ". Check your internet connection, then run the installer again.`n" + (Get-ToolTail)) }
    if (-not (Test-Complete $dest $markers)) { throw ("Downloaded " + $leaf + " but it looks incomplete. Run the installer again to retry.") }
    Log ("  Downloaded " + $leaf + ".") "Green"
}

# Put the voice engine copy on the tested version ($GptSovitsCommit) - a
# fresh download AND an existing install made by an older installer (which
# took whatever upstream had published that day). The switch discards the
# copy's changed files: this project's voice patches (re-applied right after,
# in Step 4) and the engine's own settings file, which goes back to the
# tested original (a newer engine adds entries the tested one does not
# know). Downloaded model files are not tracked by git and stay untouched.
function Set-GptSovitsVersion($dest, $commit){
    $git = Resolve-Exe "git"
    $head = (& $git -C $dest rev-parse HEAD 2>$null | Out-String).Trim()
    if ($head -eq $commit) { Log "  Voice engine is on the tested version." "Green"; return }
    Log ("  Switching the voice engine to the tested version (" + $commit.Substring(0, 7) + ")...") "Yellow"
    & $git -C $dest cat-file -e ($commit + "^{commit}") 2>$null
    if ($LASTEXITCODE -ne 0) {
        Invoke-Tool "Fetching the tested voice engine version" $git @("-C", $dest, "fetch", "--progress", "origin", $commit)
        if ($LASTEXITCODE -ne 0) { throw ("Could not download the tested voice engine version. Check your internet connection, then run the installer again.`n" + (Get-ToolTail)) }
    }
    Invoke-Tool "Switching the voice engine version" $git @("-C", $dest, "checkout", "--force", "--quiet", $commit)
    $now = (& $git -C $dest rev-parse HEAD 2>$null | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or $now -ne $commit) { throw ("Could not switch the voice engine to its tested version.`n" + (Get-ToolTail)) }
    Log ("  Voice engine switched to the tested version (was " + $(if ($head) { $head.Substring(0, [Math]::Min(7, $head.Length)) } else { "unknown" }) + ").") "Green"
}

# Create a conda environment only if it does not already exist (safe to re-run).
function Ensure-Env($name, $py="3.10"){
    $names = & $conda env list | ForEach-Object { ($_ -split '\s+')[0] }
    if ($names -contains $name) { Log ("  Python environment '" + $name + "' already exists.") "Yellow"; return }
    Invoke-Tool ("Creating the Python environment '" + $name + "'") $conda @("create", "-n", $name, ("python=" + $py), "-y")
    if ($LASTEXITCODE -ne 0) { throw ("Could not create the '" + $name + "' Python environment.`n" + (Get-ToolTail)) }
    Log ("  Python environment '" + $name + "' created.") "Green"
}

# Locate a toolbox's own Python program (the program that actually runs the
# jobs). conda keeps environments in <miniconda folder>\envs\<name>, and
# the installer's own `conda create` always lands them there - on a fresh
# PC included - so this is found everywhere. It returns $null only if a
# machine keeps its toolboxes somewhere unusual; callers then fall back to
# the traditional `conda run` helper route, which is still correct (just
# the slower door that could freeze on some Windows machines).
function Env-Python($name){
    $p = Join-Path $condaHome "envs\$name\python.exe"
    if (Test-Path $p) { return $p }
    return $null
}

# Run pip inside one of the toolboxes. Prefers the toolbox's own Python
# (no middleman); uses the helper route only as a fallback. $pipArgs is
# everything pip should receive, e.g. @("install", "Flask") or
# @("uninstall", "-y", "torch"). Runs through Invoke-Tool (full output in the
# log + $extraLog, one progress line on screen). Install/download commands get
# pip's "raw" progress mode (download percentages the progress line can
# show); a pip too old to know it is simply asked again without it.
function Invoke-PipInEnv($name, $pipArgs, $label = "pip", $extraLog = ""){
    $pipArgs = @($pipArgs)
    $withRaw = $pipArgs
    if ($pipArgs.Count -gt 0 -and $pipArgs[0] -in @("install", "download")) {
        $withRaw = @($pipArgs[0], "--progress-bar", "raw") + @($pipArgs | Select-Object -Skip 1)
    }
    $py = Env-Python $name
    if ($py) { $exe = $py; $pre = @("-m", "pip") } else { $exe = $conda; $pre = @("run", "-n", $name, "pip") }
    Invoke-Tool $label $exe ($pre + $withRaw) $extraLog
    if ($LASTEXITCODE -ne 0 -and $withRaw.Count -ne $pipArgs.Count -and
        (Get-ToolTail) -match "no such option|invalid choice|unrecognized arguments") {
        Invoke-Tool ($label + " (again, plain progress)") $exe ($pre + $pipArgs) $extraLog
    }
}

# ============================================================================
#  Built-in local model: hardware check (read-only - it only LOOKS at the PC)
# ============================================================================
# Which Gemma 4 model and settings this PC gets. This is an exact copy of
# scripts/local_llm_tiers.json (the installer runs before the project is
# downloaded, so it carries its own); the project's tests keep them identical.
# >>> TIER TABLE (copy of scripts/local_llm_tiers.json)
$TierTableJson = @'
{
  "version": 2,
  "about": "Built-in local model: which Gemma 4 QAT model and settings each PC gets. scripts/install_windows.ps1 carries an exact copy (between its TIER TABLE markers) because it runs before the project is downloaded; tests/test_llm_tiers.py keeps the two identical.",
  "dedicated_card_min_gb": 2,
  "voice_on_cpu_min_ram_gb": 16,
  "disk_headroom_gb": 2,
  "models": {
    "E4B": {
      "label": "Gemma 4 E4B (QAT)",
      "repo": "unsloth/gemma-4-E4B-it-qat-GGUF",
      "revision": "8c5a9e4fd5482e2be20fe0bf013b4c262a8f4265",
      "file": "gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf",
      "bytes": 4215695776,
      "sha256": "df0fd4ee07072c607c29a0a1cb4f98918426cca12f45a2776bdd6ee6d09a4de3",
      "drafter": {"file": "mtp-gemma-4-E4B-it.gguf", "bytes": 59678016, "sha256": "423074e537504b4f9ec5eafed5c639fac82c96631626efccacdd3c4039b20605"}
    },
    "12B": {
      "label": "Gemma 4 12B (QAT)",
      "repo": "unsloth/gemma-4-12b-it-qat-GGUF",
      "revision": "980b060c40a8539ac159e0501a3e0f66a6365af3",
      "file": "gemma-4-12B-it-qat-UD-Q4_K_XL.gguf",
      "bytes": 6716356800,
      "sha256": "90fd44e29e0d7cffeb0fd00dc73cfdab9ed0b0e95306ecf7821ea634c940c370",
      "drafter": {"file": "mtp-gemma-4-12B-it.gguf", "bytes": 253708800, "sha256": "fcb35dea42c71333db904cee11baac525c9ef872818ee3753f6cb156f3c6f4f6"}
    },
    "26B-A4B": {
      "label": "Gemma 4 26B-A4B (QAT)",
      "repo": "unsloth/gemma-4-26B-A4B-it-qat-GGUF",
      "revision": "7b92b5b28818151e8669af2e45e88d6086f490dd",
      "file": "gemma-4-26B-A4B-it-qat-UD-Q4_K_XL.gguf",
      "bytes": 14249047104,
      "sha256": "a7c5bc715f5ff8e99a3e8901ce7d2b42b402c669bf24f7c5250747633d0f5891",
      "drafter": {"file": "mtp-gemma-4-26B-A4B-it.gguf", "bytes": 251939328, "sha256": "7272d97595f0d4c74bd7b623492b7dbdaafd8b7c72f329a8270ba4eca68f768a"}
    },
    "31B": {
      "label": "Gemma 4 31B (QAT)",
      "repo": "unsloth/gemma-4-31B-it-qat-GGUF",
      "revision": "43cc1aeb31adf47ec06a854507ce552cd9862e6f",
      "file": "gemma-4-31B-it-qat-UD-Q4_K_XL.gguf",
      "bytes": 17287670048,
      "sha256": "00b5a7c497f0c8934033088c10a7fa9a4c015e46ee6d89e9c6890650ba5d0e71",
      "drafter": {"file": "mtp-gemma-4-31B-it.gguf", "bytes": 279955968, "sha256": "3a5e99fd8d0b23afb1fccd1ee0c9ebd1f571d00399c2dae2292d217feeec0f6b"}
    }
  },
  "llama_cpp": {
    "release": "b11400",
    "builds": {
      "cuda-13.4": {
        "min_nvidia_driver": 580,
        "files": [
          {"name": "llama-b11400-bin-win-cuda-13.4-x64.zip", "bytes": 153112447, "sha256": "872175bf23819d3a72a2bfc54aa21d750f0e020a586d1a6eed79cae585d1d23a"},
          {"name": "cudart-llama-bin-win-cuda-13.4-x64.zip", "bytes": 423535356, "sha256": "738f8c251ac22b70c3ae6f83a10cf222725df0395246a2cf58f32bdb85fbe668"}
        ]
      },
      "cuda-12.4": {
        "min_nvidia_driver": 525,
        "files": [
          {"name": "llama-b11400-bin-win-cuda-12.4-x64.zip", "bytes": 264160281, "sha256": "b5c185b6b0a87c875a468ff920e20bfcdb82c0a9a7b219cd52c937d461f35d53"},
          {"name": "cudart-llama-bin-win-cuda-12.4-x64.zip", "bytes": 391443627, "sha256": "8c79a9b226de4b3cacfd1f83d24f962d0773be79f1e7b75c6af4ded7e32ae1d6"}
        ]
      },
      "vulkan": {
        "files": [
          {"name": "llama-b11400-bin-win-vulkan-x64.zip", "bytes": 33307867, "sha256": "77a5b9ad8f3ab34c9f600e60a04136574e5510ac7f93912e6600d616bbaf8ac1"}
        ]
      }
    }
  },
  "tiers": [
    {"id": "under-8",  "min_vram_gb": 2,  "model": "E4B",     "context": 16384, "kv_cache": "q8_0", "voice_device": "cpu",  "warning": true},
    {"id": "8-15",     "min_vram_gb": 8,  "model": "12B",     "context": 16384, "kv_cache": "q8_0", "voice_device": "cpu",  "warning": false},
    {"id": "16-23",    "min_vram_gb": 16, "model": "12B",     "context": 32768, "kv_cache": "f16",  "voice_device": "cuda", "warning": false},
    {"id": "24-31",    "min_vram_gb": 24, "model": "26B-A4B", "context": 32768, "kv_cache": "f16",  "voice_device": "cuda", "warning": false},
    {"id": "32-plus",  "min_vram_gb": 32, "model": "31B",     "context": 32768, "kv_cache": "f16",  "voice_device": "cuda", "warning": false}
  ]
}
'@
# <<< TIER TABLE
$TierTable = $TierTableJson | ConvertFrom-Json

# Nearest whole GB, halves rounded up (PowerShell's own rounding would turn
# 8.5 into 8). Cards report a little under their label - an RTX 5090 says
# 32607 MiB = 31.8 GB - so a plain comparison would drop every card a tier.
function Round-Gb([double]$gb){ return [int][math]::Floor($gb + 0.5) }

# The biggest separate graphics card, or $null. NVIDIA: nvidia-smi (exact
# memory + driver version). Others: Windows' own record of each display
# adapter in the registry - its 64-bit memory value, because the usual
# Windows figure (Win32_VideoController.AdapterRAM) stops at 4 GB. Built-in
# graphics report little or no memory of their own (they borrow normal
# memory) and so count as "no card".
function Get-GraphicsCard {
    $best = $null
    if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
        try {
            foreach ($line in (& nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader,nounits 2>$null)) {
                $p = $line -split ',\s*'
                if ($p.Count -ge 3 -and $p[1].Trim() -match '^\d+$') {
                    $gb = Round-Gb ([double]$p[1].Trim() / 1024)
                    if (-not $best -or $gb -gt $best.vram_gb) {
                        $best = [pscustomobject]@{ vendor = "nvidia"; name = $p[0].Trim(); vram_gb = $gb; driver = $p[2].Trim() }
                    }
                }
            }
        } catch {}
    }
    if ($best) { return $best }
    # The registry also keeps records of cards that were REMOVED (a PC can
    # still list a card it no longer has), so only adapters Windows reports as
    # present right now count. If Windows won't list them, all records count.
    $present = $null
    try { $present = @(Get-CimInstance Win32_VideoController -ErrorAction Stop | ForEach-Object { [string]$_.Name }) } catch {}
    $classKey = "HKLM:\SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
    foreach ($k in (Get-ChildItem $classKey -ErrorAction SilentlyContinue | Where-Object { $_.PSChildName -match '^\d{4}$' })) {
        $props = Get-ItemProperty $k.PSPath -ErrorAction SilentlyContinue
        if (-not $props) { continue }
        $name = [string]$props.DriverDesc
        if ($present -and ($present -notcontains $name)) { continue }
        $bytes = [double]0
        $q = $props.'HardwareInformation.qwMemorySize'
        if ($q) { $bytes = [double]$q }
        else {
            $m = $props.'HardwareInformation.MemorySize'
            if ($m -is [byte[]] -and $m.Length -ge 4) { $bytes = [double][BitConverter]::ToUInt32($m, 0) }
            elseif ($m) { $bytes = [double]$m }
        }
        $who = ($name + " " + [string]$props.ProviderName)
        # An NVIDIA card that nvidia-smi could not report has a missing or
        # broken driver: neither llama.cpp's CUDA build nor the voice
        # engine's CUDA PyTorch (installed only when nvidia-smi works) can
        # use it, so it is treated like any other card (Vulkan build).
        if     ($who -match 'NVIDIA')              { $vendor = "nvidia-unverified" }
        elseif ($who -match 'AMD|ATI|Radeon')      { $vendor = "amd" }
        elseif ($who -match 'Intel')               { $vendor = "intel" }
        else { continue }
        $gb = Round-Gb ($bytes / 1GB)
        if (-not $best -or $gb -gt $best.vram_gb) {
            $best = [pscustomobject]@{ vendor = $vendor; name = $name; vram_gb = $gb; driver = [string]$props.DriverVersion }
        }
    }
    if ($best -and $best.vram_gb -lt $TierTable.dedicated_card_min_gb) { return $null }
    return $best
}

# Normal memory (RAM) in whole GB, or -1 when Windows won't say.
function Get-RamGb {
    try {
        $sum = (Get-CimInstance Win32_PhysicalMemory -ErrorAction Stop | Measure-Object Capacity -Sum).Sum
        if ($sum -gt 0) { return (Round-Gb ($sum / 1GB)) }
    } catch {}
    try { return (Round-Gb ((Get-CimInstance Win32_ComputerSystem -ErrorAction Stop).TotalPhysicalMemory / 1GB)) } catch {}
    return -1
}

# Free space (GB, one decimal) on the drive that holds $path.
function Get-FreeDiskGb($path){
    try {
        $root = [IO.Path]::GetPathRoot([IO.Path]::GetFullPath($path))
        return [math]::Floor((New-Object IO.DriveInfo $root).AvailableFreeSpace / 1GB * 10) / 10
    } catch { return -1 }
}

# The decision for this hardware - the same rules as scripts/llm_tiers.py
# (choose); the project's tests run both on the same inputs.
function Select-LlamaBuild($vendor, $driver){
    # The newest CUDA build the NVIDIA driver can run; else Vulkan (AMD,
    # Intel, an old or unreadable NVIDIA driver). Same as pick_build.
    if ($vendor -eq "nvidia") {
        $major = 0
        $head = ([string]$driver).Trim().Split(".")[0]
        if ($head -match '^\d+$') { $major = [int]$head }
        $cuda = @()
        foreach ($p in $TierTable.llama_cpp.builds.PSObject.Properties) {
            if ($null -ne $p.Value.min_nvidia_driver) { $cuda += [pscustomobject]@{ name = $p.Name; need = [int]$p.Value.min_nvidia_driver } }
        }
        foreach ($b in ($cuda | Sort-Object need -Descending)) { if ($major -ge $b.need) { return $b.name } }
    }
    return "vulkan"
}

# Everything option A downloads: model + drafter + llama.cpp files (bytes).
function Get-LlmDownloadBytes($modelKey, $build){
    $m = $TierTable.models.$modelKey
    $total = [double]$m.bytes + [double]$m.drafter.bytes
    foreach ($f in $TierTable.llama_cpp.builds.$build.files) { $total += [double]$f.bytes }
    return $total
}

function Select-LlmTier($vendor, [int]$vramGb, [int]$ramGb, $driver = ""){
    if (-not $vendor -or $vramGb -lt $TierTable.dedicated_card_min_gb) {
        return [pscustomobject]@{ offered = $false; reason = "no_card" }
    }
    $tier = $null
    foreach ($t in ($TierTable.tiers | Sort-Object min_vram_gb)) { if ($vramGb -ge $t.min_vram_gb) { $tier = $t } }
    # Her voice engine can use only NVIDIA cards; on any other card it runs
    # on the processor, which needs plenty of normal memory.
    $voice = if ($vendor -eq "nvidia") { $tier.voice_device } else { "cpu" }
    if ($voice -eq "cpu" -and $ramGb -ge 0 -and $ramGb -lt $TierTable.voice_on_cpu_min_ram_gb) {
        return [pscustomobject]@{ offered = $false; reason = "low_ram" }
    }
    return [pscustomobject]@{
        offered = $true; reason = ""; tier = $tier.id; model = $tier.model
        context = [int]$tier.context; kv_cache = $tier.kv_cache; voice_device = $voice
        warning = [bool]$tier.warning; build = (Select-LlamaBuild $vendor $driver)
    }
}

# Look at the PC (honouring the -Simulate* test options) and decide.
function Invoke-HardwareCheck($dir){
    $card = Get-GraphicsCard
    $ram  = Get-RamGb
    $simulated = $false
    if ($SimulateVramGB -ge 0) {
        $simulated = $true
        if ($SimulateVramGB -lt $TierTable.dedicated_card_min_gb) { $card = $null }
        elseif ($card) { $card = [pscustomobject]@{ vendor = $card.vendor; name = $card.name + " (pretending " + $SimulateVramGB + " GB)"; vram_gb = $SimulateVramGB; driver = $card.driver } }
        else { $card = [pscustomobject]@{ vendor = "nvidia"; name = "(pretend NVIDIA card, " + $SimulateVramGB + " GB)"; vram_gb = $SimulateVramGB; driver = "" } }
    }
    if ($SimulateRamGB -ge 0) { $simulated = $true; $ram = $SimulateRamGB }
    $vendor = $null; $vram = 0; $driver = ""
    if ($card) { $vendor = $card.vendor; $vram = [int]$card.vram_gb; $driver = $card.driver }
    $choice = Select-LlmTier $vendor $vram $ram $driver
    $model = $null; $dlGb = 0
    if ($choice.offered) {
        $model = $TierTable.models.($choice.model)
        $dlGb = [math]::Ceiling((Get-LlmDownloadBytes $choice.model $choice.build) / 1GB * 10) / 10
    }
    return [pscustomobject]@{
        card = $card; ram_gb = $ram; disk_free_gb = (Get-FreeDiskGb $dir)
        simulated = $simulated; choice = $choice; model = $model; download_gb = $dlGb
    }
}

# Plain-language summary lines of a hardware check (for the screen + log).
function Format-HardwareCheck($hw){
    $lines = @()
    if ($hw.card) { $lines += ("  Graphics card : " + $hw.card.name + " - " + $hw.card.vram_gb + " GB graphics memory") }
    else          { $lines += "  Graphics card : none found (only built-in graphics)" }
    $lines += ("  Normal memory : " + $(if ($hw.ram_gb -ge 0) { [string]$hw.ram_gb + " GB" } else { "unknown" }))
    $lines += ("  Free disk     : " + $hw.disk_free_gb + " GB")
    if ($hw.choice.offered) {
        $lines += ("  Local model   : " + $hw.model.label + " (" + $hw.download_gb + " GB download, with its helper program)")
        $lines += ("  Settings      : " + $hw.choice.context + " context, " + $hw.choice.kv_cache + " memory, voice on the " + $(if ($hw.choice.voice_device -eq "cpu") { "processor" } else { "graphics card" }) + ", " + $hw.choice.build + " build")
        if ($hw.choice.warning) { $lines += "  (Not recommended on this PC - under 8 GB of graphics memory.)" }
    } elseif ($hw.choice.reason -eq "no_card") {
        $lines += "  Local model   : not offered - no separate graphics card (option B only)"
    } else {
        $lines += "  Local model   : not offered - her voice would run on the processor, which needs 16 GB of normal memory (option B only)"
    }
    if ($hw.simulated) { $lines += "  (TEST: pretend hardware values in use)" }
    return $lines
}

# ============================================================================
#  Built-in local model: the A/B question, the downloads, the settings file
# ============================================================================
# The model and its helper program live NEXT TO the project folder, not in
# it: a broken project folder is deleted and re-downloaded (Ensure-Clone),
# which must never take a 4-17 GB model with it.
$ModelsDir     = Join-Path $InstallDir "models"
$LlmChoiceFile = Join-Path $ModelsDir "local_llm_choice.json"
$DownloadRetryDelaySeconds = 15

# Ask until the answer is one of $valid (upper-cased). Gives up after many
# empty/invalid answers so a closed input can never loop forever.
function Read-Answer($prompt, $valid){
    for ($i = 0; $i -lt 20; $i++) {
        $a = Read-Host $prompt
        if ($null -eq $a) { $a = "" }
        $a = $a.Trim().ToUpper()
        if ($valid -contains $a) { Log ("  (answer: " + $a + ")"); return $a }
        Log ("  Please type one of: " + ($valid -join ", ")) "Yellow"
    }
    Die "No answer was given."
}

function Write-Utf8File($path, $text){
    New-Item -ItemType Directory -Force -Path (Split-Path $path -Parent) | Out-Null
    [IO.File]::WriteAllText($path, $text, (New-Object Text.UTF8Encoding $false))
}

function New-CustomChoice { return [pscustomobject]@{ mode = "custom"; chosen = (Get-Date -Format s) } }

# Option A on this PC: show what was found, then confirm (or fall back to B).
function Confirm-LocalModel($hw){
    Log ""
    Format-HardwareCheck $hw | ForEach-Object { Log $_ }
    Log ""
    if (-not $hw.choice.offered) {
        if ($hw.choice.reason -eq "no_card") {
            Log "  A local model needs a separate graphics card. On this PC she would think far too slowly, so Amadeus will use option B: you connect a model yourself (a cloud service such as OpenRouter, or your own model server)." "Yellow"
        } else {
            Log ("  On this PC her voice has to run on the processor, which needs at least " + $TierTable.voice_on_cpu_min_ram_gb + " GB of normal memory (this PC has " + $hw.ram_gb + " GB). So Amadeus will use option B: you connect a model yourself (a cloud service such as OpenRouter, or your own model server).") "Yellow"
        }
        Read-Host "Press Enter to continue with option B" | Out-Null
        return (New-CustomChoice)
    }
    if ($hw.choice.warning) {
        Log "  WARNING:" "Yellow"
        Log "  Using Amadeus with a local model is not recommended on PCs with less than 8 GB of graphics memory. Her responses will be slower and less intelligent, and features like web search might be buggy. For a better experience, choose option B and connect a cloud model (e.g. OpenRouter)." "Yellow"
        if ((Read-Answer "Type A to use the local model anyway, or B to switch to option B" @("A","B")) -eq "B") { return (New-CustomChoice) }
    }
    $need = $hw.download_gb + $TierTable.disk_headroom_gb
    if ($hw.disk_free_gb -ge 0 -and $hw.disk_free_gb -lt $need) {
        Log ("  Not enough free disk space for the local model: it needs about " + $need + " GB, this drive has " + $hw.disk_free_gb + " GB free.") "Yellow"
        if ((Read-Answer "Type B to continue with option B, or Q to stop here (free up space, then run the installer again)" @("B","Q")) -eq "Q") {
            Log "Stopped - nothing was changed. Free up some space, then run the installer again." "Yellow"
            Read-Host "Press Enter to close this window" | Out-Null
            exit 0
        }
        return (New-CustomChoice)
    }
    $q = "Download " + $hw.model.label + " (" + $hw.download_gb + " GB) near the end of the install? Type Y (yes) or B (do it myself instead)"
    if ((Read-Answer $q @("Y","B")) -eq "B") { return (New-CustomChoice) }
    $c = $hw.choice
    return [pscustomobject]@{
        mode = "builtin"; model = $c.model; tier = $c.tier; build = $c.build
        context = $c.context; kv_cache = $c.kv_cache; voice_device = $c.voice_device
        simulated = [bool]$hw.simulated; chosen = (Get-Date -Format s)
    }
}

# STEP 0: the very first question, before anything is downloaded. The
# answer is saved next to the project, so a re-run only asks "keep it?".
function Invoke-LlmChoice($hw){
    Step 0 "How should Amadeus think?"
    if (Test-Path $LlmChoiceFile) {
        $prev = $null
        try { $prev = Get-Content $LlmChoiceFile -Raw | ConvertFrom-Json } catch {}
        if ($prev -and ($prev.mode -eq "custom" -or ($prev.mode -eq "builtin" -and $TierTable.models.($prev.model)))) {
            if ($prev.mode -eq "builtin") { $desc = "A) her built-in local model - " + $TierTable.models.($prev.model).label }
            else { $desc = "B) you connect a model yourself" }
            Log ("  Last time you chose: " + $desc)
            if ((Read-Answer "Keep that choice? Type Y (yes) or N (choose again)" @("Y","N")) -eq "Y") { return $prev }
        }
    }
    Log "  How should Amadeus think?" "Cyan"
    Log "    A) Install and set up a local model automatically (private, runs on this PC, no account needed)"
    Log "    B) I'll do it myself (choose this if using a cloud model or your own local model)"
    if ((Read-Answer "Type A or B" @("A","B")) -eq "A") { $choice = Confirm-LocalModel $hw } else { $choice = New-CustomChoice }
    Write-Utf8File $LlmChoiceFile ($choice | ConvertTo-Json -Depth 4)
    if ($choice.mode -eq "builtin") { Log ("  Chosen: " + $TierTable.models.($choice.model).label + " - it downloads near the end of the install.") "Green" }
    else { Log "  Chosen: option B - you connect a model yourself in Settings after the install." "Green" }
    return $choice
}

# SHA-256 via .NET directly (no PowerShell module needed - Get-FileHash lives
# in a module that fails to load in some mixed PowerShell 5/7 setups).
function Get-Sha256($path){
    $stream = [IO.File]::OpenRead($path)
    try {
        $sha = [Security.Cryptography.SHA256]::Create()
        return ([BitConverter]::ToString($sha.ComputeHash($stream)) -replace '-', '').ToLower()
    } finally { $stream.Dispose() }
}

# Download one file and prove it is the official one (size + SHA-256). Safe
# to re-run: a finished, checked file is skipped (a small ".verified" note
# next to it saves re-checking 17 GB every time); a half-finished ".part"
# continues where it stopped; a damaged download is deleted. Uses Windows'
# own curl.exe (progress bar + resume); without it, PowerShell's downloader
# (no resume).
function Get-VerifiedFile($url, $dest, [double]$bytes, $sha256, $label){
    $marker = $dest + ".verified"
    $stamp  = $sha256 + " " + [string][int64]$bytes
    if ((Test-Path -LiteralPath $dest) -and (Test-Path -LiteralPath $marker) -and
        ((Get-Content -LiteralPath $marker -Raw).Trim() -eq $stamp) -and ((Get-Item -LiteralPath $dest).Length -eq $bytes)) {
        Log ("  Already have " + $label + " (checked) - skipping.") "Yellow"; return
    }
    New-Item -ItemType Directory -Force -Path (Split-Path $dest -Parent) | Out-Null
    $part = $dest + ".part"
    if (Test-Path -LiteralPath $dest) {
        # Present but not yet checked (e.g. an interrupted check): check it now.
        if ((Get-Item -LiteralPath $dest).Length -eq $bytes -and (Get-Sha256 $dest) -eq $sha256) {
            Set-Content -LiteralPath $marker -Value $stamp -NoNewline -Encoding ascii
            Log ("  Already have " + $label + " (checked) - skipping.") "Yellow"; return
        }
        Move-Item -LiteralPath $dest -Destination $part -Force
    }
    $gb = [math]::Round($bytes / 1GB, 2)
    Log ("  Downloading " + $label + " (" + $gb + " GB)...")
    Log ("  from: " + $url)
    $curl = Join-Path $env:SystemRoot "System32\curl.exe"
    for ($try = 1; $try -le 4; $try++) {
        if ((Test-Path -LiteralPath $part) -and (Get-Item -LiteralPath $part).Length -gt $bytes) { Remove-Item -LiteralPath $part -Force }
        if ((Test-Path -LiteralPath $part) -and (Get-Item -LiteralPath $part).Length -eq $bytes) { break }
        if (Test-Path $curl) {
            & $curl -L --fail --retry 5 --retry-delay 10 --connect-timeout 30 -C - --progress-bar -o $part $url
            $have = if (Test-Path -LiteralPath $part) { (Get-Item -LiteralPath $part).Length } else { 0 }
            Write-LogOnly ("  download attempt " + $try + " (" + $label + "): curl exit " + $LASTEXITCODE + ", " + $have + " of " + [int64]$bytes + " bytes")
        } else {
            try { Invoke-WebRequest $url -OutFile $part -UseBasicParsing -TimeoutSec 86400 } catch { Log ("  " + $_.Exception.Message) "Yellow" }
        }
        if ((Test-Path -LiteralPath $part) -and (Get-Item -LiteralPath $part).Length -eq $bytes) { break }
        if ($try -lt 4) { Log "  The download was interrupted - trying again shortly (it continues where it stopped)..." "Yellow"; Start-Sleep -Seconds $DownloadRetryDelaySeconds }
    }
    if (-not (Test-Path -LiteralPath $part) -or (Get-Item -LiteralPath $part).Length -ne $bytes) {
        throw ("Could not download " + $label + " right now. Check your internet connection, then run the installer again - it continues where it stopped.")
    }
    Log "  Checking the downloaded file (about a minute for the big ones)..."
    $got = Get-Sha256 $part
    Write-LogOnly ("  checksum " + $label + ": " + $got + $(if ($got -eq $sha256) { " (matches)" } else { " (EXPECTED " + $sha256 + ")" }))
    if ($got -ne $sha256) {
        Remove-Item -LiteralPath $part -Force
        throw ($label + " was downloaded but is damaged (its checksum does not match the official one), so it was deleted. Run the installer again to download it fresh.")
    }
    Move-Item -LiteralPath $part -Destination $dest -Force
    Set-Content -LiteralPath $marker -Value $stamp -NoNewline -Encoding ascii
    Log ("  " + $label + " downloaded and checked.") "Green"
}

# Unpack a zip into $dir (overwriting), with .NET's zip reader: much faster
# than Expand-Archive on Windows PowerShell, and needs no module. Entries
# that would land outside $dir are refused.
function Expand-ZipFile($zip, $dir){
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $root = [IO.Path]::GetFullPath($dir).TrimEnd('\') + '\'
    $archive = [IO.Compression.ZipFile]::OpenRead($zip)
    try {
        foreach ($e in $archive.Entries) {
            $target = [IO.Path]::GetFullPath((Join-Path $root $e.FullName))
            if (-not $target.StartsWith($root, [StringComparison]::OrdinalIgnoreCase)) { throw ("Unsafe path in " + $zip + ": " + $e.FullName) }
            if ($e.FullName.EndsWith('/') -or $e.FullName.EndsWith('\')) { New-Item -ItemType Directory -Force -Path $target | Out-Null; continue }
            New-Item -ItemType Directory -Force -Path (Split-Path $target -Parent) | Out-Null
            [IO.Compression.ZipFileExtensions]::ExtractToFile($e, $target, $true)
        }
    } finally { $archive.Dispose() }
}

# Download + unpack one llama.cpp build into models\llama.cpp\<release>-<build>\
# and prove its server program starts. Returns the path to llama-server.exe.
function Install-LlamaBuild($build){
    $rel  = $TierTable.llama_cpp.release
    $dir  = Join-Path $ModelsDir ("llama.cpp\" + $rel + "-" + $build)
    $done = Join-Path $dir "install_complete.txt"
    if (Test-Path -LiteralPath $done) {
        $exe = (Get-Content -LiteralPath $done -Raw).Trim()
        if ($exe -and (Test-Path -LiteralPath $exe)) { Log ("  Already have llama.cpp " + $rel + " (" + $build + ") - skipping.") "Yellow"; return $exe }
    }
    $dl = Join-Path $ModelsDir "downloads"
    foreach ($f in $TierTable.llama_cpp.builds.$build.files) {
        Get-VerifiedFile ("https://github.com/ggml-org/llama.cpp/releases/download/" + $rel + "/" + $f.name) (Join-Path $dl $f.name) $f.bytes $f.sha256 ("llama.cpp " + $f.name)
    }
    if (Test-Path -LiteralPath $dir) { Remove-Item -LiteralPath $dir -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    Log ("  Unpacking llama.cpp (" + $build + ")...")
    foreach ($f in $TierTable.llama_cpp.builds.$build.files) { Expand-ZipFile (Join-Path $dl $f.name) $dir }
    $found = Get-ChildItem -LiteralPath $dir -Recurse -Filter "llama-server.exe" | Select-Object -First 1
    if (-not $found) { throw ("The llama.cpp download (" + $build + ") has no llama-server.exe.") }
    # Smoke test: the program must start (catches a missing runtime file or
    # a driver that cannot load the CUDA build).
    $ver = & $found.FullName --version 2>&1 | Out-String
    Write-LogOnly ("  llama-server --version (exit " + $LASTEXITCODE + "): " + $ver.Trim())
    if ($LASTEXITCODE -ne 0) { throw ("llama.cpp (" + $build + ") would not start on this PC: " + $ver.Trim()) }
    Set-Content -LiteralPath $done -Value $found.FullName -NoNewline -Encoding utf8
    foreach ($f in $TierTable.llama_cpp.builds.$build.files) {
        Remove-Item -LiteralPath (Join-Path $dl $f.name), (Join-Path $dl ($f.name + ".verified")) -Force -ErrorAction SilentlyContinue
    }
    Log ("  llama.cpp " + $rel + " (" + $build + ") ready.") "Green"
    return $found.FullName
}

function Get-HfUrl($m, $file){ return ("https://huggingface.co/" + $m.repo + "/resolve/" + $m.revision + "/" + $file + "?download=true") }

# The app's built-in-model settings: backend\data\local_llm.json. Written
# even when a download failed (model_ok/server_ok false), so the app knows
# the model is missing instead of silently falling back to something else.
# Option B on an install that had A before: kept, but switched to "custom".
function Write-LocalLlmConfig($proj, $choice, $exe, $serverOk, $modelPath, $modelOk, $draftPath, $draftOk){
    $cfg = Join-Path $proj "backend\data\local_llm.json"
    if ($choice.mode -ne "builtin") {
        if (Test-Path $cfg) {
            try { $old = Get-Content $cfg -Raw | ConvertFrom-Json; $old.mode = "custom"; Write-Utf8File $cfg ($old | ConvertTo-Json -Depth 4) } catch {}
        }
        return
    }
    $m = $TierTable.models.($choice.model)
    $data = [ordered]@{
        mode = "builtin"; model = $choice.model; model_label = $m.label
        model_file = $modelPath; model_ok = [bool]$modelOk
        drafter_file = $draftPath; drafter_ok = [bool]$draftOk; use_drafter = $false
        server_exe = $exe; server_ok = [bool]$serverOk
        llama_release = $TierTable.llama_cpp.release; build = $choice.build
        tier = $choice.tier; context = [int]$choice.context; kv_cache = $choice.kv_cache
        voice_device = $choice.voice_device; gpu_layers = 999; port = 8091
        simulated = [bool]$choice.simulated; written = (Get-Date -Format s); installer = $InstallerVersion
    }
    Write-Utf8File $cfg ($data | ConvertTo-Json -Depth 4)
    Log ("  Built-in model settings saved: " + $cfg)
}

# The last install step for option A: helper program, model, drafter. A
# failed download never stops the install - the summary says what to do.
# Returns $true when everything she needs is in place.
function Install-BuiltinLlm($proj, $choice){
    $m = $TierTable.models.($choice.model)
    $modelPath = Join-Path $ModelsDir $m.file
    $draftPath = Join-Path $ModelsDir $m.drafter.file
    $exe = ""; $serverOk = $false; $modelOk = $false; $draftOk = $false
    if ($SkipLlmDownload) {
        Log "  (TEST: -SkipLlmDownload - nothing is downloaded; the settings file is written as 'missing'.)" "Magenta"
    } else {
        try { $exe = Install-LlamaBuild $choice.build; $serverOk = $true }
        catch {
            Log ("  " + $_.Exception.Message) "Yellow"
            if ($choice.build -ne "vulkan") {
                Log "  Trying the general (Vulkan) version of llama.cpp instead..." "Yellow"
                try { $exe = Install-LlamaBuild "vulkan"; $serverOk = $true; $choice.build = "vulkan" } catch { Log ("  " + $_.Exception.Message) "Red" }
            }
        }
        try { Get-VerifiedFile (Get-HfUrl $m $m.file) $modelPath $m.bytes $m.sha256 $m.label; $modelOk = $true }
        catch { Log ("  " + $_.Exception.Message) "Red" }
        try { Get-VerifiedFile (Get-HfUrl $m $m.drafter.file) $draftPath $m.drafter.bytes $m.drafter.sha256 ($m.label + " speed-up file"); $draftOk = $true }
        catch { Log ("  (optional) " + $_.Exception.Message) "Yellow" }
    }
    Write-LocalLlmConfig $proj $choice $exe $serverOk $modelPath $modelOk $draftPath $draftOk
    return ($serverOk -and $modelOk)
}

# The last question: start Amadeus right away? The installer runs as
# administrator, and a program it starts directly would run as administrator
# too (and so would the browser she opens). Windows Explorer starts her
# instead - as the normal user, exactly like a double-click on the file.
function Offer-StartAmadeus($proj){
    $ProgressPreference = "Continue"
    Write-Progress -Id 1 -Activity "Installing Amadeus" -Completed
    $bat = Join-Path $proj "start_windows.bat"
    if (-not (Test-Path $bat)) { Read-Host "Press Enter to close this window" | Out-Null; return }
    Log ""
    if ((Read-Answer "Start Amadeus now? Type Y (yes) or N (no, I'll start it later)" @("Y","N")) -eq "Y") {
        try {
            Start-Process -FilePath (Join-Path $env:SystemRoot "explorer.exe") -ArgumentList ('"' + $bat + '"')
            Log "  Amadeus is starting in her own window (the first start takes a little longer). You can close this one." "Green"
        } catch {
            Log ("  Could not start her from here (" + $_.Exception.Message + "). Double-click start_windows.bat instead.") "Yellow"
        }
    } else {
        Log ("  To start her later, double-click: " + $bat)
    }
    Read-Host "Press Enter to close this window" | Out-Null
}

if ($HardwareCheckOnly) {
    $hwDir = if ($InstallDir) { $InstallDir } else { Join-Path $env:USERPROFILE "Amadeus" }
    $hw = Invoke-HardwareCheck $hwDir
    if ($Json) { $hw | ConvertTo-Json -Depth 5 -Compress }
    else { Write-Host "Amadeus hardware check (nothing is installed):" -ForegroundColor Cyan; Format-HardwareCheck $hw | ForEach-Object { Write-Host $_ } }
    exit 0
}

New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
# UTF-8 with a byte-order mark, so even old Notepad shows Japanese text right.
[IO.File]::WriteAllText($LogPath, ("Amadeus install (installer " + $InstallerVersion + ") started " + (Get-Date) + "`r`n"), (New-Object Text.UTF8Encoding $true))
if ($LlmScreensOnly) { $SkipLlmDownload = $true }

try {

    Log "============================================================" "Green"
    Log ("  AMADEUS INSTALLER " + $InstallerVersion + "  (safe to re-run - it resumes where it stopped)") "Green"
    Log "============================================================" "Green"
    Log "It downloads several gigabytes, so give it time. Keep this window open." "Yellow"
    if ($Source -or $Branch -ne "main" -or $EnvSuffix -or $SimulateVramGB -ge 0 -or $SimulateRamGB -ge 0 -or $SkipLlmDownload) {
        Log "TEST OPTIONS in use:" "Magenta"
        if ($Source)              { Log ("  code from this PC : " + $Source) "Magenta" }
        if ($Branch -ne "main")   { Log ("  branch            : " + $Branch) "Magenta" }
        if ($EnvSuffix)           { Log ("  environment suffix: " + $EnvSuffix) "Magenta" }
        if ($SimulateVramGB -ge 0){ Log ("  pretend graphics memory: " + $SimulateVramGB + " GB") "Magenta" }
        if ($SimulateRamGB -ge 0) { Log ("  pretend normal memory: " + $SimulateRamGB + " GB") "Magenta" }
        if ($SkipLlmDownload)     { Log "  no local-model downloads (screens + settings file only)" "Magenta" }
    }
    if ($Source -and -not ($ForkUrl -and (Test-Path (Join-Path $ForkUrl ".git")))) {
        Die ("-Source must be a git project folder on this PC (with a .git folder inside): " + $Source)
    }
    if ($EnvSuffix -and $EnvSuffix -notmatch '^-?[A-Za-z0-9_]+$') {
        Die ("-EnvSuffix may contain only letters, digits and _ (got: " + $EnvSuffix + ")")
    }
    # The very first question, before anything is downloaded.
    $hw = Invoke-HardwareCheck $InstallDir
    $llmChoice = Invoke-LlmChoice $hw
    if ($LlmScreensOnly) {
        $proj = Join-Path $InstallDir "Amadeus-Project"
        Install-BuiltinLlm $proj $llmChoice | Out-Null
        Log "TEST: -LlmScreensOnly - stopping here (nothing else was installed)." "Magenta"
        exit 0
    }
    Log ""
    Log "Everything else installs now. It downloads several gigabytes, so give it time." "Yellow"
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
        Invoke-Tool ("Installing " + $name) (Resolve-Exe "winget") @("install", "-e", "--id", $id, "--silent", "--accept-source-agreements", "--accept-package-agreements")
        if ($LASTEXITCODE -eq 0) { Log ("  Installed " + $name + ".") "Green" }
        else { Log ("  WARNING: " + $name + " install returned code " + $LASTEXITCODE + ". We will double-check below.") "Yellow" }
    }

    if (Get-Command git -ErrorAction SilentlyContinue) { Log "  Git already present." "Green" }
    else { Install-Pkg "Git" "Git.Git"; Refresh-Path }

    $condaHome = Join-Path $env:USERPROFILE "miniconda3"
    function Find-CondaHome {
        # The first location that actually contains a working conda, or $null.
        $spots = @((Join-Path $env:USERPROFILE "miniconda3"),
                   (Join-Path $env:LOCALAPPDATA "Continuum\miniconda3"),
                   "C:\ProgramData\miniconda3")
        foreach ($a in $spots) { if (Test-Path (Join-Path $a "Scripts\conda.exe")) { return $a } }
        return $null
    }
    if ($found = Find-CondaHome) { Log "  Miniconda already present." "Green"; $condaHome = $found }
    else {
        # Anaconda renamed the winget package in 2026: current catalogs know
        # "Anaconda.Miniconda3", older ones only "ContinuumAnalytics.Miniconda3"
        # (removed from the community source). Try the new name, then the old.
        Install-Pkg "Miniconda" "Anaconda.Miniconda3"
        if (-not (Find-CondaHome)) { Install-Pkg "Miniconda" "ContinuumAnalytics.Miniconda3" }
        if (-not (Find-CondaHome)) {
            # winget could not deliver Miniconda on this machine. Its package
            # lookup is unreliable on some fresh systems (e.g. Windows
            # Sandbox), so fall back to Anaconda's own official download:
            # the permanent "-latest" file, always the current version, a
            # link that never disappears. Same program, fully automatic.
            Log "  winget could not install Miniconda here - falling back to Anaconda's official download (the same program, just a different door)." "Yellow"
            $mcUrl = "https://repo.anaconda.com/miniconda/Miniconda3-latest-Windows-x86_64.exe"
            $mcExe = Join-Path $env:TEMP "Miniconda3-latest-Windows-x86_64.exe"
            try {
                Log "  Downloading Miniconda from Anaconda (about 125 MB, can take a few minutes)..."
                Log ("  from: " + $mcUrl)
                Invoke-WebRequest $mcUrl -OutFile $mcExe -UseBasicParsing -TimeoutSec 3600
                Start-Process -Wait $mcExe -ArgumentList ("/S /D=`"" + $condaHome + "`"")
            } catch {
                Log ("  Miniconda download/install failed: " + $_.Exception.Message) "Red"
            } finally {
                Remove-Item $mcExe -Force -ErrorAction SilentlyContinue
            }
        }
        if (-not ($condaHome = Find-CondaHome)) { Die "Could not find Miniconda after installing it. Look at this window and the log." }
    }
    $conda = Join-Path $condaHome "Scripts\conda.exe"
    Log ("  Using conda: " + $conda)
    # Record acceptance of any channel Terms of Service, in case this
    # conda is new enough (Miniconda 25.1+) to require them. No-op on
    # older conda, which has no 'tos' command.
    & $conda tos accept 2>$null | Out-Null
    Log "  Conda terms of service handled (required by newer conda before it will build environments unattended)."
    # Silence conda's "a newer version of conda exists" banners: they are
    # harmless (we always install the newest conda the package sources
    # offer) but confusing mid-install, and upgrading conda again halfway
    # through would add risk without any benefit. Best effort: older conda
    # simply ignores the unknown setting.
    & $conda config --set notify_outdated_conda false 2>$null | Out-Null

    if (Get-Command node -ErrorAction SilentlyContinue) { Log "  Node.js already present." "Green" }
    else { Install-Pkg "Node.js" "OpenJS.NodeJS.LTS"; Refresh-Path }

    if (Get-Command ffmpeg -ErrorAction SilentlyContinue) { Log "  FFmpeg already present." "Green" }
    else { Install-Pkg "FFmpeg" "Gyan.FFmpeg"; Refresh-Path }

    Invoke-Tool "Installing Git LFS" (Resolve-Exe "winget") @("install", "-e", "--id", "Git.LFS", "--silent", "--accept-source-agreements", "--accept-package-agreements")
    Invoke-Tool "Setting up Git LFS" (Resolve-Exe "git") @("lfs", "install")
    Log "  Git + LFS ready." "Green"

    # ---------- STEP 3: Amadeus project ----------
    Step 3 "Downloading the Amadeus project"
    $proj = Join-Path $InstallDir "Amadeus-Project"
    if (Test-Complete $proj @("backend\main.py","start_windows.bat")) {
        Log "  Already have Amadeus-Project - checking for updates." "Yellow"
        if (Test-Path (Join-Path $proj ".git")) {
            Push-Location $proj
            try {
                & git remote add fork $ForkUrl 2>$null | Out-Null   # no-op if already present
                & git fetch fork $Branch 2>$null | Out-Null
                $current = (& git rev-parse --abbrev-ref HEAD).Trim()
                # The app writes these personal files at runtime (her saved
                # connection, her personality notes). On any machine that has
                # USED the app they show as "locally changed" - expected, and
                # they must not block updates (v4.7: the fresh-VM test found
                # the old check made the installer refuse to update every
                # existing install that had ever saved settings). Everything
                # else still blocks, so genuine local work is never touched.
                # (Git's own fast-forward safety still applies: if an update
                # ever needed to rewrite one of these files, the merge fails
                # and the files stay exactly as the user left them.)
                $personal = "llm_server\.txt|llm_model\.txt|personality\.txt|api_key\.txt"
                $status   = & git status --porcelain | Where-Object { $_ -notmatch $personal }
                if ($current -eq $Branch -and -not $status) {
                    & git merge --ff-only "fork/$Branch" 2>$null | Out-Null
                    if ($LASTEXITCODE -eq 0) { Log "  Updated to the latest version." "Green" }
                    elseif (Move-ToRewrittenHistory "fork/$Branch" $personal) { Log "  Updated to the latest version (the project's history was cleaned up)." "Green" }
                    else { Log "  Could not fast-forward (local commits present?) - leaving it as is." "Yellow" }
                } elseif ($current -eq $LegacyBranch -and -not $status) {
                    # An install from an older installer (feature branch): move
                    # it to main - only when it holds nothing of the user's own.
                    if (Move-ToRewrittenHistory "fork/$Branch" $personal) {
                        & git checkout -q -B $Branch 2>$null | Out-Null
                        & git branch -q --set-upstream-to "fork/$Branch" $Branch 2>$null | Out-Null
                        & git branch -q -D $LegacyBranch 2>$null | Out-Null
                        Log "  Updated to the latest version (now following the main branch)." "Green"
                    } else {
                        Log ("  This folder has changes of its own on branch '" + $current + "', so I did NOT touch it.") "Yellow"
                    }
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

    # Which Python environments this install uses. Normally the plain names;
    # with -EnvSuffix its own set. The suffix is remembered inside the
    # project folder (start_windows.bat, check_amadeus.bat and the launcher
    # read the same file), and a re-run without -EnvSuffix keeps it - so a
    # forgotten option can never install into another install's environments.
    $suffixFile = Join-Path $proj ".runtime\env_suffix.txt"
    $savedSuffix = ""
    if (Test-Path $suffixFile) { $savedSuffix = (Get-Content $suffixFile -Raw).Trim() }
    if ($EnvSuffix) {
        $wanted = "-" + $EnvSuffix.TrimStart("-")
        if ($savedSuffix -and $savedSuffix -ne $wanted) {
            Die ("This folder already uses the environments ending in '" + $savedSuffix + "'. Run again with -EnvSuffix " + $savedSuffix.TrimStart("-") + " (or install into a new folder).")
        }
        $sfx = $wanted
    } else {
        $sfx = $savedSuffix
        if ($sfx) { Log ("  This install uses its own Python environments (ending in '" + $sfx + "') - keeping them.") "Yellow" }
    }
    if ($sfx -and -not $savedSuffix) {
        New-Item -ItemType Directory -Force -Path (Split-Path $suffixFile -Parent) | Out-Null
        Set-Content -Path $suffixFile -Value $sfx -NoNewline -Encoding ascii
    }
    $EnvApp   = "amadeus" + $sfx
    $EnvCm    = "amadeus-cm" + $sfx
    $EnvVoice = "GPTSoVits" + $sfx
    Log ("  Python environments: " + $EnvApp + ", " + $EnvCm + ", " + $EnvVoice)

    # ---------- STEP 4: voice engine ----------
    Step 4 "Downloading the voice engine (GPT-SoVITS)"
    $gpt = Join-Path $proj "GPT-SoVITS"
    Ensure-Clone $GptSovitsUrl $gpt $null @("requirements.txt","extra-req.txt")
    Set-GptSovitsVersion $gpt $GptSovitsCommit
    # Apply this project's voice patches to the GPT-SoVITS copy. The files
    # are committed in this project's repo and mirror the exact setup that is
    # proven in daily use: a prebuilt/optional Japanese text helper and pure
    # python text segmentation, so a fresh PC never needs a C compiler. The
    # copy preserves the folder structure.
    $voicePatch = Join-Path $proj "scripts\voice-patch"
    $patched = @(Get-ChildItem -Path $voicePatch -Recurse -File)
    foreach ($pf in $patched) {
        $rel = $pf.FullName.Substring($voicePatch.Length).TrimStart("\")
        $dest = Join-Path $gpt $rel
        $destDir = Split-Path $dest -Parent
        if (-not (Test-Path $destDir)) { New-Item -ItemType Directory -Force -Path $destDir | Out-Null }
        Copy-Item -LiteralPath $pf.FullName -Destination $dest -Force
    }
    Log ("  Voice patches applied (" + $patched.Count + " files - no C compiler needed).") "Green"

    # ---------- STEP 5: backend env ----------
    Step 5 "Setting up the Amadeus Python environments"
    Ensure-Env $EnvApp
    # The memory sidecar's package list needs a newer Python (3.12+) than the
    # app environment (3.10), so it gets its own environment. The launcher
    # finds it on first start and builds the sidecar from it.
    Ensure-Env $EnvCm "3.13"
    Invoke-PipInEnv $EnvApp @("install", "--upgrade", "pip") "Updating pip (app)"
    # ddgs: the DuckDuckGo search library behind her web search (weather is
    # separate - it uses a keyless Open-Meteo feed and needs no library).
    # The 2026-10-07 fresh-VM run proved a fresh PC was missing this: she
    # answered searches from her own knowledge and honestly said she could
    # not verify them online.
    # e2k: a small offline English->katakana model (public-domain code; its
    # model is trained on CC-BY-SA dictionaries) so her voice says English
    # words the Japanese way instead of switching to an English accent.
    Invoke-PipInEnv $EnvApp @("install", "Flask", "flask-cors", "requests", "tqdm", "langchain", "langchain-openai", "pydantic", "ddgs", "e2k") "Installing the app packages"
    if ($LASTEXITCODE -ne 0) { throw ("Could not install the backend packages. Send me the log. The last lines were:`n" + (Get-ToolTail)) }
    Log ("  App packages installed" + $(if ($script:LastToolInstalled) { " (" + $script:LastToolInstalled + " new)" } else { "" }) + ".") "Green"
    # Smoke test: the whole backend must import cleanly in this environment.
    $backend = Join-Path $proj "backend"
    Push-Location $backend
    # ddgs is imported lazily by the backend (web search), so also check it
    # explicitly: a fresh PC that lost the search library must never hide
    # behind a passing smoke test.
    # Direct Python where the toolbox layout is standard (see Env-Python
    # above); the helper route stays as the fallback.
    $amPy = Env-Python $EnvApp
    if ($amPy) { $smoke = & $amPy -c "import api; import ddgs; import e2k; print('SMOKE_OK')" 2>&1 | Out-String }
    else { $smoke = & $conda run -n $EnvApp python -c "import api; import ddgs; import e2k; print('SMOKE_OK')" 2>&1 | Out-String }
    Write-LogOnly ("  backend smoke test output: " + $smoke.Trim())
    Pop-Location
    if ($smoke -match "SMOKE_OK") { Log "  Backend ready (startup smoke test passed)." "Green" }
    else { Log "  WARNING: the startup smoke test did not pass. If Amadeus fails to start later, send the log." "Yellow"; Log $smoke }

    # ---------- STEP 6: voice env (PyTorch first, then the rest) ----------
    Step 6 "Setting up the voice engine environment (big download, be patient)"
    Ensure-Env $EnvVoice
    Push-Location $gpt
    $pipLog = Join-Path $InstallDir "voice_pip.log"
    # A fresh voice_pip.log per run (the voice engine's pip output; the same
    # lines also go into install_log.txt).
    try { [IO.File]::WriteAllText($pipLog, ("Voice engine packages, installer " + $InstallerVersion + ", " + (Get-Date) + "`r`n"), $Utf8NoBom) } catch {}
    # Up-to-date pip first: its "raw" progress mode drives the progress line.
    Invoke-PipInEnv $EnvVoice @("install", "--upgrade", "pip") "Updating pip (voice engine)" $pipLog

    # --- 6a. Install PyTorch FIRST, with the build that matches your hardware.
    #         Doing it before requirements.txt means the big download happens once
    #         and the rest of the packages see torch as already installed.
    $hasGpu = $false
    try { $nv = & nvidia-smi 2>&1 | Out-String; if ($nv -match "NVIDIA") { $hasGpu = $true } } catch { $hasGpu = $false }
    # One fast local check (no download): what PyTorch build is already in
    # the toolbox, and can it already see the hardware? If the right kind
    # is present and working, the 2.5 GB download is skipped entirely - a
    # re-run on such a machine moves on in seconds. (On a fresh PC the
    # answer is 'none' and the normal download happens.)
    $torchProbe = "try:`n    import torch`n    print('T|' + torch.__version__ + '|' + str(torch.version.cuda or 'cpu') + '|' + str(torch.cuda.is_available()))`nexcept Exception:`n    print('NONE')`n"
    $gptPy = Env-Python $EnvVoice
    if ($gptPy) { $torchInfo = (& $gptPy -c $torchProbe 2>&1 | Out-String).Trim() }
    else { $torchInfo = (& $conda run -n $EnvVoice python -c $torchProbe 2>&1 | Out-String).Trim() }
    Write-LogOnly ("  PyTorch check: " + $torchInfo + " (NVIDIA card seen: " + $hasGpu + ")")
    $torchReady = $false
    if ($torchInfo -match '^T\|') {
        $tp = $torchInfo.Split('|')   # T | version | cuda build tag | can-see-GPU
        $torchReady = ($hasGpu -and $tp[3] -eq 'True') -or ((-not $hasGpu) -and $tp[2] -eq 'cpu')
    }
    if ($torchReady) {
        Log ("  PyTorch " + $tp[1] + " is already in place and ready for this machine - skipping the download.") "Green"
    } else {
        Invoke-PipInEnv $EnvVoice @("uninstall", "-y", "torch", "torchvision", "torchaudio") "Removing an old PyTorch" $pipLog   # clear any stale copy
        if ($hasGpu) {
            Log "  NVIDIA GPU found - installing PyTorch CUDA 12.8 (required for RTX 50-series / Blackwell)." "Green"
            Invoke-PipInEnv $EnvVoice @("install", "torch", "torchvision", "torchaudio", "--index-url", "https://download.pytorch.org/whl/cu128") "Installing PyTorch (graphics-card version, about 2.5 GB)" $pipLog
        } else {
            Log "  No working NVIDIA GPU found - installing the CPU version (works, but voice is slower)." "Yellow"
            Invoke-PipInEnv $EnvVoice @("install", "torch", "torchvision", "torchaudio", "--index-url", "https://download.pytorch.org/whl/cpu") "Installing PyTorch (processor version)" $pipLog
        }
        if ($LASTEXITCODE -ne 0) {
            Pop-Location
            $tail = (Get-Content $pipLog -Tail 15) -join "`n"
            throw "PyTorch failed to install. The last lines of the error are in voice_pip.log (your Amadeus folder):`n$tail"
        }
    }

    # --- 6b. Install the remaining voice packages (any error is now captured).
    Invoke-PipInEnv $EnvVoice @("install", "-r", "extra-req.txt", "--no-deps") "Installing the voice engine extras" $pipLog
    Invoke-PipInEnv $EnvVoice @("install", "-r", "requirements.txt") "Installing the voice engine packages" $pipLog
    if ($LASTEXITCODE -ne 0) {
        # Last resort: install everything except the Japanese text helper
        # (pyopenjtalk). The patched voice code treats it as optional, so
        # voice still works without it.
        $reqAll  = Join-Path $gpt "requirements.txt"
        $reqCore = Join-Path $gpt "requirements-core.txt"
        Get-Content $reqAll | Where-Object { $_ -notmatch "pyopenjtalk" } | Set-Content -Path $reqCore -Encoding ascii
        Log "  Retrying the voice packages without the optional Japanese text helper..." "Yellow"
        Invoke-PipInEnv $EnvVoice @("install", "-r", $reqCore) "Installing the voice engine packages (without the Japanese text helper)" $pipLog
        if ($LASTEXITCODE -eq 0) {
            Log "  Voice packages installed (Japanese text helper skipped - voice still works)." "Yellow"
        } else {
            Pop-Location
            $tail = (Get-Content $pipLog -Tail 15) -join "`n"
            throw "The voice engine packages failed to install. The last lines of the error are in voice_pip.log (your Amadeus folder):`n$tail"
        }
    }
    Pop-Location

    # ---------- STEP 7: confirm PyTorch can actually use your GPU ----------
    Step 7 "Checking that PyTorch can use your GPU"
    # Direct Python where the toolbox layout is standard (step 6 already
    # located it in $gptPy); the helper route stays as the fallback.
    if ($gptPy) { $gpuCheck = & $gptPy -c "import torch;print('GPU_OK' if torch.cuda.is_available() else 'GPU_NO')" 2>&1 | Out-String }
    else { $gpuCheck = & $conda run -n $EnvVoice python -c "import torch;print('GPU_OK' if torch.cuda.is_available() else 'GPU_NO')" 2>&1 | Out-String }
    Write-LogOnly ("  PyTorch GPU check: " + $gpuCheck.Trim())
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
        Invoke-Tool "Downloading the voice model files" (Resolve-Exe "git") @("clone", "--progress", "https://huggingface.co/lj1995/GPT-SoVITS", $hf)
        if ($LASTEXITCODE -ne 0) { throw ("Could not download the voice model files. Check your internet connection, then run the installer again.`n" + (Get-ToolTail)) }
        Log "  Voice model files downloaded." "Green"
    } else { Log "  Voice model files already downloaded - reusing them." "Yellow" }
    $dst = Join-Path $gpt "GPT_SoVITS\pretrained_models"
    New-Item -ItemType Directory -Force -Path $dst | Out-Null
    # Copy everything from the cloned repo (but skip its hidden .git folder).
    Get-ChildItem -Path $hf -Force | Where-Object { $_.Name -ne ".git" } | ForEach-Object {
        Copy-Item -Path $_.FullName -Destination $dst -Recurse -Force
    }
    if (-not (Test-Path (Join-Path $dst "s2G488k.pth"))) { throw "The voice model files copied but look incomplete (missing s2G488k.pth). Run the installer again to retry." }
    Log "  Voice model files copied into place." "Green"
    # The newer voice engine detects the language of each line with a
    # language-identification model (about 130 MB) that it fetches ON FIRST
    # USE - and its downloader refuses to run unless its cache folder already
    # exists (a fresh clone has none), which made the very first voice line
    # fail on fresh installs (2026-10-07, fresh-VM test). Pre-download it now
    # so her first voice line never waits on a network fetch. Best effort: if
    # this download fails (offline machine, blocked CDN), the launcher creates
    # the cache folder at start and the engine fetches the model on first use
    # instead.
    $fldDir = Join-Path $dst "fast_langdetect"
    New-Item -ItemType Directory -Force -Path $fldDir | Out-Null
    $fldFile = Join-Path $fldDir "lid.176.bin"
    if (Test-Path $fldFile) {
        Log "  Voice language model already present." "Yellow"
    } else {
        $fldUrl = "https://dl.fbaipublicfiles.com/fasttext/supervised-models/lid.176.bin"
        # Download with Python (its TLS works on more machines than
        # PowerShell's - on some PCs Invoke-WebRequest cannot open an SSL
        # connection at all while Python is fine); PowerShell is the fallback.
        $fldOk = $false
        $py = Join-Path $condaHome "python.exe"
        if (Test-Path $py) {
            try {
                Log "  Downloading the voice language model (about 130 MB)..."
                & $py -c "import sys, urllib.request; urllib.request.urlretrieve(sys.argv[1], sys.argv[2])" $fldUrl $fldFile
                $fldOk = ($LASTEXITCODE -eq 0)
            } catch { $fldOk = $false }
        }
        if (-not $fldOk) {
            try {
                Log "  Downloading the voice language model (about 130 MB)..."
                Invoke-WebRequest $fldUrl -OutFile $fldFile -UseBasicParsing -TimeoutSec 1800
                $fldOk = ($LASTEXITCODE -eq 0)
            } catch { $fldOk = $false }
        }
        if ($fldOk -and (Test-Path $fldFile)) {
            Log "  Voice language model pre-downloaded." "Green"
        } else {
            # A half-downloaded file would be TRUSTED later (the engine only
            # fetches when the file is absent), so delete it and fall back to
            # a fresh automatic download at first use.
            Remove-Item $fldFile -Force -ErrorAction SilentlyContinue
            Log "  Could not pre-download the voice language model right now (is your internet reachable?). No problem - the voice engine will download it automatically the first time she speaks." "Yellow"
        }
    }
    # The voice engine reads Western-script words (the name you typed in,
    # "AI", ...) with a separate english text module. That module needs a
    # few small data files (a pronunciation dictionary + grammar patterns,
    # a few MB in total). A fresh PC does not have them, and the automatic
    # downloader inside the g2p_en package only knows the OLD file names -
    # while the newer text library a fresh install receives looks for the
    # renamed ones - so the first line containing a Western-script word
    # crashed the whole voice request (2026-10-07, fresh-VM test: her
    # wake-up line came out without voice). Pre-fetch every name into the
    # voice engine's own data folder (its first search path). Best effort:
    # if this fails (offline machine), the launcher checks again on every
    # start, and the engine fetches the rest on first use.
    $nltkDir = Join-Path $gpt "runtime\nltk_data"
    New-Item -ItemType Directory -Force -Path $nltkDir | Out-Null
    $nltkCode = "import os, sys, socket`nsocket.setdefaulttimeout(120)`nos.environ['NLTK_DATA'] = sys.argv[1]`nos.makedirs(sys.argv[1], exist_ok=True)`nimport nltk`nok = True`nfor res in sys.argv[2:]:`n    try:`n        nltk.data.find(res)`n        continue`n    except Exception:`n        pass`n    try:`n        if not nltk.download(res, quiet=True, download_dir=sys.argv[1]):`n            ok = False`n    except Exception:`n        ok = False`nprint('NLTK_DATA_OK' if ok else 'NLTK_DATA_INCOMPLETE')`n"
    # v4.11 field fix (2026-10-08, dev PC E: drive install): call the
    # voice env's python.exe directly instead of through `conda run`. The
    # conda-run form of this exact step deadlocked there: the call never
    # returned and no child process survived, stalling the install silently
    # (reproduced twice). Direct python.exe is the same interpreter the
    # wrapper would launch - it just skips the middleman - and it is the
    # same pattern the language-model step above uses, which worked fine.
    # The 120 s socket timeout in $nltkCode keeps a stalled download from
    # hanging forever (it then falls back to the yellow note + on-first-use
    # fetch, as before).
    $nltkPy = Join-Path $condaHome ("envs\" + $EnvVoice + "\python.exe")
    if (Test-Path $nltkPy) {
        $nltkOut = & $nltkPy -c $nltkCode $nltkDir averaged_perceptron_tagger_eng averaged_perceptron_tagger cmudict 2>&1 | Out-String
    } else {
        # Fallback for exotic layouts where the env's python is elsewhere.
        $nltkOut = & $conda run -n $EnvVoice python -c $nltkCode $nltkDir averaged_perceptron_tagger_eng averaged_perceptron_tagger cmudict 2>&1 | Out-String
    }
    Write-LogOnly ("  voice text data fetch output: " + $nltkOut.Trim())
    if ($nltkOut -match "NLTK_DATA_OK") {
        Log "  Voice text data for Western-script words is in place." "Green"
    } else {
        Log "  Could not pre-fetch all of the voice text data right now (is your internet reachable?). No problem - Amadeus checks again every time she starts, and the voice engine fetches anything still missing the first time she speaks such a word." "Yellow"
    }

    # ---------- STEP 9: frontend ----------
    Step 9 "Setting up the web interface (frontend)"
    Push-Location (Join-Path $proj "frontend")
    # --no-audit --no-fund: skip npm's background registry checks - they
    # save nothing for an install and can add tens of seconds on fresh PCs.
    Invoke-Tool "Setting up the web interface" (Join-Path $env:SystemRoot "System32\cmd.exe") @("/d", "/c", "npm", "install", "--no-audit", "--no-fund")
    if ($LASTEXITCODE -ne 0) { Log "  npm install reported an issue, but this is often harmless. Continuing." "Yellow" } else { Log "  Frontend ready." "Green" }
    Pop-Location

    # ---------- STEP 10: built-in local model (option A only) ----------
    # Last on purpose: the biggest download, and a failure here must not
    # block anything else (re-running the installer resumes it).
    $llmReady = $false
    if ($llmChoice.mode -ne "builtin") {
        Write-LocalLlmConfig $proj $llmChoice "" $false "" $false "" $false   # switches an earlier option-A install off
    } else {
        Step 10 ("Setting up her built-in local model - " + $TierTable.models.($llmChoice.model).label + " (big download, be patient)")
        $llmReady = Install-BuiltinLlm $proj $llmChoice
    }

    # ---------- DONE ----------
    Step 11 "ALL DONE!"
    Log "" "Green"
    if ($llmChoice.mode -eq "builtin") {
        if ($llmReady) {
            Log ("Everything is installed. Amadeus thinks with her built-in local model (" + $TierTable.models.($llmChoice.model).label + ") - nothing to connect.") "Green"
        } else {
            Log "Everything is installed EXCEPT her built-in local model (see the messages above)." "Yellow"
            Log "Run this installer again when you can - it continues the download where it stopped." "Yellow"
        }
        Log "To START Amadeus, double-click this file:" "Green"
        Log ("       " + (Join-Path $proj "start_windows.bat"))
        Log "(You can switch to your own model or a cloud service any time in Settings -> Connection.)"
        Log ""
        Log ("(Install log saved to: " + $LogPath + ")")
        Offer-StartAmadeus $proj
        exit 0
    }
    Log "Everything is installed. Amadeus needs a model to think with:" "Green"
    Log "  - a cloud model service (e.g. OpenRouter or OpenAI), or"
    Log "  - a local model server on this PC (e.g. KoboldCpp, Unsloth"
    Log "    Desktop, NInfer, Ollama or vLLM)."
    Log "Here is what to do next:" "Green"
    Log "  1) Double-click this file to START Amadeus:"
    Log ("       " + (Join-Path $proj "start_windows.bat"))
    Log "  2) When she opens in your browser, read her introduction message -"
    Log "     it walks you through connecting your model, step by step."
    Log "  3) (Shortcut) You can also open Settings -> Connection and click"
    Log "     'Test connection' - the dot turns green when she can reach"
    Log "     your model. Pick your model from the chips (or type its name)"
    Log "     and paste its API key, if your provider has one."
    Log ""
    Log ("(Install log saved to: " + $LogPath + ")")
    Offer-StartAmadeus $proj

} catch {
    Log ("UNEXPECTED ERROR: " + $_.Exception.Message) "Red"
    Log ("At line: " + $_.InvocationInfo.ScriptLineNumber) "Red"
    Die "Something unexpected went wrong."
}
