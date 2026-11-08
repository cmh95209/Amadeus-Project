import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
} from "react";

import { SpeechPlayer } from "../audio/SpeechPlayer";
import type { VoicePlayback } from "../audio/voicePacing";
import { KurisuController } from "../live2d/KurisuController";

import type { PlayMotionResult } from "../live2d/MotionPlayer";

export type Live2DCharacterHandle = {
  playMotion: (group: string) => PlayMotionResult;
  prepareSpeech: () => Promise<void>;
  playSpeech: (url: string) => Promise<void>;
  stopSpeech: () => void;
  /** A line is loading or still being spoken. */
  isSpeechBusy: () => boolean;
  /** She looks at a picture you sent (eyes down, thinking pose) while on. */
  setLooking: (looking: boolean) => void;
};

// Redraws in a row before the "bring her back" button (a page reload) takes
// over: a phone that keeps losing the drawing area must not loop.
const REDRAW_LIMIT = 3;
const REDRAW_WINDOW_MS = 60_000;

type Live2DCharacterProps = {
  onSpeechError?: (message: string) => void;
  /** Settings -> Preferences -> Voice playback. */
  voicePlayback?: VoicePlayback;
  /** 0..1 while her voice is prepared before it plays; null when not waiting. */
  onVoicePreparing?: (progress: number | null) => void;
};

const Live2DCharacter = forwardRef<Live2DCharacterHandle, Live2DCharacterProps>(
  function Live2DCharacter({ onSpeechError, voicePlayback = "smooth", onVoicePreparing }, ref) {
    const onErrorRef = useRef(onSpeechError);
    onErrorRef.current = onSpeechError;
    const onPreparingRef = useRef(onVoicePreparing);
    onPreparingRef.current = onVoicePreparing;
    const playbackRef = useRef(voicePlayback);
    playbackRef.current = voicePlayback;
    const canvasRef = useRef<HTMLCanvasElement>(null);
    const controllerRef = useRef<KurisuController | null>(null);
    const speechPlayerRef = useRef<SpeechPlayer | null>(null);
    const lookingRef = useRef(false);
    const redraws = useRef<number[]>([]);
    // Each drawing gets a fresh canvas: a lost one never comes back reliably.
    const [drawing, setDrawing] = useState(0);
    const [broken, setBroken] = useState(false);

    useImperativeHandle(ref, () => ({
      playMotion(group: string) {
        return controllerRef.current?.playMotion(group) ?? "not-ready";
      },
      async prepareSpeech() {
        await speechPlayerRef.current?.prepare();
      },
      async playSpeech(url: string) {
        await speechPlayerRef.current?.play(url);
      },
      stopSpeech() {
        speechPlayerRef.current?.stop();
      },
      isSpeechBusy() {
        return speechPlayerRef.current?.isBusy() ?? false;
      },
      setLooking(looking: boolean) {
        lookingRef.current = looking;
        controllerRef.current?.setLooking(looking);
      },
    }), []);

    // Her voice outlives a redraw of her picture.
    useEffect(() => {
      const speechPlayer = new SpeechPlayer({
        onSpeakingChange: (speaking) => controllerRef.current?.setSpeaking(speaking),
        onAmplitude: (value) => controllerRef.current?.setLipSyncValue(value),
        onError: (message) => onErrorRef.current?.(message),
        onPreparing: (progress) => onPreparingRef.current?.(progress),
      });
      speechPlayer.setPlayback(playbackRef.current);
      speechPlayerRef.current = speechPlayer;
      return () => {
        speechPlayerRef.current = null;
        speechPlayer.destroy();
      };
    }, []);

    useEffect(() => {
      speechPlayerRef.current?.setPlayback(voicePlayback);
    }, [voicePlayback]);

    useEffect(() => {
      const canvas = canvasRef.current;
      if (!canvas) {
        return;
      }

      const controller = new KurisuController(
        canvas,
        "/live2d/kurisu/kurisu.model3.json"
      );

      controllerRef.current = controller;
      let gone = false;
      let lost = false;
      let asked = false;

      // Draw her again on a fresh canvas, once the page is in view.
      const redraw = () => {
        if (asked || document.visibilityState !== "visible") return;
        asked = true;
        const now = Date.now();
        redraws.current = redraws.current.filter((t) => now - t < REDRAW_WINDOW_MS);
        if (redraws.current.length >= REDRAW_LIMIT) {
          setBroken(true);
          return;
        }
        redraws.current.push(now);
        setDrawing((n) => n + 1);
      };
      const onLost = (event: Event) => {
        event.preventDefault();
        lost = true;
        redraw();
      };
      const onBack = () => {
        if (lost || controller.isContextLost()) {
          lost = true;
          redraw();
        }
      };
      canvas.addEventListener("webglcontextlost", onLost);
      document.addEventListener("visibilitychange", onBack);
      window.addEventListener("pageshow", onBack);

      void controller.initialize().then(() => {
        if (gone) return;
        setBroken(false);
        if (lookingRef.current) controller.setLooking(true);
      }).catch((error) => {
        console.error("Character initialization failed:", error);
        if (!gone) setBroken(true);
      });

      return () => {
        gone = true;
        canvas.removeEventListener("webglcontextlost", onLost);
        document.removeEventListener("visibilitychange", onBack);
        window.removeEventListener("pageshow", onBack);
        if (controllerRef.current === controller) controllerRef.current = null;
        controller.destroy();
      };
    }, [drawing]);

    return (
      <>
        <canvas
          key={drawing}
          ref={canvasRef}
          className="live2d-canvas"
          // A lost canvas shows the browser's white "sad page" box.
          style={broken ? { visibility: "hidden" } : undefined}
        />
        {broken && (
          <button
            type="button"
            className="ghost-button live2d-retry"
            // After several losses the browser refuses new drawings until the
            // page reloads (no greeting; the draft and tray come back).
            onClick={() => window.location.reload()}
          >
            Tap to bring her back
          </button>
        )}
      </>
    );
  }
);

export default Live2DCharacter;
