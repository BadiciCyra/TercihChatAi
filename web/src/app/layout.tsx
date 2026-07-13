import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";
import { AssistantNavbar } from "@/components/AssistantNavbar";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin", "latin-ext"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin", "latin-ext"],
});

export const metadata: Metadata = {
  title: {
    default: "Tercih Asistanı · TercihNoktam",
    template: "%s · Tercih Asistanı",
  },
  description:
    "YÖK Atlas verisiyle çalışan yapay zekâ tercih danışmanı. Sıralamana ve ilgi alanlarına göre sana en uygun üniversite ve bölümleri keşfet.",
};

// Temayı ilk boyamadan önce ayarla (yanlış tema parlamasını önler).
const themeInitScript = `(function(){try{var t=localStorage.getItem("theme");var d=t==="dark"||(!t&&window.matchMedia("(prefers-color-scheme: dark)").matches);if(d)document.documentElement.classList.add("dark");}catch(e){}})();`;

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="tr"
      suppressHydrationWarning
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="flex min-h-full flex-col bg-neutral-50 text-neutral-900 dark:bg-neutral-950 dark:text-neutral-100">
        <script dangerouslySetInnerHTML={{ __html: themeInitScript }} />
        <div className="aurora" aria-hidden />
        <AssistantNavbar />
        {children}
      </body>
    </html>
  );
}
