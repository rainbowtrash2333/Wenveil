export type Page = "process" | "restore" | "settings";
export type StepKey = "ocr" | "organize" | "mask" | "audit";

export type SelectedFile = {
  id: string;
  name: string;
  path?: string;
  size: number;
  extension: string;
  source?: File;
};

export type StepState = Record<StepKey, boolean>;

export const STEP_LABELS: Record<StepKey, string> = {
  ocr: "文字识别",
  organize: "文本整理",
  mask: "文档脱敏",
  audit: "脱敏检查",
};

export const TEXT_EXTENSIONS = new Set([".md", ".markdown", ".txt", ".rtf"]);

export const DEFAULT_STEPS: StepState = {
  ocr: true,
  organize: true,
  mask: true,
  audit: true,
};

export const ENTITY_OPTIONS = [
  ["PERSON", "人名"],
  ["ORG", "机构名称"],
  ["ID_CARD", "身份证"],
  ["PHONE", "手机号"],
  ["BANK_ACCOUNT", "银行账号"],
  ["CUSTOM", "自定义字段"],
  ["AMOUNT", "金额"],
  ["NUMBER", "普通数字"],
] as const;

export function extensionOf(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot >= 0 ? name.slice(dot).toLowerCase() : "";
}

export function defaultStepsFor(files: SelectedFile[]): StepState {
  if (files.length > 0 && files.every((file) => TEXT_EXTENSIONS.has(file.extension))) {
    return { ...DEFAULT_STEPS, ocr: false };
  }
  return { ...DEFAULT_STEPS };
}

export function fileKey(file: Pick<SelectedFile, "name" | "path" | "size">): string {
  return `${file.path ?? file.name}\u0000${file.size}`;
}

export function mergeFiles(current: SelectedFile[], incoming: SelectedFile[]): SelectedFile[] {
  const seen = new Set(current.map(fileKey));
  return [...current, ...incoming.filter((file) => {
    const key = fileKey(file);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  })];
}

export function validatePassword(password: string, confirmation: string): string | null {
  if (!password && !confirmation) return null;
  if (!password || !confirmation) return "密码可不设置；如需恢复，请同时填写密码和确认密码。";
  if (password.length < 8) return "密码至少需要 8 个字符。";
  if (password !== confirmation) return "两次输入的密码不一致。";
  return null;
}

export function formatBytes(bytes: number): string {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${(bytes / 1024 ** index).toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
}
