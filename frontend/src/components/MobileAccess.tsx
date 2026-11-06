import { useEffect, useState, type ReactNode } from "react";
import {
  changeRemoteAccess,
  getRemoteAccess,
  isLocalPage,
  type RemoteAccess,
  type RemoteSetupResult,
} from "../api";
import { qrcodegen } from "../vendor/qrcodegen";

// Settings -> "Mobile and remote access": open her on your phone, tablet or
// another PC through Tailscale (backend/remote_access.py). The same steps are
// in docs/remote-access.md. Only `tailscale serve` (your own devices) - never
// Funnel: the app has no login, so Funnel would put her on the public internet.

const GUIDE_URL = "https://github.com/cmh95209/Amadeus-Project/blob/main/docs/remote-access.md";
const ON_COMMANDS = [
  "tailscale serve --bg --https=443 http://localhost:5173",
  "tailscale serve --bg --https=8443 http://127.0.0.1:5050",
];
const OFF_COMMANDS = [
  "tailscale serve --https=443 off",
  "tailscale serve --https=8443 off",
];

function CopyButton({ text, label = "Copy" }: { text: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button type="button" className="ghost-button" onClick={() => {
      void navigator.clipboard?.writeText(text).then(() => {
        setCopied(true);
        window.setTimeout(() => setCopied(false), 2000);
      });
    }}>{copied ? "Copied" : label}</button>
  );
}

function Command({ text }: { text: string }) {
  return (
    <div className="conn-row remote-command">
      <code>{text}</code>
      <CopyButton text={text} />
    </div>
  );
}

// The address as a QR code for the phone's camera: dark squares on white with
// the standard 4-square quiet border (scanners need both, even in dark mode).
function QrPicture({ text }: { text: string }) {
  let qr: qrcodegen.QrCode;
  try {
    qr = qrcodegen.QrCode.encodeText(text, qrcodegen.QrCode.Ecc.MEDIUM);
  } catch {
    return null;
  }
  const border = 4;
  const side = qr.size + border * 2;
  let path = "";
  for (let y = 0; y < qr.size; y++) {
    for (let x = 0; x < qr.size; x++) {
      if (qr.getModule(x, y)) path += `M${x + border},${y + border}h1v1h-1z`;
    }
  }
  return (
    <svg className="remote-qr" viewBox={`0 0 ${side} ${side}`} role="img"
      aria-label={`QR code for ${text}`} shapeRendering="crispEdges">
      <rect width={side} height={side} fill="#fff" />
      <path d={path} fill="#000" />
    </svg>
  );
}

function Status({ remote, error }: { remote: RemoteAccess | null; error: string }) {
  if (error) return <p className="settings-error" role="alert">{error}</p>;
  if (remote === null) return <p className="settings-help" role="status">Checking...</p>;
  if (remote.state === "on") {
    return <>
      <div className="conn-status remote-status ok"><span className="conn-dot" aria-hidden="true" />
        <span>On - through Tailscale. Only devices signed in to your Tailscale account can open her.</span></div>
      <p className="settings-help">
        To open her on your phone, scan this code with its camera, or type the address in its browser
        (Tailscale must be on on the phone too).
      </p>
      <QrPicture text={remote.address} />
      <div className="conn-row">
        <input className="remote-address" readOnly value={remote.address} aria-label="Her address"
          onFocus={(event) => event.target.select()} />
        <CopyButton text={remote.address} />
      </div>
    </>;
  }
  if (remote.state === "partly") {
    return <div className="conn-status remote-status warn"><span className="conn-dot" aria-hidden="true" />
      <span>Tailscale is on, but {remote.page ? "her backend is" : remote.backend ? "her page is" : "she is"} not
        shared yet. Do step 3 below.</span></div>;
  }
  return <div className="conn-status remote-status fail"><span className="conn-dot" aria-hidden="true" />
    <span>{remote.state === "off" ? "Tailscale is installed but not running on this PC." : "Tailscale is not installed on this PC."}{" "}
      Until it is, she can only be opened on this PC.</span></div>;
}

function FunnelWarning({ on }: { on: boolean }) {
  if (on) {
    return (
      <div className="remote-warning danger" role="alert">
        <strong>Funnel is ON for this PC - her page may be on the public internet.</strong> The app has
        no login, so anyone with the address could talk to her and read your chats. Turn it off now in a
        terminal: <Command text="tailscale funnel reset" />
        (this also stops the remote access below; turn it on again with step 3.)
      </div>
    );
  }
  return (
    <div className="remote-warning" role="note">
      <strong>Use "serve", never "Funnel".</strong> Serve shares her only with devices signed in to your
      own Tailscale account. Funnel would put her on the public internet - the app has no login, so
      anyone with the address could talk to her and read your chats.
    </div>
  );
}

function Steps({ children }: { children: ReactNode }) {
  return <ol className="remote-steps">{children}</ol>;
}

export default function MobileAccess({ active, busy }: { active: boolean; busy: boolean }) {
  const [remote, setRemote] = useState<RemoteAccess | null>(null);
  const [remoteError, setRemoteError] = useState("");
  const [asking, setAsking] = useState<"on" | "off" | null>(null);
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<RemoteSetupResult | null>(null);
  const [error, setError] = useState("");

  function refresh(cancelled = () => false) {
    getRemoteAccess().then((r) => { if (!cancelled()) { setRemote(r); setRemoteError(""); } })
      .catch((err) => { if (!cancelled()) setRemoteError(err instanceof Error ? err.message : "Could not check"); });
  }

  // Read again each time the tab is opened (Tailscale may have changed meanwhile).
  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    refresh(() => cancelled);
    return () => { cancelled = true; };
  }, [active]);

  async function run(action: "on" | "off") {
    setAsking(null);
    setRunning(true);
    setResult(null);
    setError("");
    try {
      const done = await changeRemoteAccess(action);
      setResult(done);
      if (done.ok) setRemote(done.access);
      else refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not run the commands");
    } finally {
      setRunning(false);
    }
  }

  const on = remote?.state === "on";
  const disabled = busy || running;
  const commands = asking === "off" ? OFF_COMMANDS : ON_COMMANDS;

  return (
    <>
      <p className="settings-intro">
        Open her on your phone, tablet or another PC, through Tailscale (free for personal use).
        She still runs on this PC; your devices only show her page.
      </p>

      <div className="conn-section">
        <span className="conn-section-title">Remote access (phone, tablet, other PCs)</span>
        <Status remote={remote} error={remoteError} />
        <FunnelWarning on={Boolean(remote?.funnel)} />
      </div>

      <div className="conn-section">
        <span className="conn-section-title">How to set it up</span>
        <Steps>
          <li>
            <strong>Install Tailscale on this PC and on your phone</strong> (from tailscale.com, or the
            phone's app store).
          </li>
          <li>
            <strong>Sign in to the same Tailscale account on both</strong>, and leave Tailscale on.
          </li>
          <li>
            <strong>Turn on remote access</strong> on this PC: press "Set it up for me" below, or run
            these two commands in a terminal yourself:
            <Command text={ON_COMMANDS[0]} />
            <Command text={ON_COMMANDS[1]} />
            The first time, Tailscale may ask you to allow "Serve" for your account in your browser -
            allow it, then run the commands again.
          </li>
          <li>
            <strong>On your phone</strong>, open the address shown above (or scan its QR code).
          </li>
        </Steps>

        {!isLocalPage ? (
          <p className="settings-help">The buttons that change remote access are only on the PC itself.</p>
        ) : asking ? (
          <div className="system-confirm" role="group"
            aria-label={asking === "on" ? "Confirm turning remote access on" : "Confirm turning remote access off"}>
            <p>
              {asking === "on"
                ? "This runs these two commands on this PC, so your own Tailscale devices can open her:"
                : "This runs these two commands on this PC; your other devices can no longer open her:"}
            </p>
            {commands.map((c) => <p key={c}><code>{c}</code></p>)}
            <div className="system-confirm-buttons">
              <button type="button" className="primary-button" onClick={() => void run(asking)}>
                {asking === "on" ? "Run them" : "Turn it off"}
              </button>
              <button type="button" className="ghost-button" onClick={() => setAsking(null)}>Cancel</button>
            </div>
          </div>
        ) : (
          <div className="modal-actions remote-actions">
            {(on || remote?.state === "partly") && (
              <button type="button" className="ghost-button" disabled={disabled} onClick={() => setAsking("off")}>
                Turn remote access off
              </button>
            )}
            {!on && (
              <button type="button" className="primary-button" disabled={disabled || remote?.state === "missing"}
                onClick={() => setAsking("on")}>
                {running ? "Running..." : "Set it up for me"}
              </button>
            )}
          </div>
        )}

        {result && !result.ok && result.reason === "enable" && (
          <p className="settings-help" role="status">
            Tailscale needs you to allow "Serve" for your account first (once):{" "}
            <a href={result.link} target="_blank" rel="noreferrer">open the Tailscale page</a>, allow it,
            then press "Set it up for me" again.
          </p>
        )}
        {result && !result.ok && result.reason === "failed" && (
          <p className="settings-error" role="alert">Tailscale said: {result.message}</p>
        )}
        {result && !result.ok && result.reason === "missing" && (
          <p className="settings-error" role="alert">Tailscale is not installed on this PC (step 1).</p>
        )}
        {result?.ok && <p className="settings-success" role="status">Done.</p>}
        {error && <p className="settings-error" role="alert">{error}</p>}

        <p className="settings-help">
          To turn it off by hand: <code>{OFF_COMMANDS[0]}</code> and <code>{OFF_COMMANDS[1]}</code>.
          The same steps in more detail: <a href={GUIDE_URL} target="_blank" rel="noreferrer">remote access guide</a>.
        </p>
      </div>
    </>
  );
}
