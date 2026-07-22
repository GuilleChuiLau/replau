import assert from "node:assert/strict";
import { test } from "node:test";
import {
  DEFAULT_MAX_MEDIA_BYTES,
  channelIdForAccount,
  digits,
  envValue,
  findMediaPath,
  isDirectWhatsAppConversation,
  mimeType,
  validateMediaPath,
} from "./router-core.ts";

test("extracts environment values without exposing unrelated entries", () => {
  assert.equal(envValue("A=1\nOPENCLAW_HOOK_TOKEN='secret value'\n", "OPENCLAW_HOOK_TOKEN"), "secret value");
  assert.equal(envValue("A=1\n", "OPENCLAW_HOOK_TOKEN"), "");
});

test("normalizes WhatsApp senders", () => {
  assert.equal(digits("+51 973-875-456@s.whatsapp.net"), "51973875456");
});

test("creates stable, isolated channel ids for multiple WhatsApp accounts", () => {
  assert.equal(channelIdForAccount(undefined), "replau-main");
  assert.equal(channelIdForAccount("Business Account #2"), "whatsapp-account:business-account-2");
});

test("accepts direct WhatsApp chats and rejects groups or threads", () => {
  assert.equal(isDirectWhatsAppConversation({ channel: "whatsapp", conversationId: "51999999999@s.whatsapp.net" }), true);
  assert.equal(isDirectWhatsAppConversation({ channel: "telegram", conversationId: "51999999999" }), false);
  assert.equal(isDirectWhatsAppConversation({ channel: "whatsapp", isGroup: true }), false);
  assert.equal(isDirectWhatsAppConversation({ channel: "whatsapp", isGroup: false }), true);
  assert.equal(isDirectWhatsAppConversation({ channel: "whatsapp", conversationId: "120363000000@g.us" }), false);
  assert.equal(isDirectWhatsAppConversation({ channel: "whatsapp", parentConversationId: "parent" }), false);
  assert.equal(isDirectWhatsAppConversation({ channel: "whatsapp", threadId: "thread" }), false);
});

test("only extracts normalized paths below the inbound root", () => {
  assert.equal(findMediaPath("/tmp/file.jpg"), "");
  assert.equal(findMediaPath("/home/guill/.openclaw/media/inbound/../private.txt"), "");
  assert.equal(findMediaPath("media /home/guill/.openclaw/media/inbound/proof.webp"), "/home/guill/.openclaw/media/inbound/proof.webp");
});

test("maps supported MIME types", () => {
  assert.equal(mimeType("proof.PNG"), "image/png");
  assert.equal(mimeType("proof.webp"), "image/webp");
  assert.equal(mimeType("proof.pdf"), "application/pdf");
  assert.equal(mimeType("proof.jpeg"), "image/jpeg");
});

test("rejects unsupported media before reading it", async () => {
  await assert.rejects(
    validateMediaPath("/home/guill/.openclaw/media/inbound/archive.zip", DEFAULT_MAX_MEDIA_BYTES),
    /unsupported media type/,
  );
});
