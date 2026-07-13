import Link from "next/link";
import Image from "next/image";
import { FORUM_URL } from "@/lib/config";
import { ThemeToggle } from "@/components/ThemeToggle";

export function AssistantNavbar() {
  return (
    <header className="sticky top-0 z-40 border-b border-neutral-200 bg-white/80 backdrop-blur-md dark:border-neutral-800 dark:bg-neutral-950/80">
      <div className="mx-auto flex h-14 w-full max-w-5xl items-center gap-3 px-4 sm:px-6">
        <Link href="/" className="flex shrink-0 items-center gap-2" aria-label="Tercih Asistanı ana sayfa">
          {/* Koyu temada lacivert+siyah wordmark görünmez; beyaza çevrilir. */}
          <Image
            src="/logo.png"
            alt="TercihNoktam"
            width={161}
            height={24}
            priority
            className="h-6 w-auto dark:brightness-0 dark:invert"
            style={{ height: "1.5rem", width: "auto" }}
          />
          <span className="hidden items-center gap-1.5 rounded-full bg-gradient-to-r from-blue-600 to-indigo-600 px-2.5 py-0.5 text-[11px] font-semibold text-white sm:inline-flex">
            <svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor" aria-hidden>
              <path d="M12 2 9.9 8.6 3 9.2l5.2 4.4L6.6 21 12 17.3 17.4 21l-1.6-7.4L21 9.2l-6.9-.6L12 2Z" />
            </svg>
            Tercih Asistanı
          </span>
        </Link>

        <div className="flex-1" />

        <nav className="flex shrink-0 items-center gap-1.5">
          <a
            href={FORUM_URL}
            className="flex items-center gap-1.5 rounded-xl border border-neutral-200 px-3.5 py-1.5 text-sm font-medium text-neutral-700 transition-colors hover:border-blue-400 hover:text-blue-600 dark:border-neutral-700 dark:text-neutral-200 dark:hover:border-blue-500 dark:hover:text-blue-400"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
              <path d="M19 12H5M12 19l-7-7 7-7" />
            </svg>
            <span className="hidden sm:inline">Foruma dön</span>
            <span className="sm:hidden">Forum</span>
          </a>
          <ThemeToggle />
        </nav>
      </div>
    </header>
  );
}
