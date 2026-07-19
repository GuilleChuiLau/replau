import { realpath, stat } from "node:fs/promises";
import { extname, resolve } from "node:path";

export const MEDIA_ROOT = "/home/guill/.openclaw/media/inbound/";
export const DEFAULT_MAX_MEDIA_BYTES = 8 * 1024 * 1024;

const ALLOWED_MEDIA_EXTENSIONS = new Set([".jpg", ".jpeg", ".png", ".webp", ".pdf"]);

export function envValue(text: string, key: string): string {
  const line = text.split(/\r?\n/).find((candidate) => candidate.trim().startsWith(`${key}=`));
  if (!line) return "";
  return line.slice(line.indexOf("=") + 1).trim().replace(/^(['"])(.*)\1$/, "$2");
}

export function digits(value: unknown): string {
  return String(value ?? "").replace(/\D/g, "");
}

export function isDirectWhatsAppConversation(params: {
  channel?: unknown;
  isGroup?: unknown;
  conversationId?: unknown;
  parentConversationId?: unknown;
  threadId?: unknown;
}): boolean {
  if (String(params.channel ?? "").toLowerCase() !== "whatsapp") return false;
  if (params.isGroup === true || params.parentConversationId || params.threadId !== undefined) return false;
  const conversationId = String(params.conversationId ?? "").toLowerCase();
  if (!conversationId) return true;
  return !conversationId.endsWith("@g.us") && !conversationId.startsWith("group:");
}

export function findMediaPath(value: unknown): string {
  const serialized = typeof value === "string" ? value : JSON.stringify(value ?? {});
  const match = serialized.match(/\/home\/guill\/\.openclaw\/media\/inbound\/[A-Za-z0-9._%+\/-]+/);
  if (!match) return "";
  const candidate = resolve(match[0]);
  return candidate.startsWith(MEDIA_ROOT) ? candidate : "";
}

export async function validateMediaPath(path: string, maxBytes: number): Promise<string> {
  if (!path) return "";
  if (!Number.isSafeInteger(maxBytes) || maxBytes < 1) throw new Error("invalid media size limit");
  const extension = extname(path).toLowerCase();
  if (!ALLOWED_MEDIA_EXTENSIONS.has(extension)) throw new Error("unsupported media type");
  const canonicalRoot = await realpath(MEDIA_ROOT);
  const canonicalPath = await realpath(path);
  if (!canonicalPath.startsWith(`${canonicalRoot}/`)) throw new Error("media path escapes inbound directory");
  const details = await stat(canonicalPath);
  if (!details.isFile()) throw new Error("media path is not a file");
  if (details.size > maxBytes) throw new Error(`media exceeds ${maxBytes} byte limit`);
  return canonicalPath;
}

export function mimeType(path: string): string {
  const lower = path.toLowerCase();
  if (lower.endsWith(".png")) return "image/png";
  if (lower.endsWith(".webp")) return "image/webp";
  if (lower.endsWith(".pdf")) return "application/pdf";
  return "image/jpeg";
}
