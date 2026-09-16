import { invoke } from "@tauri-apps/api/core";
import type { SelectedFile, StepKey } from "./domain";

export type ProgressEvent = {
  fileId: string;
  fileName?: string;
  step?: StepKey;
  status: "queued" | "running" | "done" | "attention" | "error" | "cancelled";
  progress: number;
  message?: string;
};

export type ProcessRequest = {
  files: SelectedFile[];
  steps: Record<StepKey, boolean>;
  password?: string;
  outputDir: string;
  entities: string[];
  aiEnhanced: boolean;
  ocrMode: "auto" | "fast" | "enhanced";
  device: "auto" | "cpu" | "gpu";
  keepIntermediate?: boolean;
};

export type ProcessResult = {
  files: Array<{
    fileId: string;
    status: "done" | "attention" | "error";
    outputNames: string[];
    auditIssues: Array<{ line: number; category: string; summary: string }>;
    reversible: boolean;
    message?: string;
  }>;
};

export type RestoreRequest = {
  maskedPath: string;
  mappingPath: string;
  password: string;
  outputDir: string;
  maskedSource?: File;
  mappingSource?: File;
};

export type RestoreResult = {
  outputName: string;
};

export interface WenveilBridge {
  process(
    request: ProcessRequest,
    onProgress: (event: ProgressEvent) => void,
    signal?: AbortSignal,
  ): Promise<ProcessResult>;
  restore(request: RestoreRequest, signal?: AbortSignal): Promise<RestoreResult>;
}

type NativeBridge = WenveilBridge & { kind?: "native" };

declare global {
  interface Window {
    __WENVEIL_BRIDGE__?: NativeBridge;
    __TAURI_INTERNALS__?: unknown;
  }
}

function wait(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const timer = window.setTimeout(resolve, ms);
    signal?.addEventListener("abort", () => {
      window.clearTimeout(timer);
      reject(new DOMException("操作已取消", "AbortError"));
    }, { once: true });
  });
}

class DemoAdapter implements WenveilBridge {
  async process(
    request: ProcessRequest,
    onProgress: (event: ProgressEvent) => void,
    signal?: AbortSignal,
  ): Promise<ProcessResult> {
    const results: ProcessResult["files"] = [];
    for (const file of request.files) {
      onProgress({ fileId: file.id, fileName: file.name, status: "queued", progress: 0 });
    }
    for (const file of request.files) {
      const enabledSteps = (Object.keys(request.steps) as StepKey[]).filter((key) => request.steps[key]);
      for (const [index, step] of enabledSteps.entries()) {
        onProgress({ fileId: file.id, fileName: file.name, step, status: "running", progress: 0 });
        await wait(450, signal);
        onProgress({ fileId: file.id, fileName: file.name, step, status: "done", progress: 1 });
        if (index === enabledSteps.length - 1) continue;
      }
      results.push({ fileId: file.id, status: "done", outputNames: [`document-${file.id}.masked.md`], auditIssues: [], reversible: Boolean(request.password) });
      onProgress({ fileId: file.id, fileName: file.name, status: "done", progress: 1 });
    }
    return { files: results };
  }

  async restore(_request: RestoreRequest, signal?: AbortSignal): Promise<RestoreResult> {
    await wait(650, signal);
    return { outputName: "document-restored.md" };
  }
}

export function getBridge(): { bridge: WenveilBridge | null; mode: "native" | "demo" | "unavailable" } {
  if (window.__WENVEIL_BRIDGE__) return { bridge: window.__WENVEIL_BRIDGE__, mode: "native" };
  if (window.__TAURI_INTERNALS__) return { bridge: new TauriBridge(), mode: "native" };
  if (import.meta.env.VITE_HTTP_ADAPTER === "1") return { bridge: new HttpDevAdapter(), mode: "native" };
  if (import.meta.env.VITE_DEMO_ADAPTER === "1") return { bridge: new DemoAdapter(), mode: "demo" };
  return { bridge: null, mode: "unavailable" };
}

export async function pickDirectory(): Promise<string | null> {
  if (!window.__TAURI_INTERNALS__) return null;
  const { open } = await import("@tauri-apps/plugin-dialog");
  const selected = await open({ directory: true, multiple: false });
  return typeof selected === "string" ? selected : null;
}

export async function openDirectory(path: string): Promise<boolean> {
  if (!window.__TAURI_INTERNALS__) return false;
  await invoke("open_directory", { path });
  return true;
}

export async function openFile(path: string): Promise<boolean> {
  if (!window.__TAURI_INTERNALS__) return false;
  await invoke("open_file", { path });
  return true;
}

class TauriBridge implements WenveilBridge {
  async process(
    request: ProcessRequest,
    onProgress: (event: ProgressEvent) => void,
    signal?: AbortSignal,
  ): Promise<ProcessResult> {
    const files = await Promise.all(request.files.map(serializableFile));
    return this.request({ op: "process", ...request, files } as unknown as ProcessRequest, onProgress, signal) as Promise<ProcessResult>;
  }

  async restore(request: RestoreRequest, signal?: AbortSignal): Promise<RestoreResult> {
    return this.request({ op: "restore", ...(await serializableRestore(request)) } as unknown as RestoreRequest, undefined, signal) as Promise<RestoreResult>;
  }

  private async request(
    payload: ProcessRequest | RestoreRequest,
    onProgress?: (event: ProgressEvent) => void,
    signal?: AbortSignal,
  ): Promise<ProcessResult | RestoreResult> {
    if (signal?.aborted) throw new DOMException("操作已取消", "AbortError");
    const cancel = () => {
      void invoke("cancel_sidecar_request").catch(() => undefined);
    };
    signal?.addEventListener("abort", cancel, { once: true });
    let output: string;
    try {
      output = await invoke<string>("sidecar_request", { request: JSON.stringify(payload) });
    } catch (error) {
      if (signal?.aborted) throw new DOMException("操作已取消", "AbortError");
      throw error;
    } finally {
      signal?.removeEventListener("abort", cancel);
    }
    if (signal?.aborted) throw new DOMException("操作已取消", "AbortError");
    let result: ProcessResult | RestoreResult | undefined;
    for (const line of output.split("\n")) {
      if (!line.trim()) continue;
      const event = JSON.parse(line) as { type: string; event?: ProgressEvent; result?: ProcessResult | RestoreResult; message?: string };
      if (event.type === "progress" && event.event && onProgress) onProgress(event.event);
      if (event.type === "result") result = event.result;
      if (event.type === "error") throw new Error(event.message ?? "本地处理失败。");
    }
    if (!result) throw new Error("Python sidecar 返回了不完整的结果。");
    return result;
  }
}

async function fileAsBase64(file: File): Promise<string> {
  const bytes = new Uint8Array(await file.arrayBuffer());
  let binary = "";
  const chunkSize = 0x8000;
  for (let index = 0; index < bytes.length; index += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(index, index + chunkSize));
  }
  return btoa(binary);
}

async function serializableFile(file: SelectedFile): Promise<Record<string, unknown>> {
  return {
    id: file.id,
    name: file.name,
    path: file.path,
    size: file.size,
    extension: file.extension,
    contentBase64: file.source ? await fileAsBase64(file.source) : undefined,
  };
}

async function serializableRestore(request: RestoreRequest): Promise<Record<string, unknown>> {
  return {
    maskedPath: request.maskedPath,
    mappingPath: request.mappingPath,
    password: request.password,
    outputDir: request.outputDir,
    maskedContentBase64: request.maskedSource ? await fileAsBase64(request.maskedSource) : undefined,
    mappingContentBase64: request.mappingSource ? await fileAsBase64(request.mappingSource) : undefined,
  };
}

class HttpDevAdapter implements WenveilBridge {
  async process(
    request: ProcessRequest,
    onProgress: (event: ProgressEvent) => void,
    signal?: AbortSignal,
  ): Promise<ProcessResult> {
    const files = await Promise.all(request.files.map(async (file) => ({
      id: file.id,
      name: file.name,
      size: file.size,
      extension: file.extension,
      contentBase64: file.source ? await fileAsBase64(file.source) : undefined,
      path: file.path,
    })));
    return this.request({ op: "process", ...request, files } as unknown as ProcessRequest, onProgress, signal) as Promise<ProcessResult>;
  }

  async restore(request: RestoreRequest, signal?: AbortSignal): Promise<RestoreResult> {
    return this.request({ op: "restore", ...(await serializableRestore(request)) } as unknown as RestoreRequest, undefined, signal) as Promise<RestoreResult>;
  }

  private async request(
    payload: ProcessRequest | RestoreRequest,
    onProgress?: (event: ProgressEvent) => void,
    signal?: AbortSignal,
  ): Promise<ProcessResult | RestoreResult> {
    const response = await fetch("http://127.0.0.1:8765/sidecar", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(payload),
      signal,
    });
    if (!response.ok || !response.body) throw new Error("本地 Python HTTP 桥接未响应，请确认服务已启动。");
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let result: ProcessResult | RestoreResult | undefined;
    while (true) {
      const chunk = await reader.read();
      buffer += decoder.decode(chunk.value ?? new Uint8Array(), { stream: !chunk.done });
      const lines = buffer.split("\n");
      buffer = lines.pop() ?? "";
      for (const line of lines) {
        if (!line.trim()) continue;
        const event = JSON.parse(line) as { type: string; event?: ProgressEvent; result?: ProcessResult | RestoreResult; message?: string };
        if (event.type === "progress" && event.event && onProgress) onProgress(event.event);
        if (event.type === "result") result = event.result;
        if (event.type === "error") throw new Error(event.message ?? "本地处理失败。");
      }
      if (chunk.done) break;
    }
    if (!result) throw new Error("本地 Python 桥接返回了不完整的结果。");
    return result;
  }
}
