import { ChatInterface } from "@/components/ChatInterface";

export default function Home() {
  return (
    <main className="flex min-h-[calc(100dvh-3.5rem)] flex-1 flex-col">
      <ChatInterface />
    </main>
  );
}
