"use client";

import { type ReactNode, useRef, useState } from "react";

import type { ChatSource } from "@/lib/api";

type Turn = {
  role: "user" | "assistant";
  content: string;
  status?: string;
  sources?: ChatSource[];
  cited?: number[];
  error?: string;
  done?: boolean;
};

type ChatEvent =
  | { type: "status"; text: string }
  | { type: "sources"; sources: ChatSource[] }
  | { type: "token"; text: string }
  | { type: "citations"; valid: number[]; invalid: number[] }
  | { type: "error"; text: string }
  | { type: "done" };

const HISTORY_TURNS = 6;
const CITATION = /\[(\d+(?:\s*[,\-–]\s*\d+)*)\]/g;

async function* readEvents(body: ReadableStream<Uint8Array>): AsyncGenerator<ChatEvent> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) return;
    buffer += decoder.decode(value, { stream: true });
    let end;
    while ((end = buffer.indexOf("\n\n")) !== -1) {
      const frame = buffer.slice(0, end);
      buffer = buffer.slice(end + 2);
      const data = frame
        .split("\n")
        .filter((line) => line.startsWith("data: "))
        .map((line) => line.slice(6))
        .join("\n");
      if (data) yield JSON.parse(data) as ChatEvent;
    }
  }
}

export default function ChatPanel({
  initialQuestion = "",
  suggestions = [],
}: {
  initialQuestion?: string;
  suggestions?: string[];
}) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState(initialQuestion);
  const [busy, setBusy] = useState(false);
  const abort = useRef<AbortController | null>(null);

  const updateLast = (patch: (t: Turn) => Partial<Turn>) =>
    setTurns((all) => [...all.slice(0, -1), { ...all[all.length - 1], ...patch(all[all.length - 1]) }]);

  async function ask(question: string) {
    question = question.trim();
    if (!question || busy) return;
    const history = turns
      .filter((t) => !t.error)
      .slice(-HISTORY_TURNS)
      .map(({ role, content }) => ({ role, content }));
    setTurns((all) => [
      ...all,
      { role: "user", content: question },
      { role: "assistant", content: "", status: "Thinking…" },
    ]);
    setInput("");
    setBusy(true);
    abort.current = new AbortController();

    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: question, history }),
        signal: abort.current.signal,
      });
      if (!res.ok || !res.body) throw new Error(`Request failed (${res.status})`);
      for await (const event of readEvents(res.body)) {
        if (event.type === "status") updateLast(() => ({ status: event.text }));
        else if (event.type === "sources") updateLast(() => ({ sources: event.sources }));
        else if (event.type === "token") updateLast((t) => ({ content: t.content + event.text }));
        else if (event.type === "citations") updateLast(() => ({ cited: event.valid }));
        else if (event.type === "error") updateLast(() => ({ error: event.text }));
        else if (event.type === "done") updateLast(() => ({ done: true }));
      }
    } catch (err) {
      if ((err as Error).name !== "AbortError") {
        updateLast(() => ({ error: (err as Error).message }));
      }
    } finally {
      updateLast(() => ({ done: true }));
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-6">
      {turns.length === 0 && suggestions.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {suggestions.map((s) => (
            <button
              key={s}
              onClick={() => ask(s)}
              className="rounded-full border border-neutral-700 px-3 py-1 text-sm text-neutral-300 hover:border-neutral-500"
            >
              {s}
            </button>
          ))}
        </div>
      )}

      {turns.map((turn, i) =>
        turn.role === "user" ? (
          <p key={i} className="self-end rounded-lg bg-neutral-800 px-4 py-2 text-sm">
            {turn.content}
          </p>
        ) : (
          <AssistantTurn key={i} turn={turn} />
        ),
      )}

      <form
        onSubmit={(e) => {
          e.preventDefault();
          ask(input);
        }}
        className="flex gap-2"
      >
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Why is Kadokawa up today?"
          className="flex-1 rounded-lg border border-neutral-700 bg-neutral-900 px-4 py-2 text-sm outline-none focus:border-neutral-500"
        />
        {busy ? (
          <button
            type="button"
            onClick={() => abort.current?.abort()}
            className="rounded-lg border border-neutral-700 px-4 py-2 text-sm"
          >
            Stop
          </button>
        ) : (
          <button
            type="submit"
            disabled={!input.trim()}
            className="rounded-lg bg-neutral-100 px-4 py-2 text-sm font-medium text-neutral-900 disabled:opacity-40"
          >
            Ask
          </button>
        )}
      </form>
    </div>
  );
}

function AssistantTurn({ turn }: { turn: Turn }) {
  const byId = new Map((turn.sources ?? []).map((s) => [s.id, s]));
  const cited = (turn.cited ?? []).map((id) => byId.get(id)).filter((s) => s !== undefined);

  return (
    <div className="rounded-lg border border-neutral-800 p-4">
      {!turn.done && turn.status && (
        <p className="mb-2 text-xs text-neutral-500">
          {turn.status}
          {turn.sources ? ` · ${turn.sources.length} posts retrieved` : ""}
        </p>
      )}
      {turn.content && (
        <p className="whitespace-pre-wrap text-sm leading-relaxed">
          <WithCitations text={turn.content} sources={byId} verified={turn.cited} />
        </p>
      )}
      {turn.error && <p className="text-sm text-rose-400">{turn.error}</p>}
      {cited.length > 0 && (
        <ol className="mt-4 space-y-2 border-t border-neutral-800 pt-3 text-xs text-neutral-400">
          {cited.map((s) => (
            <li key={s.id} id={`source-${s.id}`}>
              <span className="font-mono text-neutral-500">[{s.id}]</span>{" "}
              {s.url ? (
                <a href={s.url} target="_blank" rel="noreferrer" className="hover:underline">
                  {s.source}
                </a>
              ) : (
                s.source
              )}
              {" · "}
              {s.published_at.slice(0, 10)}
              {s.ip_name && ` · ${s.ip_name}`}
              <span className="mt-0.5 block text-neutral-500">{s.summary ?? s.text}</span>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

function WithCitations({
  text,
  sources,
  verified,
}: {
  text: string;
  sources: Map<number, ChatSource>;
  verified?: number[];
}) {
  const parts: ReactNode[] = [];
  let last = 0;
  for (const match of text.matchAll(CITATION)) {
    parts.push(text.slice(last, match.index));
    const ids = match[1].split(/\s*[,\-–]\s*/).map(Number);
    parts.push(
      <span key={match.index} className="whitespace-nowrap">
        {ids.map((id) => {
          const ok = sources.has(id) && (verified === undefined || verified.includes(id));
          return ok ? (
            <a
              key={id}
              href={`#source-${id}`}
              className="mx-0.5 rounded bg-sky-950 px-1 font-mono text-xs text-sky-300 hover:bg-sky-900"
            >
              {id}
            </a>
          ) : (
            <span
              key={id}
              title="Not a retrieved source"
              className="mx-0.5 font-mono text-xs text-neutral-600 line-through"
            >
              {id}
            </span>
          );
        })}
      </span>,
    );
    last = match.index + match[0].length;
  }
  parts.push(text.slice(last));
  return <>{parts}</>;
}
