import { readFile } from "node:fs/promises";
import { basename } from "node:path";
import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";
import {
  DEFAULT_MAX_MEDIA_BYTES,
  digits,
  envValue,
  findMediaPath,
  isDirectWhatsAppConversation,
  mimeType,
  validateMediaPath,
} from "./router-core.ts";

const DEFAULT_BRIDGE_URL = "http://127.0.0.1:8789/webhook/whatsapp";
const DEFAULT_ENV_FILE = "/home/guill/.config/replau/bridge.env";
type InboundData = {
  customer: string;
  content: string;
  accountId?: string;
  messageId?: string;
  senderName?: string;
  metadata?: Record<string, unknown>;
};

async function routeToBridge(api: any, data: InboundData): Promise<string> {
  const config = (api.pluginConfig ?? {}) as Record<string, unknown>;
  const bridgeUrl = String(config.bridgeUrl || DEFAULT_BRIDGE_URL);
  const envFile = String(config.envFile || DEFAULT_ENV_FILE);
  const timeoutMs = Number(config.timeoutMs || 15000);
  const maxMediaBytes = Number(config.maxMediaBytes || DEFAULT_MAX_MEDIA_BYTES);
  const mediaPath = await validateMediaPath(
    findMediaPath({ content: data.content, metadata: data.metadata }),
    maxMediaBytes,
  );
  const envText = await readFile(envFile, "utf8");
  const hookToken = envValue(envText, "OPENCLAW_HOOK_TOKEN");
  if (!hookToken) throw new Error("OPENCLAW_HOOK_TOKEN is missing");

  const payload: Record<string, unknown> = {
    whatsapp_number: data.customer,
    customer_address: data.customer,
    channel_kind: "whatsapp",
    channel_id: "replau-main",
    account_id: data.accountId || null,
    message_type: mediaPath ? (mediaPath.toLowerCase().endsWith(".pdf") ? "document" : "image") : "text",
    message_text: data.content,
    raw_payload: { message_id: data.messageId, sender_name: data.senderName, metadata: data.metadata },
  };
  if (mediaPath) {
    payload.media_base64 = (await readFile(mediaPath)).toString("base64");
    payload.media_filename = basename(mediaPath);
    payload.media_mime_type = mimeType(mediaPath);
  }
  const response = await fetch(bridgeUrl, {
    method: "POST",
    headers: { "content-type": "application/json", "x-hook-token": hookToken },
    body: JSON.stringify(payload),
    signal: AbortSignal.timeout(timeoutMs),
  });
  if (!response.ok) throw new Error(`bridge returned HTTP ${response.status}`);
  const result = (await response.json()) as Record<string, unknown>;
  const replyText = String(result.reply_text || "").trim();
  if (!replyText) throw new Error("bridge returned no reply_text");
  return replyText;
}

export default definePluginEntry({
  id: "replau-whatsapp-inbound",
  name: "Replau WhatsApp Inbound Router",
  description: "Routes WhatsApp customer messages through the Replau ordering bridge.",
  register(api) {
    api.on("inbound_claim", async (event, ctx) => {
      if (!isDirectWhatsAppConversation({
        channel: event.channel,
        // OpenClaw 2026.7.1 includes this runtime field even though the
        // published inbound-claim type has not caught up yet.
        isGroup: event.isGroup,
        conversationId: event.conversationId || ctx.conversationId,
        parentConversationId: event.parentConversationId || ctx.parentConversationId,
        threadId: event.threadId,
      })) return;

      const customer = digits(event.senderId || event.conversationId);
      if (customer.length < 8) {
        api.logger.warn("Replau inbound router could not normalize the WhatsApp sender");
        return { handled: true, reply: { text: "No pude identificar este chat. Por favor intenta nuevamente." } };
      }

      try {
        const content = String(event.body || event.content || "").trim();
        const replyText = await routeToBridge(api, {
          customer,
          content,
          accountId: event.accountId,
          messageId: event.messageId,
          senderName: event.senderName,
          metadata: event.metadata,
        });
        return { handled: true, reply: { text: replyText } };
      } catch (error) {
        api.logger.error(`Replau inbound router failed: ${error instanceof Error ? error.message : String(error)}`);
        return {
          handled: true,
          reply: { text: "Tuvimos un problema temporal al procesar tu pedido. Por favor intenta nuevamente en un momento." },
        };
      }
    }, { priority: 1000, timeoutMs: 30000 });

    api.on("before_dispatch", async (event, ctx) => {
      if (!isDirectWhatsAppConversation({
        channel: event.channel || ctx.channelId,
        isGroup: event.isGroup,
        conversationId: ctx.conversationId,
      })) return;
      const customer = digits(event.senderId || ctx.senderId || ctx.conversationId);
      if (customer.length < 8) {
        return { handled: true, text: "No pude identificar este chat. Por favor intenta nuevamente." };
      }
      try {
        const text = await routeToBridge(api, {
          customer,
          content: String(event.body || event.content || "").trim(),
          accountId: ctx.accountId,
        });
        api.logger.info(`Replau routed WhatsApp inbound for session ${ctx.sessionKey || customer}`);
        return { handled: true, text };
      } catch (error) {
        api.logger.error(`Replau before-dispatch routing failed: ${error instanceof Error ? error.message : String(error)}`);
        return {
          handled: true,
          text: "Tuvimos un problema temporal al procesar tu pedido. Por favor intenta nuevamente en un momento.",
        };
      }
    }, { priority: 1000, timeoutMs: 30000 });
  },
});
