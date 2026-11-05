import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import Link from "next/link";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "IP-Alpha",
  description: "Fandom hype vs. stock price for Japanese entertainment companies",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} dark h-full antialiased`}
    >
      <body className="flex min-h-full flex-col">
        <nav className="border-b border-neutral-800">
          <div className="mx-auto flex max-w-6xl items-center gap-6 px-6 py-3">
            <Link href="/" className="font-semibold tracking-tight">
              IP-Alpha
            </Link>
            <span className="text-sm text-neutral-500">Fandom hype vs. equity</span>
            <Link href="/chat" className="ml-auto text-sm text-neutral-300 hover:underline">
              Ask the analyst
            </Link>
          </div>
        </nav>
        <div className="flex-1">{children}</div>
        <footer className="border-t border-neutral-800 px-6 py-4 text-center text-xs text-neutral-500">
          Research project. Not investment advice.
        </footer>
      </body>
    </html>
  );
}
