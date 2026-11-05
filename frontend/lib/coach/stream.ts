"use client";

import { ApiError, SessionExpiredError } from "@/lib/api/client";
import { getAccessToken, refreshAccessToken } from "@/lib/api/session";

export type Proposal = {
  id: string;
  kind: string;
  payload: Record<string, unknown>;
  reason: string | null;
};

export type ChatEvent =
  | { type: "meta"; conversation_id: string; red_flag?: string }
  | { type: "delta"; text: string }
  | { type: "tool"; name: string }
  | { type: "proposal"; proposal: Proposal }
  | { type: "done"; message_id: string | null; red_flag?: string }
  | { type: "error"; code: string; message: string };

export type ChatRequest = { text: string; conversation_id?: string; file_id?: string };

function post(body: ChatRequest, token: string | null, signal?: AbortSignal) {
  return fetch("/api/backend/coach/chat", {
    method: "POST",
    headers: {
      accept: "text/event-stream",
      "content-type": "application/json",
      ...(token ? { authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify(body),
    signal,
    cache: "no-store",
  });
}

function parse(block: string): ChatEvent | null {
  let event = "";
  let data = "";
  for (const line of block.split("\n")) {
    if (line.startsWith("event: ")) event = line.slice(7);
    else if (line.startsWith("data: ")) data += line.slice(6);
  }
  if (!event || !data) return null;
  const payload = JSON.parse(data) as Record<string, unknown>;
  if (event === "proposal") return { type: "proposal", proposal: payload as Proposal };
  return { type: event, ...payload } as ChatEvent;
}

/**
 * Send a message and read the coach's reply as it streams (server-sent events over
 * fetch, so the in-memory access token can be sent; EventSource cannot send it).
 */
export async function streamChat(
  body: ChatRequest,
  onEvent: (event: ChatEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  let res = await post(body, getAccessToken() ?? (await refreshAccessToken()), signal);
  if (res.status === 401 && (res.headers.get("www-authenticate") ?? "").startsWith("Bearer")) {
    const token = await refreshAccessToken();
    if (!token) throw new SessionExpiredError();
    res = await post(body, token, signal);
  }
  if (!res.ok || !res.body) {
    const json: unknown = await res.json().catch(() => null);
    const detail = json && typeof json === "object" && "detail" in json ? json.detail : json;
    throw new ApiError(res.status, detail);
  }
  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += value;
    let end = buffer.indexOf("\n\n");
    while (end !== -1) {
      const event = parse(buffer.slice(0, end));
      buffer = buffer.slice(end + 2);
      if (event) onEvent(event);
      end = buffer.indexOf("\n\n");
    }
  }
}
