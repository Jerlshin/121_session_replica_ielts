import { Inter } from "next/font/google";
import type { ReactNode } from "react";

import { ThemeProvider } from "@/components/theme/ThemeProvider";

import "./globals.css";

const inter = Inter({ subsets: ["latin"], variable: "--font-inter", display: "swap" });

export const metadata = {
  title: "IELTS Speaking Platform",
  description: "Automated IELTS Speaking examination and results platform.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" className={inter.variable} suppressHydrationWarning>
      <body className="min-h-screen bg-page font-sans text-ink antialiased">
        <ThemeProvider>{children}</ThemeProvider>
      </body>
    </html>
  );
}
