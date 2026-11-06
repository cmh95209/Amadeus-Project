import { CubismModelSettingJson } from "@framework/cubismmodelsettingjson";
import { CubismFramework } from "@framework/live2dcubismframework";
import { CubismMatrix44 } from "@framework/math/cubismmatrix44";
import { CubismUserModel } from "@framework/model/cubismusermodel";
import { MotionPlayer } from "./MotionPlayer";
import type { PlayMotionResult } from "./MotionPlayer";
import { loadTexture } from "./textureLoader";

/** Loads the character and delegates animation and texture work. */
export class KurisuModel extends CubismUserModel {
  private settings: CubismModelSettingJson | null = null;
  private readonly motions = new MotionPlayer();
  private readonly loading = new AbortController();
  private readonly textures: WebGLTexture[] = [];
  private released = false;
  private ready = false;
  private speaking = false;
  private lipSyncValue = 0;
  private readonly mouthOpenId = CubismFramework.getIdManager().getId("ParamMouthOpenY");
  // "Looking" at a picture you sent (D7, 2026-10-26): made from parameters
  // the model already has (it has no head or body angle): eyes glance down,
  // irises a little wider, the thinking arms + face. Eased in and out.
  private looking = false;
  private lookWeight = 0;
  private static readonly LOOK_POSE: [string, number][] = [
    ["ParamEyeBallY", -0.55],     // share of the way to the lowest value
    ["Param3", 0.35],             // IrisScale R, toward its highest value
    ["Param6", 0.8],              // Arms Thinking Movement
    ["Param8", 0.8],              // Thinking FaceExpression
  ];
  private readonly lookIds = KurisuModel.LOOK_POSE.map(([name, share]) =>
    [CubismFramework.getIdManager().getId(name), share] as const);

  constructor(private readonly gl: WebGLRenderingContext) {
    super();
  }

  async load(modelJsonPath: string): Promise<void> {
    const signal = this.loading.signal;
    const directory = modelJsonPath.slice(0, modelJsonPath.lastIndexOf("/") + 1);
    const json = await this.fetchBuffer(modelJsonPath);
    signal.throwIfAborted();
    this.settings = new CubismModelSettingJson(json, json.byteLength);

    const mocFile = this.settings.getModelFileName();
    if (!mocFile) throw new Error("No Moc file configured in model3.json");
    const moc = await this.fetchBuffer(directory + mocFile);
    signal.throwIfAborted();
    this.loadModel(moc);
    if (!this.getModel()) throw new Error("Cubism could not create the model");
    // Seed the pose restored by the first animation frame.
    this.getModel().saveParameters();

    this.createRenderer(this.gl.canvas.width, this.gl.canvas.height);
    const renderer = this.getRenderer();
    renderer.startUp(this.gl);
    for (let index = 0; index < this.settings.getTextureCount(); index++) {
      const file = this.settings.getTextureFileName(index);
      if (!file) continue;
      const texture = await loadTexture(this.gl, directory + file, signal);
      if (signal.aborted) {
        this.gl.deleteTexture(texture);
        signal.throwIfAborted();
      }
      this.textures.push(texture);
      renderer.bindTexture(index, texture);
    }
    renderer.setIsPremultipliedAlpha(true);

    await this.motions.load(this.settings, directory, signal);
    signal.throwIfAborted();
    this.ready = true;
  }

  private async fetchBuffer(path: string): Promise<ArrayBuffer> {
    const response = await fetch(path, { signal: this.loading.signal });
    if (!response.ok) throw new Error(`Failed to load ${path}: ${response.status}`);
    return response.arrayBuffer();
  }

  playMotion(group: string): PlayMotionResult {
    return this.ready ? this.motions.playMotion(group) : "not-ready";
  }

  setSpeaking(speaking: boolean): void {
    this.speaking = speaking;
    this.motions.setSpeaking(speaking);

    if (!speaking) {
      this.lipSyncValue = 0;
      if (this.ready) this.getModel().setParameterValueById(this.mouthOpenId, 0);
    }
  }

  setLipSyncValue(value: number): void {
    this.lipSyncValue = Number.isFinite(value) ? Math.max(0, Math.min(1, value)) : 0;
  }

  setLooking(looking: boolean): void {
    this.looking = looking;
  }

  /** Blend the looking pose over the motion pose (not saved into it, like lip-sync). */
  private applyLook(deltaSeconds: number): void {
    const target = this.looking ? 1 : 0;
    const step = deltaSeconds / 0.35;            // ~0.35 s to ease in / out
    this.lookWeight = target > this.lookWeight
      ? Math.min(target, this.lookWeight + step)
      : Math.max(target, this.lookWeight - step);
    if (this.lookWeight <= 0) return;
    const model = this.getModel();
    for (const [id, share] of this.lookIds) {
      const index = model.getParameterIndex(id);
      if (index < 0) continue;
      const now = model.getParameterValueByIndex(index);
      const goal = share < 0
        ? now + (model.getParameterMinimumValue(index) - now) * -share
        : now + (model.getParameterMaximumValue(index) - now) * share;
      model.setParameterValueByIndex(index, now + (goal - now) * this.lookWeight);
    }
  }

  update(deltaSeconds: number): void {
    if (!this.ready) return;
    const model = this.getModel();
    model.loadParameters();
    this.motions.update(model, deltaSeconds);
    // Carry the blended motion pose into the next frame. Saving before
    // lip-sync keeps audio-driven mouth values out of the motion baseline.
    model.saveParameters();
    this.applyLook(deltaSeconds);
    if (this.speaking) {
      model.setParameterValueById(this.mouthOpenId, this.lipSyncValue);
    }
    model.update();
  }

  draw(matrix: CubismMatrix44, viewport: number[]): void {
    if (!this.ready) return;
    const finalMatrix = new CubismMatrix44();
    finalMatrix.setMatrix(matrix.getArray());
    finalMatrix.multiplyByMatrix(this.getModelMatrix());
    const renderer = this.getRenderer();
    renderer.setMvpMatrix(finalMatrix);
    // WebGL uses null for the canvas framebuffer; the SDK declaration omits it.
    renderer.setRenderState(null!, viewport);
    renderer.drawModel("/cubism-shaders/WebGL/");
  }

  override release(): void {
    if (this.released) return;
    this.released = true;
    this.ready = false;
    this.speaking = false;
    this.lipSyncValue = 0;
    this.loading.abort();
    this.motions.release();
    super.release();
    for (const texture of this.textures) this.gl.deleteTexture(texture);
    this.textures.length = 0;
    this.settings?.release();
    this.settings = null;
  }
}
