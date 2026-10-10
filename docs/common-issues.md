# Common Issues

## Launcher says a port is already in use

The launcher attempts to clear stale Amadeus listeners from:

```text
9880
5050
5173
9870
```

If a port cannot be cleared, inspect:

```text
.runtime/logs/
```

The backend intentionally uses port `5050` rather than `5000` to avoid
conflicts with macOS services that commonly use port 5000.

---

## She has no long-term memory

The memory sidecar is optional by design — if it isn't running, everything
else works and she simply doesn't remember across sessions.

- Check that it started: `.runtime/logs/cm-sidecar.log` (the launcher also
  prints a warning if the sidecar never became ready).
- First launch is slow (a few minutes): the launcher builds the sidecar's
  venv and downloads its packages. Wait for the next start.
- On a manual install, make sure the `amadeus-cm` conda environment
  (Python 3.13) exists — the launcher builds the sidecar from it.
- The sidecar needs the same model server as the app to *learn* (it
  re-uses your Settings → Connection values); memory recall itself works
  even while the engine is still warming up.

---

## macOS says `start_macos.command` cannot be executed

Run:

```bash
chmod +x start_macos.command
```

and try again.

---

## Amadeus cannot connect to the backend

Check that the backend is available at:

```text
http://127.0.0.1:5050
```

When running the full launcher, inspect:

```text
.runtime/logs/backend.log
```

---

## Amadeus has no voice

Check that GPT-SoVITS is available at:

```text
http://127.0.0.1:9880
```

Also verify that the required pretrained models exist under:

```text
GPT-SoVITS/GPT_SoVITS/pretrained_models/
```

(and that they are real files, not tiny Git-LFS pointers — see
[Manual step 7](manual-installation.md#7-download-gpt-sovits-pretrained-models)).

---

## Live2D character does not appear

Check the browser developer console and verify that the model reaches the
expected loading stages:

```text
model3.json loaded
moc3 loaded
texture loaded
model loaded successfully
```

Also verify that the WebGL shader assets exist under:

```text
frontend/public/cubism-shaders/WebGL/
```

and that the model assets exist under:

```text
frontend/public/live2d/
```

---

## `Shader program is not initialized`

The Cubism Web renderer loads shader files asynchronously. A warning during
the initial frames can occur while the shaders are loading.

If the character eventually renders, this initial warning is not fatal.

Persistent shader compile errors usually indicate that the shader files are
not being served from the expected public path.

---

## Frontend dependencies are missing

Run:

```bash
cd frontend
npm install
```

The automatic launcher also performs this step if `node_modules/` does not
exist.

---

## Backend dependencies are missing or outdated

Run:

```bash
conda activate amadeus
cd backend
pip install -r requirements.in
```

---

## Resetting Conversation Memory

Back up the database first if you want to preserve the conversation history.

macOS/Linux:

```bash
rm backend/data/memory.db
```

Windows:

```bat
del backend\data\memory.db
```

To also reset her long-term memory, delete `backend/data/character_memory/`
(see [Runtime Data and Secrets](using-amadeus.md#runtime-data-and-secrets)). Restart Amadeus
afterward. A new database will be created automatically.
