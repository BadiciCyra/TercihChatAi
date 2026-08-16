import { ChatInterface } from "@/components/ChatInterface";

export default function Home() {
  return (
    // Sabit yükseklik: scroll sayfada değil, mesaj listesinin içinde kalsın.
    <main className="flex h-[calc(100dvh-3.5rem)] flex-col">
      <ChatInterface />
    </main>
  );
}
