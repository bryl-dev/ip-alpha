import { Suspense } from "react";

import ChatPanel from "@/components/ChatPanel";

const SUGGESTIONS = [
  "Why is Kadokawa up today?",
  "Which company has the biggest gap between hype and price?",
  "How are fans reacting to Monster Hunter Wilds?",
  "任天堂の最近の話題は？",
];

export default function ChatPage({ searchParams }: PageProps<"/chat">) {
  return (
    <main className="mx-auto w-full max-w-3xl px-6 py-10">
      <h1 className="text-2xl font-semibold">Ask the analyst</h1>
      <p className="mt-1 mb-8 text-sm text-neutral-400">
        Answers come from a local language model that reads the stored prices, Hype Scores, and
        fan posts. Numbered links point to the posts it used; citations that don&apos;t match a
        retrieved post are struck through.
      </p>
      <Suspense fallback={<p className="text-neutral-500">Loading…</p>}>
        <ChatWithQuery searchParams={searchParams} />
      </Suspense>
    </main>
  );
}

async function ChatWithQuery({ searchParams }: Pick<PageProps<"/chat">, "searchParams">) {
  const { q } = await searchParams;
  return <ChatPanel initialQuestion={typeof q === "string" ? q : ""} suggestions={SUGGESTIONS} />;
}
