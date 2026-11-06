import { CubismMatrix44 } from "@framework/math/cubismmatrix44";

import { initializeCubism } from "./cubismBootstrap";
import { KurisuModel } from "./KurisuModel";
import type { PlayMotionResult } from "./MotionPlayer";

export class KurisuController {
  private canvas: HTMLCanvasElement;
  private modelPath: string;

  private gl: WebGLRenderingContext | null = null;
  private model: KurisuModel | null = null;

  private animationFrame = 0;
  private destroyed = false;
  private lastFrameTime = 0;
  private resizeObserver: ResizeObserver | null = null;
  private pixelRatio = 0;

  constructor(
    canvas: HTMLCanvasElement,
    modelPath: string
  ) {
    this.canvas = canvas;
    this.modelPath = modelPath;
  }

  async initialize(): Promise<void> {
    initializeCubism();

    const gl = this.canvas.getContext(
      "webgl",
      {
        alpha: true,
        premultipliedAlpha: true,
        antialias: true,
      }
    );

    if (!gl) {
      throw new Error(
        "WebGL is not available"
      );
    }

    this.gl = gl;

    this.resize();

    this.resizeObserver = new ResizeObserver(() => this.resize());
    this.resizeObserver.observe(this.canvas);

    window.addEventListener(
      "resize",
      this.handleResize
    );

    const model = new KurisuModel(gl);
    this.model = model;

    try {
      await model.load(this.modelPath);
    } catch (error) {
      if (this.destroyed) return;
      this.destroy();
      throw error;
    }

    if (this.destroyed) return;
    this.lastFrameTime = performance.now();

    this.render();
  }

  private handleResize = (): void => {
    this.resize();
  };

  private resize(): void {
    const rect =
      this.canvas.getBoundingClientRect();

    const dpr =
      window.devicePixelRatio || 1;
    this.pixelRatio = dpr;

    const width = Math.max(
      1,
      Math.floor(rect.width * dpr)
    );

    const height = Math.max(
      1,
      Math.floor(rect.height * dpr)
    );

    if (
      this.canvas.width !== width ||
      this.canvas.height !== height
    ) {
      this.canvas.width = width;
      this.canvas.height = height;
    }

    if (this.gl) {
      this.gl.viewport(
        0,
        0,
        width,
        height
      );

      this.model?.setRenderTargetSize(
        width,
        height
      );
    }
  }

  private render = (): void => {
    if (
      this.destroyed ||
      !this.gl ||
      !this.model
    ) {
      return;
    }

    const gl = this.gl;

    // Container changes are observed; also handle moving between displays.
    if (this.pixelRatio !== (window.devicePixelRatio || 1)) this.resize();

    gl.viewport(
      0,
      0,
      this.canvas.width,
      this.canvas.height
    );

    gl.clearColor(
      0,
      0,
      0,
      0
    );

    gl.clear(
      gl.COLOR_BUFFER_BIT
    );

    const now = performance.now();
    const deltaSeconds = Math.min((now - this.lastFrameTime) / 1000, 0.1);
    this.lastFrameTime = now;
    this.model.update(deltaSeconds);

    const matrix =
      new CubismMatrix44();

    const aspect =
      this.canvas.width /
      this.canvas.height;

    const characterScale = 1.5;

    if (aspect > 1) {
      matrix.scale(
        (1 / aspect) * characterScale,
        characterScale
      );
    } else {
      matrix.scale(
        characterScale,
        aspect * characterScale
      );
    }

    matrix.translate(
      0,
      -0.12
    );

    const viewport = [
      0,
      0,
      this.canvas.width,
      this.canvas.height,
    ];

    this.model.draw(
      matrix,
      viewport
    );

    this.animationFrame =
      requestAnimationFrame(
        this.render
      );
  };

  playMotion(group: string): PlayMotionResult {
    return this.destroyed ? "not-ready" : this.model?.playMotion(group) ?? "not-ready";
  }

  setSpeaking(speaking: boolean): void {
    if (!this.destroyed) this.model?.setSpeaking(speaking);
  }

  setLipSyncValue(value: number): void {
    if (!this.destroyed) this.model?.setLipSyncValue(value);
  }

  setLooking(looking: boolean): void {
    if (!this.destroyed) this.model?.setLooking(looking);
  }

  /** Android may take the graphics memory back while the app is in the
   *  background; the drawing area then stays blank for good. */
  isContextLost(): boolean {
    return !!this.gl?.isContextLost();
  }

  destroy(): void {
    this.destroyed = true;

    cancelAnimationFrame(
      this.animationFrame
    );
    this.resizeObserver?.disconnect();
    this.resizeObserver = null;

    window.removeEventListener(
      "resize",
      this.handleResize
    );

    this.model?.release();
    this.model = null;

    this.gl = null;
  }
}
