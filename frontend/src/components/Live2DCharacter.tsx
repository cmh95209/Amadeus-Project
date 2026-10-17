import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useRef,
} from "react";

import { SpeechPlayer } from "../audio/SpeechPlayer";
import { KurisuController } from "../live2d/KurisuController";

import type { PlayMotionResult } from "../live2d/MotionPlayer";

export type Live2DCharacterHandle = {
  playMotion: (group: string) => PlayMotionResult;
  prepareSpeech: () => Promise<void>;
  playSpeech: (url: string) => Promise<void>;
  stopSpeech: () => void;
  /** A line is loading or still being spoken. */
  isSpeechBusy: () => boolean;
};

const Live2DCharacter = forwardRef<Live2DCharacterHandle, { onSpeechError?: (message: string) => void }>(
  function Live2DCharacter({ onSpeechError }, ref) {
    const onErrorRef = useRef(onSpeechError);
    onErrorRef.current = onSpeechError;
    const canvasRef = useRef<HTMLCanvasElement>(null);
    const controllerRef = useRef<KurisuController | null>(null);
    const speechPlayerRef = useRef<SpeechPlayer | null>(null);

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
    }), []);

    useEffect(() => {
      if (!canvasRef.current) {
        return;
      }

      const controller = new KurisuController(
        canvasRef.current,
        "/live2d/kurisu/kurisu.model3.json"
      );

      controllerRef.current = controller;

      const speechPlayer = new SpeechPlayer({
        onSpeakingChange: (speaking) => controller.setSpeaking(speaking),
        onAmplitude: (value) => controller.setLipSyncValue(value),
        onError: (message) => onErrorRef.current?.(message),
      });
      speechPlayerRef.current = speechPlayer;

      void controller.initialize().catch((error) => {
        console.error("Character initialization failed:", error);
      });

      return () => {
        speechPlayerRef.current = null;
        speechPlayer.destroy();
        controllerRef.current = null;
        controller.destroy();
      };
    }, []);

    return (
      <canvas
        ref={canvasRef}
        className="live2d-canvas"
      />
    );
  }
);

export default Live2DCharacter;
